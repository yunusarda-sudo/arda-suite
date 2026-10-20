"""Durable bridge between the Control Plane queue and an execution harness.

The bridge owns lifecycle state and leases; an injected executor owns the
actual Hermes/evaluator invocation.  Keeping those responsibilities separate
lets the control plane be tested without credentials or a live worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from arda.acceptance import AcceptanceCoordinator
from arda.autonomous_work_loop import WorkResult, WorkTask
from arda.awareness import TaskStore
from arda.core import Database, EventLog, Job, JobQueue, StaleLeaseError, _iso, _utc_now, bootstrap_runtime_database
from arda.organization import OrganizationRegistry


@dataclass(frozen=True)
class ExecutionResult:
    result: str
    reason: str = ""
    candidate_sha: str | None = None
    artifact_path: str | None = None
    episode_id: str | None = None
    attempts: int = 1
    evidence: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class TaskExecutor(Protocol):
    def execute(self, task: WorkTask) -> ExecutionResult: ...


@dataclass(frozen=True)
class BridgeRun:
    job_id: str
    task_id: str
    result: str
    task_status: str
    accepted_sha: str | None = None
    episode_id: str | None = None


class ExecutionBridge:
    """Claim one durable job, execute it, and persist the outcome safely."""

    JOB_TYPE = "autonomous-coding"

    def __init__(
        self,
        db_path: str | Path,
        executor: TaskExecutor | None = None,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 900,
    ):
        self.db_path = Path(db_path).expanduser()
        self.executor = executor
        self.worker_id = worker_id or f"bridge-{os.getpid()}"
        self.lease_seconds = lease_seconds

    def enqueue(
        self,
        task: WorkTask,
        *,
        project_id: str,
        title: str | None = None,
        priority: str = "normal",
        timeout_seconds: int | None = None,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        task.validate()
        db = bootstrap_runtime_database(self.db_path)
        try:
            organization = OrganizationRegistry(db)
            profile = organization.assert_enqueue_allowed(
                project_id, risk=task.risk, priority=priority, human_approved=human_approved
            )
            budget = json.loads(profile["budget_json"]) if profile is not None else {}
            configured_timeout = budget.get("hard_timeout_seconds")
            requested_timeout = timeout_seconds or task.hard_timeout_seconds
            if configured_timeout is not None and requested_timeout > configured_timeout:
                raise ValueError("task timeout exceeds organization budget")
            task_store = TaskStore(db)
            existing = None
            try:
                existing = task_store.get(task.task_id)
            except KeyError:
                pass
            if existing is None:
                task_row = task_store.create(
                    project_id,
                    title or task.objective[:120],
                    description=task.objective,
                    status="ready",
                    priority=priority,
                    source="control-plane",
                    task_id=task.task_id,
                )
            else:
                if existing["project_id"] != project_id:
                    raise ValueError("task id already belongs to another project")
                task_row = existing
            queue = JobQueue(db, lease_seconds=self.lease_seconds)
            idempotency_key = f"control-plane:{task.task_id}:v{task_row['version']}"
            job = queue.enqueue(
                self.JOB_TYPE,
                task.objective,
                idempotency_key=idempotency_key,
                project_id=project_id,
                acceptance_criteria=list(task.acceptance),
                context_manifest=task.canonical(),
                policy_snapshot={
                    "push": "deny",
                    "merge": "deny",
                    "deploy": "deny",
                    "prod": "deny",
                    "secrets": "deny",
                    "human_intervention": 0,
                    **({key: value for key, value in json.loads(profile["policy_json"]).items()
                        if key not in {"push", "merge", "deploy", "prod", "secrets"}}
                       if profile is not None else {}),
                },
                priority=priority,
                timeout_seconds=requested_timeout,
            )
            return {"task": dict(task_row), "job": self._job_payload(job)}
        finally:
            db.close()

    def enqueue_existing(self, task: WorkTask, *, project_id: str, priority: str = "normal",
                         timeout_seconds: int | None = None,
                         human_approved: bool = False) -> dict[str, Any]:
        """Dispatch a previously validated READY task exactly once."""
        return self.enqueue(task, project_id=project_id, priority=priority,
                            timeout_seconds=timeout_seconds, human_approved=human_approved)

    def heartbeat(self, job_id: str, lease_token: str) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return self._job_payload(JobQueue(db, lease_seconds=self.lease_seconds).heartbeat(job_id, lease_token))
        finally:
            db.close()

    def run_once(self) -> BridgeRun | None:
        db = bootstrap_runtime_database(self.db_path)
        try:
            queue = JobQueue(db, lease_seconds=self.lease_seconds)
            AcceptanceCoordinator(db, lease_seconds=self.lease_seconds).reconcile()
            queue.reconcile_expired()
            self._reconcile_cancelled_tasks(db)
            try:
                job = queue.claim(self.worker_id, job_type=self.JOB_TYPE)
            except LookupError:
                return None
            token = job.lease_token
            if not token:
                raise RuntimeError("claimed job did not receive a lease token")
            job = queue.start(job.id, token)
            contract = queue.describe(job.id, token)
            try:
                task = WorkTask.from_canonical(contract["context_manifest"])
            except Exception as exc:  # noqa: BLE001 - malformed persisted state must not strand a lease
                error = f"invalid persisted task contract: {type(exc).__name__}"
                queue.fail(job.id, token, error=error, retryable=False)
                EventLog(db).append(
                    "task.execution.failed", project_id=job.project_id,
                    source="execution-bridge", source_id=job.id,
                    payload={"job_id": job.id, "result": WorkResult.INVALID.value, "reason": error},
                )
                return BridgeRun(job.id, "unknown", WorkResult.INVALID.value, "blocked")
            task_store = TaskStore(db)
            task_row = task_store.get(task.task_id)
            task_row = task_store.update_status(
                task.task_id, "in_progress", expected_version=task_row["version"]
            )
            EventLog(db).append(
                "task.execution.started", project_id=task_row["project_id"],
                source="execution-bridge", source_id=task.task_id,
                payload={"job_id": job.id, "worker_id": self.worker_id, "attempt": job.attempt_count},
            )
            heartbeat_stop = threading.Event()
            execution_cancel = threading.Event()
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_until_done,
                args=(job.id, token, heartbeat_stop, execution_cancel),
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                if self.executor is None:
                    raise RuntimeError("no task executor configured")
                cancellable = getattr(self.executor, "execute_cancellable", None)
                result = cancellable(task, execution_cancel) if callable(cancellable) else self.executor.execute(task)
            except Exception as exc:  # noqa: BLE001 - boundary must persist failure
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=1)
                if self._ack_cancel_if_requested(db, queue, task_store, task, job.id, token):
                    return BridgeRun(job.id, task.task_id, "CANCELLED", "cancelled")
                error = f"executor failure: {type(exc).__name__}"
                self._mark_failed(db, queue, task_store, task, task_row, job.id, token, error)
                EventLog(db).append(
                    "task.execution.failed", project_id=task_row["project_id"],
                    source="execution-bridge", source_id=task.task_id,
                    payload={"job_id": job.id, "result": WorkResult.INVALID.value, "reason": error},
                )
                return BridgeRun(job.id, task.task_id, WorkResult.INVALID.value, "blocked")
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=1)

            if self._ack_cancel_if_requested(db, queue, task_store, task, job.id, token):
                return BridgeRun(job.id, task.task_id, "CANCELLED", "cancelled")

            payload = result.payload()
            payload["accepted_sha"] = None  # only the Core coordinator may assert acceptance
            artifact_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            artifact_ref = result.artifact_path or f"control-plane://jobs/{job.id}/{job.attempt_count}/result.json"
            artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
            accepted_sha = None
            try:
                queue.record_artifact(job.id, "execution-result", artifact_ref, artifact_sha,
                                      content=artifact_bytes, lease_token=token)
                if result.result == WorkResult.PASS.value:
                    if not result.candidate_sha:
                        raise ValueError("PASS result is missing a verified candidate SHA")
                    coordinator = AcceptanceCoordinator(db, lease_seconds=self.lease_seconds)
                    intent_id = coordinator.prepare(
                        job_id=job.id, lease_token=token, lease_generation=job.attempt_count,
                        task_id=task.task_id, repo_path=task.repo, ref_name=task.baseline_ref,
                        expected_old_sha=task.baseline_sha, candidate_sha=result.candidate_sha,
                        evidence_sha256=artifact_sha, result_payload=payload,
                    )
                    accepted_sha = coordinator.promote(intent_id, lease_token=token)
                    payload["accepted_sha"] = accepted_sha
                    terminal_status = "done"
                    if db.execute(
                        "SELECT 1 FROM organization_profiles WHERE project_id=?",
                        (task_row["project_id"],),
                    ).fetchone():
                        try:
                            OrganizationRegistry(db).reconcile_objective_lifecycle(task_row["project_id"])
                        except Exception as exc:  # acceptance is already durable; lifecycle projection is repairable
                            EventLog(db).append(
                                "organization.lifecycle_reconcile_failed", project_id=task_row["project_id"],
                                source="execution-bridge", source_id=task.task_id,
                                payload={"error": type(exc).__name__, "accepted_sha": accepted_sha},
                            )
                else:
                    terminal_status = "blocked"
                    task_store.update_status(task.task_id, terminal_status, expected_version=task_row["version"])
                    queue.fail(job.id, token, error=result.reason or result.result, retryable=False)
            except Exception as exc:  # noqa: BLE001 - persist a safe terminal failure
                if self._ack_cancel_if_requested(db, queue, task_store, task, job.id, token):
                    return BridgeRun(job.id, task.task_id, "CANCELLED", "cancelled")
                error = f"persistence failure: {type(exc).__name__}"
                self._mark_failed(db, queue, task_store, task, task_row, job.id, token, error)
                return BridgeRun(job.id, task.task_id, WorkResult.INVALID.value, "blocked")
            EventLog(db).append(
                "task.execution.completed", project_id=task_row["project_id"],
                source="execution-bridge", source_id=task.task_id,
                payload={"job_id": job.id, **payload},
            )
            return BridgeRun(job.id, task.task_id, result.result, terminal_status,
                             accepted_sha, result.episode_id)
        finally:
            db.close()

    @staticmethod
    def _job_payload(job: Job) -> dict[str, Any]:
        return {"id": job.id, "type": job.type, "objective": job.objective,
                "status": job.status,
                "leased_by": job.leased_by, "attempt_count": job.attempt_count,
                "cancel_requested_at": job.cancel_requested_at}

    def _heartbeat_until_done(self, job_id: str, lease_token: str, stop: threading.Event,
                              execution_cancel: threading.Event) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not stop.wait(interval):
            try:
                status = self.heartbeat(job_id, lease_token)
                if status.get("cancel_requested_at"):
                    execution_cancel.set()
                    return
            except Exception:
                execution_cancel.set()
                return

    @staticmethod
    def _ack_cancel_if_requested(db: Database, queue: JobQueue, task_store: TaskStore,
                                 task: WorkTask, job_id: str, token: str) -> bool:
        """Finalize operator cancellation only while this worker still owns the lease."""
        try:
            with db.transaction():
                row = db.execute(
                    "SELECT status, lease_token, cancel_requested_at FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                if (row is None or row["cancel_requested_at"] is None
                        or row["status"] not in {"leased", "running"} or row["lease_token"] != token):
                    return False
                queue.acknowledge_cancel(job_id, token)
                current = task_store.get(task.task_id)
                if current["status"] not in {"done", "cancelled", "blocked"}:
                    task_store.update_status(task.task_id, "cancelled", expected_version=current["version"])
                    EventLog(db).append(
                        "task.status_changed", project_id=current["project_id"],
                        source="execution-bridge", source_id=task.task_id,
                        payload={"status": "cancelled", "reason": "operator cancellation acknowledged"},
                    )
                return True
        except StaleLeaseError:
            return False

    @staticmethod
    def _reconcile_cancelled_tasks(db: Database) -> int:
        """Repair task projections when a worker dies before acknowledging cancellation."""
        rows = db.execute(
            "SELECT id, project_id, context_manifest FROM jobs "
            "WHERE type=? AND status='cancelled' AND cancel_requested_at IS NOT NULL",
            (ExecutionBridge.JOB_TYPE,),
        ).fetchall()
        repaired = 0
        task_store = TaskStore(db)
        for row in rows:
            try:
                task_id = json.loads(row["context_manifest"])["task_id"]
                task = task_store.get(task_id)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if task["status"] in {"done", "cancelled", "blocked"}:
                continue
            task_store.update_status(task_id, "cancelled", expected_version=task["version"])
            EventLog(db).append(
                "task.status_changed", project_id=row["project_id"], source="execution-bridge",
                source_id=task_id,
                payload={"status": "cancelled", "reason": "expired cancellation lease reconciled"},
            )
            repaired += 1
        return repaired

    @staticmethod
    def _mark_failed(db: Database, queue: JobQueue, task_store: TaskStore,
                     task: WorkTask, task_row: Any, job_id: str, token: str, error: str) -> None:
        try:
            now = _iso(_utc_now())
            with db.transaction():
                job = queue._verify_lease(job_id, token, ("leased", "running"))
                current = task_store.get(task.task_id)
                if current["status"] not in {"done", "cancelled", "blocked"}:
                    db.execute(
                        "UPDATE tasks SET status='blocked', completed_at=NULL, updated_at=?, version=version+1 WHERE id=? AND version=?",
                        (now, task.task_id, current["version"]),
                    )
                    EventLog(db).append("task.status_changed", project_id=current["project_id"],
                                        source="execution-bridge", source_id=task.task_id,
                                        payload={"status": "blocked", "reason": error})
                db.execute(
                    """UPDATE jobs SET status='failed', error=?, finished_at=?, updated_at=?,
                         leased_by=NULL, lease_token=NULL, lease_expires_at=NULL, version=version+1
                       WHERE id=? AND lease_token=?""",
                    (error, now, now, job.id, token),
                )
                EventLog(db).append("job.failed", project_id=job.project_id,
                                    source="execution-bridge", source_id=job_id,
                                    payload={"error": error, "retryable": False})
        except Exception:
            pass
