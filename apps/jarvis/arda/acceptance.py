"""Lease-fenced, recoverable promotion of verified candidate commits."""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from arda.core import Database, EventLog, JobQueue, StaleLeaseError, _iso, _utc_now


class AcceptanceConflict(RuntimeError):
    """A candidate cannot safely advance the accepted ref."""


class AcceptanceCoordinator:
    """Core-owned acceptance with a durable intent and Git expected-old CAS.

    The intent is committed before promotion. Promotion then holds SQLite's
    writer lock while revalidating the lease, CAS-updating Git, and committing
    job/task state. A crash after the Git update rolls back SQLite state while
    preserving the prior intent for ``reconcile``.
    """

    def __init__(self, db: Database, *, clock: Callable[[], datetime] = _utc_now,
                 fault_hook: Callable[[str], None] | None = None, lease_seconds: int = 600):
        self.db = db
        self.clock = clock
        self.fault_hook = fault_hook
        self.lease_seconds = lease_seconds
        self.events = EventLog(db, clock)

    def prepare(
        self, *, job_id: str, lease_token: str, lease_generation: int,
        task_id: str, repo_path: str, ref_name: str, expected_old_sha: str,
        candidate_sha: str, evidence_sha256: str, result_payload: dict[str, Any] | None = None,
        crash_at: str | None = None,
    ) -> str:
        repo = Path(repo_path).expanduser().resolve()
        ref_name = self._normalize_ref(repo, ref_name)
        self._validate_sha(expected_old_sha, "expected_old_sha")
        self._validate_sha(candidate_sha, "candidate_sha")
        self._validate_sha(evidence_sha256, "evidence_sha256")
        subprocess.run(["git", "cat-file", "-e", f"{candidate_sha}^{{commit}}"], cwd=repo,
                       check=True, capture_output=True, text=True)
        now = _iso(self.clock())
        result = dict(result_payload or {})
        if result.get("result") != "PASS" or result.get("candidate_sha", candidate_sha) != candidate_sha:
            raise AcceptanceConflict("only a PASS result for this exact candidate can be accepted")
        if result.get("accepted_sha") is not None:
            raise AcceptanceConflict("executor cannot assert an accepted SHA")
        result["candidate_sha"] = candidate_sha
        result["accepted_sha"] = None
        with self.db.transaction():
            queue = JobQueue(self.db, clock=self.clock)
            job = queue._verify_lease(job_id, lease_token, ("running",))
            if job["attempt_count"] != lease_generation:
                raise StaleLeaseError(f"lease generation changed for {job_id}")
            task = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None or task["project_id"] != job["project_id"] or task["status"] != "in_progress":
                raise AcceptanceConflict("task is not the active job task")
            current_ref = self._ref_sha(repo, ref_name)
            evidence = self.db.execute(
                "SELECT sha256, content FROM artifacts WHERE job_id=? AND sha256=? AND verified=1",
                (job_id, evidence_sha256),
            ).fetchone()
            if (evidence is None or evidence["content"] is None
                    or hashlib.sha256(bytes(evidence["content"])).hexdigest() != evidence["sha256"]):
                raise AcceptanceConflict("verified immutable evidence artifact is missing")
            existing = self.db.execute(
                "SELECT * FROM acceptance_intents WHERE job_id=? AND lease_generation=?",
                (job_id, lease_generation),
            ).fetchone()
            if existing is not None:
                same = (existing["task_id"] == task_id and existing["repo_path"] == str(repo)
                        and existing["ref_name"] == ref_name
                        and existing["expected_old_sha"] == expected_old_sha
                        and existing["candidate_sha"] == candidate_sha
                        and existing["evidence_sha256"] == evidence_sha256)
                if not same:
                    raise AcceptanceConflict("lease generation already has a different acceptance intent")
                return existing["intent_id"]
            if crash_at == "before_intent_commit":
                raise RuntimeError("simulated crash before acceptance intent commit")
            if self.fault_hook:
                self.fault_hook("before_intent_commit")
            intent_id = f"accept_{uuid.uuid4().hex}"
            self.db.execute(
                """INSERT INTO acceptance_intents(
                    intent_id, job_id, task_id, lease_generation, repo_path, ref_name,
                    expected_old_sha, candidate_sha, evidence_sha256, result_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)""",
                (intent_id, job_id, task_id, lease_generation, str(repo), ref_name,
                 expected_old_sha, candidate_sha, evidence_sha256,
                 json.dumps(result, sort_keys=True, ensure_ascii=False), now, now),
            )
            self.events.append("acceptance.intent_prepared", project_id=job["project_id"],
                               source="acceptance-coordinator", source_id=intent_id,
                               payload={"job_id": job_id, "task_id": task_id,
                                        "lease_generation": lease_generation,
                                        "expected_old_sha": expected_old_sha,
                                        "candidate_sha": candidate_sha,
                                        "evidence_sha256": evidence_sha256})
            if current_ref != expected_old_sha:
                intent = self._intent(intent_id)
                self._mark_conflict(intent, job, "accepted ref no longer matches expected baseline")
            return intent_id

    def promote(self, intent_id: str, *, lease_token: str, crash_at: str | None = None) -> str:
        conflict: str | None = None
        accepted_sha: str | None = None
        with self.db.transaction():
            intent = self._intent(intent_id)
            if intent["status"] == "accepted":
                return intent["candidate_sha"]
            if intent["status"] == "conflict":
                raise AcceptanceConflict(intent["error"] or "acceptance intent is conflicted")
            if intent["status"] != "prepared":
                raise AcceptanceConflict(f"acceptance intent is {intent['status']}")
            job = JobQueue(self.db, clock=self.clock)._verify_lease(
                intent["job_id"], lease_token, ("running",)
            )
            if job["attempt_count"] != intent["lease_generation"]:
                raise StaleLeaseError(f"lease generation changed for {intent['job_id']}")
            if not self._evidence_valid(intent):
                conflict = "immutable verification evidence is missing or does not match its digest"
                self._mark_conflict(intent, job, conflict)
            else:
                repo = Path(intent["repo_path"])
                current = self._ref_sha(repo, intent["ref_name"])
                if current == intent["candidate_sha"]:
                    accepted_sha = intent["candidate_sha"]
                elif current != intent["expected_old_sha"]:
                    conflict = "accepted ref is neither expected baseline nor candidate"
                    self._mark_conflict(intent, job, conflict)
                else:
                    if crash_at == "before_git_cas":
                        raise RuntimeError("simulated crash before Git CAS")
                    if self.fault_hook:
                        self.fault_hook("before_git_cas")
                    # Renew inside the serialized transaction. A competing
                    # heartbeat/reclaim cannot supersede this generation while
                    # Git CAS and final SQLite writes are in flight.
                    renewed_at = self.clock()
                    self.db.execute(
                        "UPDATE jobs SET lease_expires_at=?, updated_at=? WHERE id=? AND lease_token=?",
                        (_iso(renewed_at + timedelta(seconds=self.lease_seconds)), _iso(renewed_at),
                         job["id"], lease_token),
                    )
                    JobQueue(self.db, clock=self.clock, lease_seconds=self.lease_seconds)._verify_lease(
                        intent["job_id"], lease_token, ("running",)
                    )
                    subprocess.run(
                        ["git", "-c", "core.hooksPath=/dev/null", "update-ref", "--no-deref",
                         "-m", f"arda accept {intent['task_id']}",
                         intent["ref_name"], intent["candidate_sha"], intent["expected_old_sha"]],
                        cwd=repo, check=True, capture_output=True, text=True, timeout=30,
                    )
                    if crash_at == "after_git_cas":
                        raise RuntimeError("simulated crash after Git CAS")
                    if self.fault_hook:
                        self.fault_hook("after_git_cas")
                    accepted_sha = intent["candidate_sha"]
            if accepted_sha:
                self._finalize(intent, job, recovered=False)
        if conflict:
            raise AcceptanceConflict(conflict)
        assert accepted_sha
        return accepted_sha

    def reconcile(self) -> int:
        rows = self.db.execute(
            "SELECT intent_id FROM acceptance_intents WHERE status='prepared' ORDER BY created_at, intent_id"
        ).fetchall()
        changed = 0
        for row in rows:
            with self.db.transaction():
                intent = self._intent(row["intent_id"])
                if intent["status"] != "prepared":
                    continue
                current = self._ref_sha(Path(intent["repo_path"]), intent["ref_name"])
                job = self.db.execute("SELECT * FROM jobs WHERE id=?", (intent["job_id"],)).fetchone()
                if job is None:
                    self._set_intent(intent["intent_id"], "conflict", "job record is missing")
                elif not self._evidence_valid(intent):
                    self._mark_conflict(intent, job, "immutable verification evidence is missing or does not match its digest")
                elif current == intent["candidate_sha"]:
                    self._finalize(intent, job, recovered=True)
                elif current == intent["expected_old_sha"]:
                    self._abort_and_requeue(intent, job)
                else:
                    self._mark_conflict(intent, job, "accepted ref is neither expected baseline nor candidate")
                changed += 1
        return changed

    def _finalize(self, intent, job, *, recovered: bool) -> None:
        now = _iso(self.clock())
        result = json.loads(intent["result_json"])
        result["accepted_sha"] = intent["candidate_sha"]
        self.db.execute(
            """UPDATE acceptance_intents SET status='accepted', error=NULL, updated_at=?
               WHERE intent_id=? AND status='prepared'""", (now, intent["intent_id"]),
        )
        self.db.execute(
            """UPDATE jobs SET status='succeeded', result=?, error=NULL, finished_at=?, updated_at=?,
                 leased_by=NULL, lease_token=NULL, lease_expires_at=NULL, version=version+1 WHERE id=?""",
            (json.dumps(result, sort_keys=True, ensure_ascii=False), now, now, job["id"]),
        )
        self.db.execute(
            """UPDATE tasks SET status='done', completed_at=?, updated_at=?, version=version+1 WHERE id=?""",
            (now, now, intent["task_id"]),
        )
        self.db.execute(
            "UPDATE organization_profiles SET accepted_baseline=?, updated_at=? WHERE project_id=?",
            (intent["candidate_sha"], now, job["project_id"]),
        )
        self.events.append("task.status_changed", project_id=job["project_id"],
                           source="acceptance-coordinator", source_id=intent["task_id"],
                           payload={"status": "done", "accepted_sha": intent["candidate_sha"]})
        self.events.append("job.completed", project_id=job["project_id"],
                           source="acceptance-coordinator", source_id=job["id"],
                           payload={"accepted_sha": intent["candidate_sha"]})
        self.events.append("acceptance.reconciled" if recovered else "acceptance.completed",
                           project_id=job["project_id"], source="acceptance-coordinator",
                           source_id=intent["intent_id"], payload={"accepted_sha": intent["candidate_sha"]})

    def _abort_and_requeue(self, intent, job) -> None:
        now = _iso(self.clock())
        # A later lease generation may already be active. Retire only this
        # intent; never reset the newer worker's task/job state.
        if job["attempt_count"] != intent["lease_generation"] or job["status"] not in {"leased", "running"}:
            self._set_intent(intent["intent_id"], "aborted", "ref unchanged; newer lease must reevaluate")
            self.events.append("acceptance.aborted", project_id=job["project_id"],
                               source="acceptance-coordinator", source_id=intent["intent_id"],
                               payload={"requeued": False, "reason": "newer lease owns job state"})
            return
        task = self.db.execute("SELECT status FROM tasks WHERE id=?", (intent["task_id"],)).fetchone()
        retry = (job["attempt_count"] < job["max_attempts"]
                 and task is not None and task["status"] == "in_progress")
        status = "retry_wait" if retry else "failed"
        self._set_intent(intent["intent_id"], "aborted", "recovery found the ref unchanged; reevaluation required")
        self.db.execute(
            """UPDATE jobs SET status=?, available_at=?, error=?, finished_at=?, updated_at=?,
                 leased_by=NULL, lease_token=NULL, lease_expires_at=NULL, version=version+1
               WHERE id=? AND attempt_count=? AND status IN ('leased','running')""",
            (status, now, "acceptance interrupted before Git CAS; reevaluation required",
             None if retry else now, now, job["id"], intent["lease_generation"]),
        )
        self.db.execute(
            "UPDATE tasks SET status=?, completed_at=NULL, updated_at=?, version=version+1 WHERE id=? AND status='in_progress'",
            ("ready" if retry else "blocked", now, intent["task_id"]),
        )
        self.events.append("acceptance.aborted", project_id=job["project_id"],
                           source="acceptance-coordinator", source_id=intent["intent_id"],
                           payload={"requeued": retry, "reason": "ref unchanged; new lease must reevaluate"})

    def _mark_conflict(self, intent, job, reason: str) -> None:
        now = _iso(self.clock())
        self._set_intent(intent["intent_id"], "conflict", reason)
        owns_current_state = (job["attempt_count"] == intent["lease_generation"]
                              and job["status"] in {"leased", "running"})
        if owns_current_state:
            self.db.execute(
                """UPDATE jobs SET status='failed', error=?, finished_at=?, updated_at=?,
                     leased_by=NULL, lease_token=NULL, lease_expires_at=NULL, version=version+1 WHERE id=?""",
                (reason, now, now, job["id"]),
            )
            task = self.db.execute("SELECT status FROM tasks WHERE id=?", (intent["task_id"],)).fetchone()
            if task is not None and task["status"] not in {"done", "cancelled"}:
                self.db.execute(
                    "UPDATE tasks SET status='blocked', completed_at=NULL, updated_at=?, version=version+1 WHERE id=?",
                    (now, intent["task_id"]),
                )
        self.events.append("acceptance.conflict", project_id=job["project_id"],
                           source="acceptance-coordinator", source_id=intent["intent_id"],
                           payload={"reason": reason, "ref_name": intent["ref_name"],
                                    "job_state_updated": owns_current_state})

    def _set_intent(self, intent_id: str, status: str, error: str) -> None:
        self.db.execute("UPDATE acceptance_intents SET status=?, error=?, updated_at=? WHERE intent_id=?",
                         (status, error, _iso(self.clock()), intent_id))

    def _intent(self, intent_id: str):
        row = self.db.execute("SELECT * FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return row

    def _evidence_valid(self, intent) -> bool:
        row = self.db.execute(
            "SELECT sha256, content FROM artifacts WHERE job_id=? AND sha256=? AND verified=1",
            (intent["job_id"], intent["evidence_sha256"]),
        ).fetchone()
        return bool(row and row["content"] is not None
                    and hashlib.sha256(bytes(row["content"])).hexdigest() == row["sha256"])

    @staticmethod
    def _ref_sha(repo: Path, ref_name: str) -> str | None:
        try:
            return subprocess.check_output(["git", "rev-parse", "--verify", ref_name],
                                           cwd=repo, text=True, stderr=subprocess.PIPE).strip()
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def _normalize_ref(repo: Path, ref_name: str) -> str:
        if not ref_name or ref_name.startswith("-"):
            raise ValueError("accepted ref must be a branch name")
        if ref_name.startswith("refs/"):
            if not ref_name.startswith("refs/heads/"):
                raise ValueError("accepted ref must be under refs/heads")
            full_ref = ref_name
            args = ["git", "check-ref-format", full_ref]
        else:
            args = ["git", "check-ref-format", "--branch", ref_name]
            full_ref = f"refs/heads/{ref_name}"
        subprocess.run(args, cwd=repo, check=True, capture_output=True, text=True, timeout=30)
        return full_ref

    @staticmethod
    def _validate_sha(value: str, field: str) -> None:
        if (len(value) not in (40, 64)
                or any(char not in "0123456789abcdef" for char in value.lower())):
            raise ValueError(f"{field} must be a hexadecimal SHA-1 or SHA-256")
