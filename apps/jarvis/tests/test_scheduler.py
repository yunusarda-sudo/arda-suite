"""M1 Awareness Layer: deterministic scheduler and projection tests."""

from datetime import datetime, timedelta, timezone

import pytest

from arda.core import Database
from arda.scheduler import Scheduler


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def seed_project(db, *, project_id="sample-project", updated_at="2026-10-01T00:00:00+00:00"):
    db.execute(
        """INSERT INTO projects(id, name, status, brain_path, created_at, updated_at)
           VALUES (?, ?, 'active', ?, ?, ?)""",
        (project_id, project_id.upper(), f"brain/projects/{project_id}", updated_at, updated_at),
    )


def test_scheduler_migration_is_idempotent(tmp_path):
    path = tmp_path / "migration.sqlite3"
    first = Database(path)
    assert first.migrate() == [*range(1, 19), 21, 22, 23]
    assert first.migrate() == []
    assert first.table_exists("schedules")
    assert first.table_exists("scheduler_ticks")
    first.close()


def test_due_tick_is_idempotent_by_window_key(db):
    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    scheduler = Scheduler(db, clock=lambda: now)
    scheduler.install_defaults(run_immediately=True)

    first = scheduler.run_due()
    second = scheduler.run_due()

    assert first
    assert second == []
    assert db.scalar("SELECT COUNT(*) FROM scheduler_ticks") == len(first)
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='scheduler.tick'") == len(first)


def test_metrics_and_threshold_events_are_deterministic(db):
    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    seed_project(db, updated_at="2026-09-20T00:00:00+00:00")
    db.execute(
        """INSERT INTO tasks(id, project_id, title, status, priority, deadline,
           created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("task-1", "sample-project", "SMTP", "blocked", "high", "2026-10-04T00:00:00+00:00",
         "2026-09-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO tasks(id, project_id, title, status, priority, deadline,
           created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("task-2", "sample-project", "Decision", "awaiting_decision", "normal", None,
         "2026-09-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00"),
    )
    scheduler = Scheduler(db, clock=lambda: now)
    scheduler.install_defaults(run_immediately=True)
    scheduler.run_due()

    metric = db.execute("SELECT * FROM project_metrics WHERE project_id='sample-project'").fetchone()
    assert metric["blocked_task_count"] == 1
    assert metric["awaiting_decision_count"] == 1
    assert metric["open_task_count"] == 2
    assert metric["upcoming_deadline_count"] == 1
    assert metric["days_inactive"] == 11

    event_types = {
        row["type"] for row in db.execute(
            "SELECT type FROM events WHERE project_id='sample-project'"
        )
    }
    assert "project.inactive.warning" in event_types
    assert "deadline.urgent" in event_types


def test_restart_reconciles_running_tick(db):
    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    scheduler = Scheduler(db, clock=lambda: now, tick_timeout_seconds=10)
    scheduler.install_defaults(run_immediately=True)
    schedule = db.execute("SELECT id FROM schedules LIMIT 1").fetchone()["id"]
    scheduler.claim_tick(schedule, now=now)

    later = now + timedelta(seconds=11)
    recovered = Scheduler(db, clock=lambda: later, tick_timeout_seconds=10)
    assert recovered.reconcile_ticks() == 1
    assert db.scalar("SELECT COUNT(*) FROM scheduler_ticks WHERE status='failed'") == 1


def test_digest_event_is_generated_once_without_notification(db):
    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    scheduler = Scheduler(db, clock=lambda: now)
    scheduler.install_defaults(run_immediately=True)
    scheduler.run_due()

    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='morning_digest.requested'") == 1
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='morning_digest.generated'") == 1
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='notification.sent'") == 0


def test_optional_github_sync_runs_as_durable_schedule(db):
    seed_project(db)
    calls = []

    class FakeSync:
        def try_sync_project(self, project_id, repo):
            calls.append((project_id, repo))
            return {"status": "succeeded", "repo": repo}

    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    scheduler = Scheduler(
        db,
        clock=lambda: now,
        github_sync=FakeSync(),
        github_repos={"sample-project": "acme/sample-project"},
    )
    scheduler.install_defaults(run_immediately=True)
    scheduler.run_due()

    assert calls == [("sample-project", "acme/sample-project")]
    assert db.scalar("SELECT COUNT(*) FROM scheduler_ticks WHERE schedule_id='github_sync'") == 1
