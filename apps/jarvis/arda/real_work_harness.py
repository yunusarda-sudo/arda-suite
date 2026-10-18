"""Persistent, evidence-first real-work queue orchestration.

This layer deliberately keeps policy outside the model: memory supplies prior
lessons, the evaluator decides acceptance, and only an accepted commit advances
the dedicated harness baseline.  The original checkout and its main branch are
never used as a write target.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

from arda.autonomous_work_loop import (
    AgentReport,
    EvaluationReport,
    GitWorktree,
    IndependentEvaluator,
    WorkResult,
    WorkTask,
)


class LessonMemory(Protocol):
    def recall(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]: ...
    def store(self, lesson: dict[str, Any]) -> str | None: ...


class RepairableAgent(Protocol):
    def run(self, task: WorkTask, workspace: Path, *, context: str = "") -> AgentReport: ...

    def repair(self, task: WorkTask, workspace: Path, failure: EvaluationReport, *, context: str = "") -> AgentReport: ...


@dataclass(frozen=True)
class TaskValidityReport:
    """Read-only preflight result produced before an agent is invoked."""

    valid: bool
    already_satisfied: bool = False
    reason: str = ""
    evidence: dict[str, Any] | None = None

    @property
    def result(self) -> WorkResult:
        if self.already_satisfied:
            return WorkResult.SKIP_ALREADY_SATISFIED
        return WorkResult.INVALID


@dataclass(frozen=True)
class RealWorkOutcome:
    task_id: str
    result: WorkResult
    attempts: int
    baseline_sha: str
    candidate_sha: str | None
    failure_class: str | None
    artifact_path: str
    memory_ref: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EpisodeStore:
    """Small append-oriented index; heavy outputs stay in artifact files."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, repo TEXT NOT NULL,
                baseline_sha TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                reasoning TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL,
                failure_class TEXT, verification_result TEXT NOT NULL,
                accepted_sha TEXT, artifact_path TEXT NOT NULL, memory_refs TEXT NOT NULL
            )""")

    def write(self, row: dict[str, Any]) -> None:
        fields = ("episode_id", "task_id", "repo", "baseline_sha", "provider", "model",
                  "reasoning", "started_at", "finished_at", "attempts", "result",
                  "failure_class", "verification_result", "accepted_sha", "artifact_path",
                  "memory_refs")
        values = [json.dumps(row.get(k), ensure_ascii=False) if k == "memory_refs" else row.get(k) for k in fields]
        with sqlite3.connect(self.path) as db:
            db.execute(f"INSERT OR REPLACE INTO episodes ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)

    def update_memory_refs(self, episode_id: str, refs: list[str]) -> bool:
        with sqlite3.connect(self.path) as db:
            cursor = db.execute("UPDATE episodes SET memory_refs=? WHERE episode_id=?",
                                (json.dumps(refs, ensure_ascii=False), episode_id))
            return cursor.rowcount == 1

    def get(self, episode_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM episodes WHERE episode_id=?", (episode_id,)).fetchone()
            return dict(row) if row else None


class RealWorkHarness:
    """Run a durable task queue with one bounded repair and baseline promotion."""

    def __init__(self, *, repo: str | Path, accepted_ref: str, workspace_root: str | Path,
                 artifact_root: str | Path, episode_db: str | Path, agent: Any,
                 evaluator: IndependentEvaluator, memory: LessonMemory | None = None,
                 worker_id: str = "arda-real-work-v1", memory_queue: str | Path | None = None,
                 validity_checker: Any | None = None):
        self.repo = Path(repo).expanduser().resolve()
        self.accepted_ref = accepted_ref
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.episodes = EpisodeStore(episode_db)
        self.agent = agent
        self.evaluator = evaluator
        self.memory = memory
        self.worker_id = worker_id
        self.validity_checker = validity_checker
        self.memory_queue = Path(memory_queue or (self.artifact_root.parent / "memory-jobs"))
        self.memory_queue.mkdir(parents=True, exist_ok=True)

    def current_baseline(self) -> str:
        return subprocess.check_output(["git", "rev-parse", self.accepted_ref], cwd=self.repo, text=True).strip()

    def run(self, tasks: Sequence[WorkTask]) -> tuple[RealWorkOutcome, ...]:
        outcomes: list[RealWorkOutcome] = []
        for task in tasks:
            baseline = self.current_baseline()
            bound = WorkTask(**{**task.canonical(), "repo": str(self.repo), "baseline_ref": self.accepted_ref,
                                "baseline_sha": baseline})
            outcomes.append(self._run_one(bound))
        return tuple(outcomes)

    def _run_one(self, task: WorkTask) -> RealWorkOutcome:
        started = _now()
        episode = uuid.uuid4().hex
        task_dir = self.artifact_root / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        recalled: list[dict[str, Any]] = []
        validity: TaskValidityReport | None = None
        worktree = GitWorktree(self.workspace_root)
        workspace: Path | None = None
        attempts = 0
        evaluation: EvaluationReport | None = None
        agent_reports: list[dict[str, Any]] = []
        changed: list[str] = []
        result = WorkResult.INVALID
        reason = "not_started"
        candidate_sha: str | None = None
        try:
            workspace = worktree.create(task)
            validity = self._check_validity(task, workspace)
            if not validity.valid or validity.already_satisfied:
                result = validity.result
                reason = validity.reason or result.value
            else:
                query = f"repo={self.repo.name} task_type={task.task_type} subsystem={task.affected_subsystem} objective={task.objective}"
                recalled = self.memory.recall(query) if self.memory else []
                context = json.dumps(recalled, ensure_ascii=False, indent=2)
                attempts = 1
                agent_report = self.agent.run(task, workspace, context=context)
                agent_reports.append({"attempt": 1, "report": agent_report.__dict__})
                changed = self._changed(workspace)
                if self._scope_error(task, changed):
                    reason = self._scope_error(task, changed) or "scope failure"
                    result = WorkResult.FAIL
                else:
                    evaluation = self.evaluator.evaluate(task, workspace)
                    if not evaluation.passed and hasattr(self.agent, "repair"):
                        attempts = 2
                        repair_report = self.agent.repair(task, workspace, evaluation, context=context)
                        agent_reports.append({"attempt": 2, "report": repair_report.__dict__})
                        changed = self._changed(workspace)
                        scope = self._scope_error(task, changed)
                        evaluation = None if scope else self.evaluator.evaluate(task, workspace)
                        if scope:
                            reason = scope
                    result = WorkResult.PASS if evaluation and evaluation.passed else WorkResult.FAIL
                    reason = "independent evaluation passed" if result is WorkResult.PASS else (evaluation.error if evaluation else reason)
                if result is WorkResult.PASS:
                    # Evaluators can create or alter files while running tests.
                    # Acceptance must inspect the final delivered filesystem.
                    changed = self._changed(workspace)
                    scope = self._scope_error(task, changed)
                    if scope:
                        result = WorkResult.FAIL
                        reason = scope
                    else:
                        candidate_sha = self._candidate(task, workspace)
        except Exception as exc:
            reason = str(exc)
            result = WorkResult.INVALID
        finally:
            if workspace is not None:
                try:
                    worktree.remove(self.repo, workspace)
                except Exception:
                    result = WorkResult.INVALID
                    reason = "cleanup failed"
                    candidate_sha = None
            finished = _now()
            payload = {"schema": "arda-real-work-v1", "episode_id": episode, "task": task.canonical(),
                       "result": result.value, "reason": reason, "attempts": attempts,
                       "started_at": started, "finished_at": finished,
                       "changed_paths": changed, "recalled_lessons": recalled,
                       "agent_reports": agent_reports, "evaluation": evaluation.__dict__ if evaluation else None,
                       "validity": validity.__dict__ if validity else None,
                       "candidate_sha": candidate_sha, "accepted_sha": None}
            artifact = task_dir / f"evidence-{episode}.json"
            artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            # Candidate evidence is independent of acceptance. Core persists
            # the accepted SHA after its lease-fenced Git/SQLite protocol.
            lesson = {"task_id": task.task_id, "repo": self.repo.name,
                      "task_type": task.task_type, "result": result.value,
                      "failure_class": reason, "repair": attempts > 1,
                      "verification": evaluation.__dict__ if evaluation else None,
                      "candidate_commit": candidate_sha}
            self.episodes.write({"episode_id": episode, "task_id": task.task_id, "repo": str(self.repo),
                "baseline_sha": task.baseline_sha, "provider": task.provider, "model": task.model,
                "reasoning": task.reasoning, "started_at": started, "finished_at": finished,
                "attempts": attempts, "result": result.value, "failure_class": None if result is WorkResult.PASS else reason,
                "verification_result": json.dumps(evaluation.__dict__ if evaluation else {}, default=str),
                "accepted_sha": None, "artifact_path": str(artifact), "memory_refs": []})
            self._enqueue_memory_job(episode, lesson)
        return RealWorkOutcome(task.task_id, result, attempts, task.baseline_sha, candidate_sha,
                               None if result is WorkResult.PASS else reason, str(artifact), None)

    def _check_validity(self, task: WorkTask, workspace: Path) -> TaskValidityReport:
        """Require a concrete contract and read-only proof of missing behavior."""
        if not task.acceptance or any(not criterion.strip() for criterion in task.acceptance):
            return TaskValidityReport(False, reason="acceptance criteria must be concrete and non-empty")
        if self.validity_checker is None:
            return TaskValidityReport(True, reason="no task-specific validity checker configured")
        report = self.validity_checker(task, workspace)
        if not isinstance(report, TaskValidityReport):
            raise TypeError("validity_checker must return TaskValidityReport")
        if not report.valid and not report.already_satisfied:
            return report
        return report

    def _enqueue_memory_job(self, episode_id: str, lesson: dict[str, Any]) -> Path:
        job = self.memory_queue / f"{episode_id}.json"
        tmp = job.with_suffix(".tmp")
        tmp.write_text(json.dumps({"episode_id": episode_id, "lesson": lesson}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(job)
        return job

    def reconcile_artifacts(self) -> int:
        """Rebuild missing episode rows and enqueue missing memory sidecars."""
        repaired = 0
        for artifact in sorted(self.artifact_root.glob("TASK-*/evidence-*.json")):
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            episode_id = payload["episode_id"]
            task = payload["task"]
            if self.episodes.get(episode_id) is None:
                self.episodes.write({"episode_id": episode_id, "task_id": task["task_id"],
                    "repo": str(self.repo), "baseline_sha": task["baseline_sha"],
                    "provider": task["provider"], "model": task["model"], "reasoning": task["reasoning"],
                    "started_at": payload.get("started_at", "unknown"), "finished_at": payload.get("finished_at", "unknown"),
                    "attempts": payload.get("attempts", 0), "result": payload["result"],
                    "failure_class": None if payload["result"] == "PASS" else payload.get("reason"),
                    "verification_result": json.dumps(payload.get("evaluation") or {}, default=str),
                    "accepted_sha": payload.get("accepted_sha"), "artifact_path": str(artifact), "memory_refs": []})
                repaired += 1
            if self.episodes.get(episode_id).get("memory_refs") in ("[]", "", None):
                lesson = {"task_id": task["task_id"], "repo": self.repo.name,
                          "task_type": task.get("task_type", "maintenance"), "result": payload["result"],
                          "failure_class": payload.get("reason"), "repair": payload.get("attempts", 0) > 1,
                          "verification": payload.get("evaluation"), "accepted_commit": payload.get("accepted_sha")}
                self._enqueue_memory_job(episode_id, lesson)
        return repaired

    def _candidate(self, task: WorkTask, workspace: Path) -> str:
        subprocess.run(["git", "add", "--all"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", f"harness: accept {task.task_id}"], cwd=workspace, check=True,
                       capture_output=True, text=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace, text=True).strip()

    @staticmethod
    def _changed(workspace: Path) -> list[str]:
        out = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace, text=True)
        return sorted({line[3:].split(" -> ")[-1] for line in out.splitlines() if len(line) >= 4})

    @staticmethod
    def _scope_error(task: WorkTask, changed: Sequence[str]) -> str | None:
        import fnmatch
        if any(any(fnmatch.fnmatch(path, pattern) for pattern in task.forbidden_paths) for path in changed):
            return "forbidden path changed"
        if task.allowed_paths and any(not any(fnmatch.fnmatch(path, pattern) for pattern in task.allowed_paths) for path in changed):
            return "unrelated path changed"
        return None
