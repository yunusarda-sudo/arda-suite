"""M1 Awareness Layer: deterministic scheduler and local projections."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from arda.core import Database, EventLog, _iso, _utc_now


LOCAL_ZONE = ZoneInfo("Europe/Istanbul")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Scheduler:
    """Durable, deterministic schedule runner; providers are optional adapters."""

    DEFAULTS = (
        ("hourly_health_check", "Hourly health check", "hourly_health_check", 3600, None, None),
        ("deadline_check", "Deadline check", "deadline_check", 3600, None, None),
        ("inactivity_check", "Inactivity check", "inactivity_check", 86400, None, None),
        ("morning_digest", "Morning digest", "morning_digest", 86400, 8, 0),
    )

    def __init__(
        self,
        db: Database,
        *,
        clock=_utc_now,
        tick_timeout_seconds: int = 300,
        github_sync=None,
        github_repos: dict[str, str] | None = None,
        director=None,
    ):
        self.db = db
        self.clock = clock
        self.tick_timeout_seconds = tick_timeout_seconds
        self.events = EventLog(db, clock)
        self.github_sync = github_sync
        self.github_repos = dict(github_repos or {})
        self.director = director

    def install_defaults(self, *, run_immediately: bool = False) -> None:
        now = self.clock()
        defaults = self.DEFAULTS
        if self.github_sync and self.github_repos:
            defaults += (("github_sync", "GitHub read-only sync", "github_sync", 3600, None, None),)
        if self.director is not None:
            defaults += (("director_tick", "Autonomous Director", "director_tick", 600, None, None),)
        for schedule_id, name, kind, interval, hour, minute in defaults:
            next_run = now if run_immediately else self._next_run(now, interval, hour, minute)
            self.db.execute(
                """INSERT INTO schedules(
                    id, name, kind, interval_seconds, local_hour, local_minute,
                    enabled, catch_up_policy, next_run_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'current_window', ?, 1)
                ON CONFLICT(id) DO NOTHING""",
                (schedule_id, name, kind, interval, hour, minute, _iso(next_run)),
            )

    def run_due(self, *, now: datetime | None = None) -> list[str]:
        now = now or self.clock()
        due = self.db.execute(
            """SELECT id FROM schedules WHERE enabled=1 AND next_run_at <= ?
               ORDER BY next_run_at, id""",
            (_iso(now),),
        ).fetchall()
        completed: list[str] = []
        for row in due:
            tick_id = self.claim_tick(row["id"], now=now)
            if tick_id is None:
                continue
            try:
                self._run_tick(tick_id, row["id"], now)
                completed.append(tick_id)
            except Exception as exc:  # scheduler must record failure, not die
                self._fail_tick(tick_id, str(exc), now)
        return completed

    def claim_tick(self, schedule_id: str, *, now: datetime | None = None) -> str | None:
        now = now or self.clock()
        schedule = self.db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if schedule is None:
            raise KeyError(schedule_id)
        scheduled_at = _parse(schedule["next_run_at"])
        window_key = self._window_key(schedule, scheduled_at)
        tick_id = f"tick_{uuid.uuid4().hex}"
        expires = now + timedelta(seconds=self.tick_timeout_seconds)
        with self.db.transaction():
            try:
                self.db.execute(
                    """INSERT INTO scheduler_ticks(
                        tick_id, schedule_id, scheduled_at, observed_at, window_key,
                        status, lease_expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
                    (tick_id, schedule_id, _iso(scheduled_at), _iso(now), window_key,
                     _iso(expires), _iso(now)),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    return None
                raise
            next_run = self._next_run(now, schedule["interval_seconds"],
                                      schedule["local_hour"], schedule["local_minute"])
            self.db.execute(
                """UPDATE schedules SET last_run_at=?, next_run_at=?, version=version+1
                   WHERE id=?""",
                (_iso(now), _iso(next_run), schedule_id),
            )
            self.events.append(
                "scheduler.tick", source="scheduler", source_id=tick_id,
                payload={"schedule_id": schedule_id, "window_key": window_key},
            )
        return tick_id

    def reconcile_ticks(self) -> int:
        now = self.clock()
        rows = self.db.execute(
            """SELECT tick_id FROM scheduler_ticks
               WHERE status='running' AND lease_expires_at < ?""",
            (_iso(now),),
        ).fetchall()
        for row in rows:
            self._fail_tick(row["tick_id"], "scheduler tick lease expired", now)
        return len(rows)

    def _run_tick(self, tick_id: str, schedule_id: str, now: datetime) -> None:
        schedule = self.db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        kind = schedule["kind"]
        if kind == "hourly_health_check":
            self._refresh_metrics(now)
        elif kind == "deadline_check":
            self._refresh_metrics(now)
        elif kind == "inactivity_check":
            self._refresh_metrics(now)
        elif kind == "morning_digest":
            self._morning_digest(now)
        elif kind == "github_sync":
            self._sync_github()
        elif kind == "director_tick":
            self._run_director()
        else:
            raise ValueError(f"unknown schedule kind: {kind}")
        with self.db.transaction():
            self.db.execute(
                """UPDATE scheduler_ticks SET status='succeeded', finished_at=?
                   WHERE tick_id=? AND status='running'""",
                (_iso(now), tick_id),
            )
            self.events.append("scheduler.tick.completed", source="scheduler", source_id=tick_id)

    def _run_director(self) -> None:
        if self.director is None:
            return
        missions = self.db.execute(
            "SELECT id, project_id FROM organization_missions WHERE status='active' AND expires_at>? ORDER BY id",
            (_iso(self.clock()),),
        ).fetchall()
        for mission in missions:
            result = self.director.run_once(mission["id"])
            self.events.append(
                "director.mission_tick.completed", project_id=mission["project_id"],
                source="director", source_id=mission["id"],
                payload={"state": result.get("state", "UNKNOWN"),
                         "reason": result.get("reason", "")},
            )

    def _sync_github(self) -> None:
        if not self.github_sync or not self.github_repos:
            return
        for project_id, repo in sorted(self.github_repos.items()):
            self.github_sync.try_sync_project(project_id, repo)

    def _fail_tick(self, tick_id: str, error: str, now: datetime) -> None:
        with self.db.transaction():
            self.db.execute(
                """UPDATE scheduler_ticks SET status='failed', error=?, finished_at=?
                   WHERE tick_id=? AND status='running'""",
                (error, _iso(now), tick_id),
            )
            self.events.append(
                "scheduler.tick.failed", source="scheduler", source_id=tick_id,
                payload={"error": error},
            )

    def _refresh_metrics(self, now: datetime) -> None:
        projects = self.db.execute(
            "SELECT id, updated_at FROM projects WHERE status='active'"
        ).fetchall()
        self.events.append("metrics.refresh.requested", source="scheduler")
        for project in projects:
            project_id = project["id"]
            latest_task = self.db.execute(
                "SELECT MAX(updated_at) FROM tasks WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            latest_event = self.db.execute(
                """SELECT MAX(created_at) FROM events
                   WHERE project_id=? AND source != 'scheduler'""", (project_id,)
            ).fetchone()[0]
            candidates = [value for value in (project["updated_at"], latest_task, latest_event) if value]
            last_activity = max(candidates) if candidates else _iso(now)
            days_inactive = max(0, math.floor((now - _parse(last_activity)).total_seconds() / 86400))
            open_tasks = self.db.execute(
                """SELECT COUNT(*) FROM tasks WHERE project_id=?
                   AND status NOT IN ('done', 'cancelled')""", (project_id,)
            ).fetchone()[0]
            blocked = self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE project_id=? AND status='blocked'", (project_id,)
            ).fetchone()[0]
            awaiting = self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE project_id=? AND status='awaiting_decision'", (project_id,)
            ).fetchone()[0]
            deadlines = self.db.execute(
                """SELECT deadline FROM tasks WHERE project_id=?
                   AND status NOT IN ('done', 'cancelled') AND deadline IS NOT NULL
                   ORDER BY deadline""", (project_id,)
            ).fetchall()
            upcoming = [row["deadline"] for row in deadlines if _parse(row["deadline"]) >= now]
            next_deadline = upcoming[0] if upcoming else None
            self.db.execute(
                """INSERT INTO project_metrics(
                    project_id, last_activity_at, days_inactive, open_task_count,
                    blocked_task_count, awaiting_decision_count, upcoming_deadline_count,
                    next_deadline_at, calculated_at, source_event_watermark, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(project_id) DO UPDATE SET
                    last_activity_at=excluded.last_activity_at,
                    days_inactive=excluded.days_inactive,
                    open_task_count=excluded.open_task_count,
                    blocked_task_count=excluded.blocked_task_count,
                    awaiting_decision_count=excluded.awaiting_decision_count,
                    upcoming_deadline_count=excluded.upcoming_deadline_count,
                    next_deadline_at=excluded.next_deadline_at,
                    calculated_at=excluded.calculated_at,
                    source_event_watermark=excluded.source_event_watermark,
                    version=project_metrics.version+1""",
                (project_id, last_activity, days_inactive, open_tasks, blocked, awaiting,
                 len(upcoming), next_deadline, _iso(now), latest_event),
            )
            if days_inactive >= 14:
                self._emit_once(
                    f"inactivity:{project_id}:stale:{last_activity[:10]}",
                    "project.inactive.stale", project_id, {"days_inactive": days_inactive},
                )
            elif days_inactive >= 7:
                self._emit_once(
                    f"inactivity:{project_id}:warning:{last_activity[:10]}",
                    "project.inactive.warning", project_id, {"days_inactive": days_inactive},
                )
            for deadline in upcoming:
                days_left = math.floor((_parse(deadline) - now).total_seconds() / 86400)
                if days_left <= 3:
                    self._emit_once(
                        f"deadline:{project_id}:{deadline}:urgent",
                        "deadline.urgent", project_id, {"deadline": deadline},
                    )
                elif days_left <= 7:
                    self._emit_once(
                        f"deadline:{project_id}:{deadline}:approaching",
                        "deadline.approaching", project_id, {"deadline": deadline},
                    )
            for row in deadlines:
                if _parse(row["deadline"]) < now:
                    self._emit_once(
                        f"deadline:{project_id}:{row['deadline']}:overdue",
                        "deadline.overdue", project_id, {"deadline": row["deadline"]},
                    )
        self.events.append("metrics.refresh.completed", source="scheduler")

    def _morning_digest(self, now: datetime) -> None:
        day = now.astimezone(LOCAL_ZONE).date().isoformat()
        requested = self._emit_once(
            f"morning_digest:{day}:requested", "morning_digest.requested", None, {"date": day}
        )
        if requested:
            metrics = [dict(row) for row in self.db.execute("SELECT * FROM project_metrics ORDER BY project_id")]
            self._emit_once(
                f"morning_digest:{day}:generated", "morning_digest.generated", None,
                {"date": day, "metrics": metrics},
            )

    def _emit_once(self, key: str, event_type: str, project_id: str | None, payload: dict) -> bool:
        with self.db.transaction():
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO scheduler_emissions(idempotency_key, event_type, created_at) VALUES (?, ?, ?)",
                (key, event_type, _iso(self.clock())),
            ).rowcount
            if inserted != 1:
                return False
            self.events.append(event_type, project_id=project_id, source="scheduler",
                               source_id=key, payload=payload)
        return True

    @staticmethod
    def _window_key(schedule, scheduled_at: datetime) -> str:
        if schedule["local_hour"] is not None:
            return f"{schedule['id']}:{scheduled_at.astimezone(LOCAL_ZONE).date().isoformat()}"
        interval = schedule["interval_seconds"] or 3600
        epoch = int(scheduled_at.timestamp())
        window = epoch - (epoch % interval)
        return f"{schedule['id']}:{datetime.fromtimestamp(window, timezone.utc).isoformat()}"

    @staticmethod
    def _next_run(now: datetime, interval: int, hour: int | None, minute: int | None) -> datetime:
        if hour is None:
            return now + timedelta(seconds=interval)
        local = now.astimezone(LOCAL_ZONE)
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
