"""M0 / Reliable Kernel: failure-first contract tests."""

import json
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from arda.core import (
    ApprovalError,
    ApprovalService,
    bootstrap_runtime_database,
    Database,
    EventLog,
    JobQueue,
    PolicyEngine,
    PolicyLevel,
    StaleLeaseError,
)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def test_migrations_are_idempotent_and_survive_reopen(tmp_path):
    path = tmp_path / "arda.sqlite3"
    first = Database(path)
    assert first.migrate() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
    assert first.migrate() == []
    first.close()

    reopened = Database(path)
    assert reopened.migrate() == []
    assert reopened.table_exists("events")
    assert reopened.table_exists("jobs")
    assert reopened.table_exists("requirement_contract_revisions")
    reopened.close()


def test_runtime_database_bootstrap_uses_versioned_migrations(tmp_path):
    path = tmp_path / "runtime" / "core.sqlite3"
    database = bootstrap_runtime_database(path)
    try:
        assert database.path == path
        assert database.table_exists("schema_migrations")
        assert database.scalar("SELECT MAX(version) FROM schema_migrations") == 17
    finally:
        database.close()


def test_event_log_is_append_only(db):
    events = EventLog(db)
    event_id = events.append(
        "github.pr.updated",
        project_id=None,
        source="github",
        source_id="pr-42",
        payload={"state": "open"},
        correlation_id="sync-1",
    )

    row = events.get(event_id)
    assert json.loads(row["payload"]) == {"state": "open"}

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE events SET type = 'tampered' WHERE event_id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM events WHERE event_id = ?", (event_id,))


def test_duplicate_idempotency_key_returns_one_job(db):
    queue = JobQueue(db, clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    first = queue.enqueue("coding", "Fix SMTP", idempotency_key="job-key-1")
    second = queue.enqueue("coding", "Fix SMTP", idempotency_key="job-key-1")

    assert second.id == first.id
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 1

    with pytest.raises(ValueError, match="idempotency key conflict"):
        queue.enqueue("coding", "Different objective", idempotency_key="job-key-1")


def test_expired_lease_is_requeued_and_old_worker_is_fenced(db):
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    queue = JobQueue(db, clock=lambda: now[0], lease_seconds=10)
    job = queue.enqueue("test", "Run tests", idempotency_key="job-key-2")
    lease_one = queue.claim("worker-a")
    assert lease_one.id == job.id
    queue.start(job.id, lease_one.lease_token)

    now[0] += timedelta(seconds=11)
    lease_two = queue.claim("worker-b")
    assert lease_two.id == job.id
    assert lease_two.lease_token != lease_one.lease_token
    queue.start(job.id, lease_two.lease_token)

    with pytest.raises(StaleLeaseError):
        queue.complete(job.id, lease_one.lease_token, result={"worker": "a"})

    content = b'{"ok": true}'
    digest = hashlib.sha256(content).hexdigest()
    queue.record_artifact(job.id, "result.json", "result.json", digest, content=content)
    queue.complete(job.id, lease_two.lease_token, result={"worker": "b"})
    assert queue.get(job.id).status == "succeeded"


def test_restart_reconciles_running_job_without_losing_it(tmp_path):
    path = tmp_path / "restart.sqlite3"
    first = Database(path)
    first.migrate()
    queue = JobQueue(first, clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc), lease_seconds=10)
    job = queue.enqueue("test", "Run tests", idempotency_key="job-key-3")
    lease = queue.claim("worker-a")
    queue.start(job.id, lease.lease_token)
    first.close()

    second = Database(path)
    second.migrate()
    recovered = JobQueue(
        second,
        clock=lambda: datetime(2026, 10, 1, 0, 0, 11, tzinfo=timezone.utc),
        lease_seconds=10,
    )
    assert recovered.reconcile_expired() == 1
    assert recovered.get(job.id).status == "queued"
    second.close()


def test_complete_requires_verified_artifacts(db):
    queue = JobQueue(db)
    job = queue.enqueue("coding", "Produce report", idempotency_key="job-key-4")
    lease = queue.claim("worker-a")
    queue.start(job.id, lease.lease_token)

    with pytest.raises(ValueError, match="artifact"):
        queue.complete(job.id, lease.lease_token, result={"ok": True})

    content = b"report"
    digest = hashlib.sha256(content).hexdigest()
    queue.record_artifact(job.id, "test-report.json", "test-report.json", digest, content=content)
    queue.complete(job.id, lease.lease_token, result={"ok": True})
    assert queue.get(job.id).status == "succeeded"


def test_max_attempts_exceeded_is_terminal_failure(db):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Run once", idempotency_key="job-key-5", max_attempts=1)
    lease = queue.claim("worker-a")
    queue.start(job.id, lease.lease_token)

    failed = queue.fail(job.id, lease.lease_token, error="unknown side effect", retryable=True)
    assert failed.status == "failed"


def test_transaction_rolls_back_partial_state(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute(
                "INSERT INTO jobs(id, type, objective, acceptance_criteria, available_at, "
                "idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("job_rollback", "test", "never", "[]", "2026-10-01T00:00:00+00:00",
                 "rollback-key", "2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"),
            )
            raise RuntimeError("abort")

    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE id='job_rollback'") == 0


def test_job_transitions_append_events(db):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Emit events", idempotency_key="job-key-6")
    lease = queue.claim("worker-a")
    queue.start(job.id, lease.lease_token)
    content = b"event-result"
    queue.record_artifact(
        job.id, "result.txt", "result.txt", hashlib.sha256(content).hexdigest(), content=content
    )
    queue.complete(job.id, lease.lease_token, result={"ok": True})

    types = [row["type"] for row in db.execute(
        "SELECT type FROM events WHERE source_id=? ORDER BY created_at", (job.id,)
    )]
    assert types == ["job.leased", "job.started", "job.completed"]


def test_concurrent_claim_allows_only_one_lease(tmp_path):
    path = tmp_path / "claim-race.sqlite3"
    setup = Database(path)
    setup.migrate()
    JobQueue(setup).enqueue("test", "Race", idempotency_key="race-claim")
    setup.close()

    def claim(worker_id):
        connection = Database(path)
        connection.migrate()
        try:
            return JobQueue(connection).claim(worker_id).leased_by
        except LookupError:
            return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sorted(item for item in results if item) == ["worker-a"] or sorted(
        item for item in results if item
    ) == ["worker-b"]


def test_concurrent_enqueue_same_key_creates_one_job(tmp_path):
    path = tmp_path / "enqueue-race.sqlite3"
    setup = Database(path)
    setup.migrate()
    setup.close()

    def enqueue():
        connection = Database(path)
        connection.migrate()
        try:
            return JobQueue(connection).enqueue(
                "test", "Race", idempotency_key="race-enqueue"
            ).id
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue(), range(2)))
    assert len(set(results)) == 1

    check = Database(path)
    assert check.scalar("SELECT COUNT(*) FROM jobs") == 1
    check.close()


def test_concurrent_approval_consume_allows_only_one_execution(tmp_path):
    path = tmp_path / "approval-race.sqlite3"
    setup = Database(path)
    setup.migrate()
    payload = {"repo": "sample-project", "branch": "feature/race"}
    approval = ApprovalService(setup).request(
        "github.create_pr", "job-race", payload, requested_by="user"
    )
    approval_id = approval["id"]
    ApprovalService(setup).approve(approval_id, payload)
    setup.close()

    def consume():
        connection = Database(path)
        connection.migrate()
        try:
            ApprovalService(connection).consume(approval_id, payload)
            return "executed"
        except ApprovalError:
            return "rejected"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(results) == ["executed", "rejected"]


def test_approval_snapshot_hash_mismatch_is_rejected(db):
    approvals = ApprovalService(db, clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    payload = {"repo": "sample-project", "branch": "feature/smtp", "title": "Fix SMTP"}
    approval = approvals.request("github.create_pr", "job-1", payload, requested_by="user")
    approval_id = approval["id"]
    approvals.approve(approval_id, payload)
    assert approvals.consume(approval_id, payload)["branch"] == "feature/smtp"

    with pytest.raises(ApprovalError, match="already"):
        approvals.consume(approval_id, payload)


def test_approval_payload_change_and_expiry_are_rejected(db):
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    approvals = ApprovalService(db, clock=lambda: now[0])
    payload = {"repo": "sample-project", "branch": "feature/smtp"}

    changed = approvals.request("github.create_pr", "job-2", payload, requested_by="user")
    changed_id = changed["id"]
    with pytest.raises(ApprovalError, match="hash"):
        approvals.approve(changed_id, {**payload, "branch": "feature/other"})

    expiring = approvals.request("github.create_pr", "job-3", payload, requested_by="user", expires_in=5)
    now[0] += timedelta(seconds=6)
    with pytest.raises(ApprovalError, match="expired"):
        approvals.approve(expiring["id"], payload)


def test_policy_is_model_independent():
    policy = PolicyEngine()
    assert policy.decide("github.list_prs") is PolicyLevel.AUTO
    assert policy.decide("github.create_pr") is PolicyLevel.CONFIRM
    assert policy.decide("github.merge_pr") is PolicyLevel.HUMAN_ONLY
    assert policy.decide("private_key.read") is PolicyLevel.DENY
    assert policy.decide("unknown.tool") is PolicyLevel.CONFIRM
