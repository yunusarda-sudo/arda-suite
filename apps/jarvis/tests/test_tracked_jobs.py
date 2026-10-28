from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from http.server import ThreadingHTTPServer

from arda.control_plane import ControlCenterHandler, ControlPlane
from arda.core import JobQueue, bootstrap_runtime_database
from arda.tracked_jobs import TrackedJobError, TrackedJobRegistry


def _event(status, key, **extra):
    return {"status": status, "idempotency_key": key, **extra}


def test_registry_is_durable_idempotent_and_keeps_multiple_runs(tmp_path):
    path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(path)
    registry = TrackedJobRegistry(db)
    queued = registry.append(_event("queued", "scrape:r1:queued", job_id="pickable-scrape",
                                   run_id="r1", name="Reddit data collection", project="Pickable",
                                   kind="continuous", metrics={"new_records": 0}))
    assert queued["duplicate"] is False
    duplicate = registry.append(_event("queued", "scrape:r1:queued", job_id="pickable-scrape",
                                       run_id="r1", name="Reddit data collection", project="Pickable",
                                       kind="continuous"))
    assert duplicate["duplicate"] is True
    registry.append(_event("running", "scrape:r1:running", job_id="pickable-scrape", run_id="r1",
                           metrics={"new_records": 12}, log="checkpoint updated"))
    registry.append(_event("completed", "scrape:r1:done", job_id="pickable-scrape", run_id="r1",
                           result={"communities": 100}))
    registry.append(_event("completed", "scrape:r1:logs", job_id="pickable-scrape", run_id="r1",
                           log="All 100 communities complete."))
    registry.append(_event("completed", "scrape:r1:logs", job_id="pickable-scrape", run_id="r1",
                           log="All 100 communities complete."))
    registry.append(_event("queued", "scrape:r2:queued", job_id="pickable-scrape",
                           run_id="r2", name="Reddit data collection", project="Pickable",
                           kind="continuous"))
    assert registry.list_jobs(view="active")[0]["run_id"] == "r2"
    assert registry.get_job("pickable-scrape")["run_count"] == 2
    archived_run = registry.get_run("pickable-scrape", "r1")
    assert archived_run["status"] == "completed"
    assert archived_run["result"] == {"communities": 100}
    assert [entry["text"] for entry in archived_run["logs"]] == [
        "checkpoint updated", "All 100 communities complete."
    ]
    with pytest.raises(TrackedJobError, match="invalid status transition"):
        registry.append(_event("running", "scrape:r1:late", job_id="pickable-scrape", run_id="r1"))
    db.close()

    reopened = bootstrap_runtime_database(path)
    assert TrackedJobRegistry(reopened).list_jobs(view="active")[0]["run_id"] == "r2"
    reopened.close()


def test_registry_statuses_failure_scheduling_and_bounded_logs(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    registry = TrackedJobRegistry(db)
    for status in ("paused", "retrying", "failed", "cancelled", "completed"):
        job_id = f"job-{status}"
        run_id = f"run-{status}"
        registry.append(_event("queued", f"{job_id}:q", job_id=job_id, run_id=run_id,
                               name=job_id, kind="scheduled" if status == "completed" else "one_off",
                               scheduled_at="2026-10-16T12:00:00Z" if status == "completed" else ""))
        registry.append(_event(status, f"{job_id}:{status}", job_id=job_id, run_id=run_id,
                               log="x" * 5000 if status == "failed" else "state update"))
    assert {j["status"] for j in registry.list_jobs(view="history")} == {"failed", "cancelled", "completed"}
    assert registry.list_jobs(view="scheduled")[0]["kind"] == "scheduled"
    failed = registry.get_run("job-failed", "run-failed")
    assert max(map(len, (entry["text"] for entry in failed["logs"]))) <= 4000
    db.close()


def test_running_job_with_missing_heartbeat_is_reconciled(tmp_path):
    now = [datetime(2026, 10, 15, 12, tzinfo=timezone.utc)]
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    registry = TrackedJobRegistry(db, clock=lambda: now[0])
    registry.append(_event("queued", "stale:q", job_id="stale-job", run_id="stale-run",
                           name="Observer task"))
    registry.append(_event("running", "stale:r", job_id="stale-job", run_id="stale-run"))
    now[0] += timedelta(minutes=6)
    assert registry.list_jobs(view="active") == []
    stale = registry.get_run("stale-job", "stale-run")
    assert stale["status"] == "failed"
    assert "heartbeat" in stale["logs"][-1]["text"]
    db.close()


def test_core_queue_automatically_projects_attempt_runs(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    queue = JobQueue(db)
    job = queue.enqueue("research", "Analyze new product signals", idempotency_key="research:one")
    assert queue.enqueue("research", "Analyze new product signals", idempotency_key="research:one").id == job.id
    registry = TrackedJobRegistry(db)
    assert registry.get_job(job.id)["status"] == "queued"
    assert registry.get_job(job.id)["run_count"] == 1

    first = queue.claim("worker-a")
    queue.start(first.id, first.lease_token)
    queue.heartbeat(first.id, first.lease_token)
    queue.fail(first.id, first.lease_token, error="temporary provider outage", retryable=True)
    second = queue.claim("worker-a")
    queue.start(second.id, second.lease_token)
    content = b"verified artifact"
    queue.record_artifact(second.id, "report", "result.md", __import__("hashlib").sha256(content).hexdigest(),
                          content=content, lease_token=second.lease_token)
    queue.complete(second.id, second.lease_token, result={"summary": "done"})

    projected = registry.get_job(job.id)
    assert projected["status"] == "completed"
    assert projected["run_count"] == 2
    assert registry.get_run(job.id, f"{job.id}-attempt-1")["status"] == "retrying"
    assert registry.get_run(job.id, f"{job.id}-attempt-2")["status"] == "completed"
    db.close()


def test_control_plane_http_event_and_history_routes(tmp_path):
    path = tmp_path / "core.sqlite3"
    bootstrap_runtime_database(path).close()
    plane = ControlPlane(path, evidence_root=tmp_path)

    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = urllib.request.Request(
            base + "/api/tracked-jobs/events",
            data=json.dumps(_event("queued", "api:queued", job_id="api-job", run_id="api-run",
                                   name="API smoke", project="Pickable")).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as response:
            assert json.load(response)["duplicate"] is False
        with urllib.request.urlopen(base + "/api/tracked-jobs?view=active&project=Pickable") as response:
            jobs = json.load(response)["jobs"]
        assert jobs[0]["job_id"] == "api-job"
        with urllib.request.urlopen(base + "/api/tracked-jobs/api-job/runs/api-run") as response:
            assert json.load(response)["status"] == "queued"
    finally:
        server.shutdown()
        thread.join(timeout=2)
