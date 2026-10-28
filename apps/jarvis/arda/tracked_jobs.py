"""Generic operational job cards projected from Arda Core's event log."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from arda.core import Database, EventLog


STATUSES = {"queued", "running", "paused", "retrying", "completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "paused", "retrying"}
KINDS = {"one_off", "continuous", "scheduled"}
_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_IDEMPOTENCY = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_TERMINAL = {"completed", "failed", "cancelled"}
_ALLOWED_TRANSITIONS = {
    "queued": ACTIVE_STATUSES | _TERMINAL | {"queued"},
    "running": ACTIVE_STATUSES | _TERMINAL,
    "paused": ACTIVE_STATUSES | _TERMINAL,
    "retrying": ACTIVE_STATUSES | _TERMINAL,
    "completed": {"queued", "completed"},
    "failed": {"queued", "failed"},
    "cancelled": {"queued", "cancelled"},
}


class TrackedJobError(ValueError):
    """Invalid or conflicting operational lifecycle event."""


class TrackedJobRegistry:
    """Append lifecycle events to Core and derive durable job/run projections."""

    EVENT_TYPE = "tracked_job.lifecycle"

    def __init__(self, db: Database, clock: Callable[[], datetime] | None = None):
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.events = EventLog(db, self.clock)

    @staticmethod
    def _string(payload: dict[str, Any], name: str, *, required=False, limit=240, default="") -> str:
        value = payload.get(name, default)
        if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
            raise TrackedJobError(f"{name} must be a string of at most {limit} characters")
        return value.strip()

    def append(self, payload: dict[str, Any], *, source="external") -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TrackedJobError("event body must be an object")
        status = self._string(payload, "status", required=True, limit=16)
        if status not in STATUSES:
            raise TrackedJobError("unsupported job status")
        key = self._string(payload, "idempotency_key", required=True, limit=160)
        if not _IDEMPOTENCY.fullmatch(key):
            raise TrackedJobError("idempotency_key has invalid characters")
        job_id = self._string(payload, "job_id", limit=128)
        run_id = self._string(payload, "run_id", limit=128)
        if job_id and not _ID.fullmatch(job_id):
            raise TrackedJobError("job_id has invalid characters")
        if run_id and not _ID.fullmatch(run_id):
            raise TrackedJobError("run_id has invalid characters")
        name = self._string(payload, "name", required=status == "queued", limit=160)
        project = self._string(payload, "project", limit=120)
        kind = self._string(payload, "kind", limit=20, default="one_off") or "one_off"
        if kind not in KINDS:
            raise TrackedJobError("kind must be one_off, continuous, or scheduled")
        progress = payload.get("progress")
        if progress is not None and (not isinstance(progress, dict) or len(json.dumps(progress)) > 2048):
            raise TrackedJobError("progress must be an object no larger than 2048 characters")
        metrics = payload.get("metrics", {})
        if not isinstance(metrics, dict) or len(json.dumps(metrics, ensure_ascii=False)) > 4096:
            raise TrackedJobError("metrics must be an object no larger than 4096 characters")
        log = payload.get("log", "")
        if not isinstance(log, str):
            raise TrackedJobError("log must be a string")
        log = log[-4000:]
        result = payload.get("result")
        if result is not None and len(json.dumps(result, ensure_ascii=False)) > 4096:
            raise TrackedJobError("result must be no larger than 4096 characters")
        scheduled_at = self._string(payload, "scheduled_at", limit=64)
        heartbeat_timeout = payload.get("heartbeat_timeout_seconds")
        if heartbeat_timeout is None:
            heartbeat_timeout = 300
        if not isinstance(heartbeat_timeout, int) or not 30 <= heartbeat_timeout <= 86_400:
            raise TrackedJobError("heartbeat_timeout_seconds must be between 30 and 86400")
        source = self._string({"source": source}, "source", required=True, limit=80)

        with self.db.transaction():
            duplicate = self.db.execute(
                "SELECT event_id,payload FROM events WHERE type=? AND source_id=? ORDER BY created_at LIMIT 1",
                (self.EVENT_TYPE, key),
            ).fetchone()
            if duplicate:
                return {"event_id": duplicate["event_id"], "duplicate": True,
                        "event": json.loads(duplicate["payload"])}

            if not job_id:
                job_id = f"job_{uuid.uuid4().hex}"
            if not run_id:
                run_id = f"run_{uuid.uuid4().hex}"
            previous = self._run_events(job_id, run_id)
            if not previous:
                if status != "queued":
                    raise TrackedJobError("a run must be created with queued status first")
            else:
                last = previous[-1]["data"]
                if status not in _ALLOWED_TRANSITIONS[last["status"]]:
                    raise TrackedJobError(f"invalid status transition: {last['status']} -> {status}")
                if name and name != last["name"]:
                    raise TrackedJobError("job name cannot change after creation")
                if project and project != last.get("project", ""):
                    raise TrackedJobError("project cannot change after creation")
                kind = last.get("kind", kind)
                name = last["name"]
                project = last.get("project", "")
                if "progress" not in payload:
                    progress = last.get("progress")
                if "metrics" not in payload:
                    metrics = last.get("metrics", {})
                if payload.get("result") is None:
                    result = last.get("result")
                scheduled_at = scheduled_at or last.get("scheduled_at", "")
                heartbeat_timeout = payload.get("heartbeat_timeout_seconds",
                                                last.get("heartbeat_timeout_seconds", heartbeat_timeout))

            event = {
                "job_id": job_id, "run_id": run_id, "name": name or "",
                "project": project, "kind": kind, "status": status,
                "progress": progress, "metrics": metrics, "log": log,
                "result": result, "scheduled_at": scheduled_at,
                "heartbeat_timeout_seconds": heartbeat_timeout,
            }
            event_id = self.events.append(self.EVENT_TYPE, source=source, source_id=key,
                                          payload=event, correlation_id=job_id)
        return {"event_id": event_id, "duplicate": False, "event": event}

    def _run_events(self, job_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT rowid AS event_order,event_id,payload,created_at FROM events WHERE type=? ORDER BY rowid",
            (self.EVENT_TYPE,),
        ).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["payload"])
            if data.get("job_id") == job_id and data.get("run_id") == run_id:
                out.append({"event_id": row["event_id"], "created_at": row["created_at"],
                            "event_order": row["event_order"], "data": data})
        return out

    @staticmethod
    def _summarize(events: list[dict[str, Any]], *, include_logs=True) -> dict[str, Any]:
        latest = events[-1]["data"]
        first = events[0]
        return {
            **latest,
            "started_at": next((e["created_at"] for e in events if e["data"]["status"] == "running"), first["created_at"]),
            "updated_at": events[-1]["created_at"],
            "event_order": events[-1].get("event_order", 0),
            "event_count": len(events),
            "logs": ([{"at": e["created_at"], "text": e["data"].get("log", "")}
                      for e in events if e["data"].get("log")][-200:] if include_logs else []),
        }

    def list_jobs(self, *, view="all", project="", query="", limit=100,
                  offset=0, _reconcile=True) -> list[dict[str, Any]]:
        if view not in {"all", "active", "history", "scheduled"}:
            raise TrackedJobError("view must be all, active, history, or scheduled")
        if _reconcile:
            self.reconcile_stale()
        limit = max(1, min(int(limit), 250))
        offset = max(0, min(int(offset), 1_000_000))
        rows = self.db.execute(
            "SELECT rowid AS event_order,event_id,payload,created_at FROM events WHERE type=? ORDER BY rowid",
            (self.EVENT_TYPE,),
        ).fetchall()
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in rows:
            data = json.loads(row["payload"])
            grouped.setdefault(data["job_id"], {}).setdefault(data["run_id"], []).append(
                {"event_id": row["event_id"], "created_at": row["created_at"],
                 "event_order": row["event_order"], "data": data}
            )
        jobs = []
        for job_id, run_map in grouped.items():
            runs = [self._summarize(events, include_logs=False) for events in run_map.values()]
            runs.sort(key=lambda item: (item["updated_at"], item["event_order"]), reverse=True)
            latest = runs[0]
            if view == "active" and latest["status"] not in ACTIVE_STATUSES:
                continue
            if view == "history" and latest["status"] not in _TERMINAL:
                continue
            if view == "scheduled" and latest.get("kind") != "scheduled":
                continue
            if project and latest.get("project", "").casefold() != project.casefold():
                continue
            if query and query.casefold() not in (latest.get("name", "") + " " + latest.get("project", "")).casefold():
                continue
            jobs.append({**latest, "job_id": job_id, "run_count": len(runs), "runs": runs[:50]})
        jobs.sort(key=lambda item: (item["updated_at"], item["event_order"]), reverse=True)
        return jobs[offset:offset + limit]

    def reconcile_stale(self, timeout_seconds=300) -> int:
        """Close abandoned running runs after a bounded heartbeat grace period."""
        rows = self.db.execute(
            "SELECT event_id,payload,created_at FROM events WHERE type=? ORDER BY rowid",
            (self.EVENT_TYPE,),
        ).fetchall()
        latest: dict[tuple[str, str], Any] = {}
        for row in rows:
            data = json.loads(row["payload"])
            latest[(data["job_id"], data["run_id"])] = row
        now = self.clock()
        closed = 0
        for (job_id, run_id), row in latest.items():
            data = json.loads(row["payload"])
            if data.get("status") != "running":
                continue
            try:
                updated = datetime.fromisoformat(row["created_at"])
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            timeout = data.get("heartbeat_timeout_seconds", timeout_seconds)
            if (now - updated).total_seconds() <= timeout:
                continue
            self.append({
                "job_id": job_id, "run_id": run_id, "status": "failed",
                "idempotency_key": f"reconcile:{run_id}:heartbeat-timeout:{row['event_id']}",
                "name": data.get("name", ""), "project": data.get("project", ""),
                "kind": data.get("kind", "one_off"), "metrics": data.get("metrics", {}),
                "log": f"No lifecycle heartbeat for at least {timeout} seconds; marked stale.",
            }, source="core-reconciler")
            closed += 1
        return closed

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        rows = self.db.execute(
            "SELECT rowid AS event_order,event_id,payload,created_at FROM events "
            "WHERE type=? AND correlation_id=? ORDER BY rowid",
            (self.EVENT_TYPE, job_id),
        ).fetchall()
        if not rows:
            return None
        run_map: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            data = json.loads(row["payload"])
            run_map.setdefault(data["run_id"], []).append(
                {"event_id": row["event_id"], "created_at": row["created_at"],
                 "event_order": row["event_order"], "data": data}
            )
        runs = [self._summarize(events, include_logs=False) for events in run_map.values()]
        runs.sort(key=lambda item: (item["updated_at"], item["event_order"]), reverse=True)
        return {**runs[0], "job_id": job_id, "run_count": len(runs), "runs": runs[:50]}

    def get_run(self, job_id: str, run_id: str) -> dict[str, Any] | None:
        events = self._run_events(job_id, run_id)
        return self._summarize(events) if events else None
