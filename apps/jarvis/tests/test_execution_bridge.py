from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from arda.autonomous_work_loop import WorkResult, WorkTask
from arda.awareness import ProjectRegistry
from arda.core import bootstrap_runtime_database
from arda.execution_bridge import ExecutionBridge, ExecutionResult
from arda.organization import OrganizationRegistry


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "source.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/heads/accepted", baseline], cwd=repo, check=True)
    return repo, baseline, candidate


def make_task(repo, baseline) -> WorkTask:
    return WorkTask(
        task_id="TASK-001",
        repo=str(repo),
        baseline_ref="refs/heads/accepted",
        baseline_sha=baseline,
        objective="Implement the bounded task",
        acceptance=("tests pass",),
    )


class PassingExecutor:
    def __init__(self, candidate_sha):
        self.candidate_sha = candidate_sha

    def execute(self, task):
        assert task.task_id == "TASK-001"
        return ExecutionResult(
            result=WorkResult.PASS.value,
            candidate_sha=self.candidate_sha,
            episode_id="ep-1",
            evidence={"tests": "PASS"},
        )


class FailingExecutor:
    def execute(self, task):
        return ExecutionResult(result=WorkResult.FAIL.value, reason="scope")


class ExpiredFailingExecutor:
    def __init__(self, db_path, job_id):
        self.db_path = db_path
        self.job_id = job_id

    def execute(self, task):
        db = bootstrap_runtime_database(self.db_path)
        try:
            expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            db.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?", (expired, self.job_id))
        finally:
            db.close()
        raise RuntimeError("executor failed after lease expiry")


def setup_db(path):
    db = bootstrap_runtime_database(path)
    ProjectRegistry(db).create("sample-project", "Sample Project")
    db.close()


def test_enqueue_is_idempotent_and_run_persists_success(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, baseline, candidate = make_repo(tmp_path)
    setup_db(db_path)
    bridge = ExecutionBridge(db_path, PassingExecutor(candidate), worker_id="worker-1")

    first = bridge.enqueue(make_task(repo, baseline), project_id="sample-project")
    second = bridge.enqueue(make_task(repo, baseline), project_id="sample-project")
    assert first["task"]["id"] == second["task"]["id"] == "TASK-001"
    assert first["job"]["id"] == second["job"]["id"]

    outcome = bridge.run_once()
    assert outcome.result == "PASS"
    assert outcome.task_status == "done"
    assert outcome.accepted_sha == candidate
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate
    db = bootstrap_runtime_database(db_path)
    try:
        task = db.execute("SELECT status FROM tasks WHERE id='TASK-001'").fetchone()
        job = db.execute("SELECT status, result FROM jobs").fetchone()
        events = [row[0] for row in db.execute("SELECT type FROM events").fetchall()]
        assert task["status"] == "done"
        assert job["status"] == "succeeded"
        assert '"accepted_sha"' in job["result"]
        assert "task.execution.started" in events
        assert "task.execution.completed" in events
    finally:
        db.close()


def test_failure_is_terminal_for_task_and_does_not_advance_baseline(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    setup_db(db_path)
    repo, baseline, candidate = make_repo(tmp_path)
    bridge = ExecutionBridge(db_path, FailingExecutor(), worker_id="worker-1")
    bridge.enqueue(make_task(repo, baseline), project_id="sample-project")

    outcome = bridge.run_once()
    assert outcome.result == "FAIL"
    assert outcome.task_status == "blocked"
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-001'").fetchone()[0] == "blocked"
        assert db.execute("SELECT status FROM jobs").fetchone()[0] == "failed"
    finally:
        db.close()


def test_heartbeat_renews_claimed_job(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    setup_db(db_path)
    repo, baseline, candidate = make_repo(tmp_path)
    bridge = ExecutionBridge(db_path, PassingExecutor(candidate), worker_id="worker-1")
    queued = bridge.enqueue(make_task(repo, baseline), project_id="sample-project")
    db = bootstrap_runtime_database(db_path)
    try:
        from arda.core import JobQueue
        job = JobQueue(db).claim("worker-1", job_type="autonomous-coding")
        renewed = bridge.heartbeat(job.id, job.lease_token)
        assert renewed["id"] == job.id
        assert renewed["status"] == "leased"
    finally:
        db.close()


def test_stale_executor_failure_cannot_block_task_or_fail_new_lease(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, baseline, candidate = make_repo(tmp_path)
    setup_db(db_path)
    base_bridge = ExecutionBridge(db_path, worker_id="worker-1")
    queued = base_bridge.enqueue(make_task(repo, baseline), project_id="sample-project")
    bridge = ExecutionBridge(db_path, ExpiredFailingExecutor(db_path, queued["job"]["id"]),
                             worker_id="worker-1")

    outcome = bridge.run_once()

    assert outcome.result == "INVALID"
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-001'").fetchone()[0] == "in_progress"
        assert db.execute("SELECT status FROM jobs").fetchone()[0] == "running"
        assert db.execute("SELECT status FROM acceptance_intents").fetchall() == []
    finally:
        db.close()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == baseline


def test_heartbeat_failure_signals_cancellable_executor(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, baseline, _candidate = make_repo(tmp_path)
    setup_db(db_path)

    class WaitingExecutor:
        def __init__(self):
            self.cancelled = threading.Event()

        def execute_cancellable(self, task, cancel_event):
            assert task.task_id == "TASK-001"
            assert cancel_event.wait(4)
            self.cancelled.set()
            return ExecutionResult(result=WorkResult.INVALID.value, reason="Core lease was lost")

    executor = WaitingExecutor()
    bridge = ExecutionBridge(db_path, executor, worker_id="worker-1", lease_seconds=3)
    bridge.enqueue(make_task(repo, baseline), project_id="sample-project")

    def lose_lease(_job_id, _token):
        raise RuntimeError("stale lease")

    bridge.heartbeat = lose_lease
    outcome = bridge.run_once()

    assert executor.cancelled.is_set()
    assert outcome.result == WorkResult.INVALID.value


def test_run_bounded_terminates_process_group_when_cancelled(tmp_path):
    from arda.hermes_sandbox import HermesPodmanTaskExecutor

    cancelled = threading.Event()
    timer = threading.Timer(0.3, cancelled.set)
    timer.start()
    started = time.monotonic()
    result = HermesPodmanTaskExecutor._run_bounded(
        ["/bin/sh", "-c", "sleep 10"], cwd=tmp_path, env=os.environ,
        timeout_seconds=5, stdout_limit=1024, stderr_limit=1024, cancel_event=cancelled,
    )
    timer.join()

    assert result["termination_reason"] == "Core lease lost"
    assert time.monotonic() - started < 3
    assert result["exit_code"] is not None


def test_lifecycle_projection_failure_does_not_undo_durable_acceptance(tmp_path, monkeypatch):
    db_path = tmp_path / "core.sqlite3"
    repo, baseline, candidate = make_repo(tmp_path)
    setup_db(db_path)
    db = bootstrap_runtime_database(db_path)
    try:
        OrganizationRegistry(db).upsert_profile("sample-project", mission="test")
    finally:
        db.close()

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(OrganizationRegistry, "reconcile_objective_lifecycle", fail_projection)
    bridge = ExecutionBridge(db_path, PassingExecutor(candidate), worker_id="worker-1")
    bridge.enqueue(make_task(repo, baseline), project_id="sample-project")

    outcome = bridge.run_once()

    assert outcome.result == "PASS"
    assert outcome.accepted_sha == candidate
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-001'").fetchone()[0] == "done"
        assert db.execute("SELECT status FROM jobs").fetchone()[0] == "succeeded"
        assert db.execute("SELECT type FROM events WHERE type='organization.lifecycle_reconcile_failed'").fetchone()
    finally:
        db.close()
