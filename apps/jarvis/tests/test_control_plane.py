from __future__ import annotations

import json
import threading
import urllib.request

from arda.awareness import ProjectRegistry, TaskStore
from arda.control_plane import ControlCenterHandler, ControlPlane
from arda.core import JobQueue, bootstrap_runtime_database
from arda.organization import OrganizationRegistry
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError


def test_control_plane_reads_core_and_updates_with_optimistic_lock(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    project = ProjectRegistry(db).create("sample-project", "Sample Project")
    task = TaskStore(db).create("sample-project", "Inspect source health")
    db.close()

    plane = ControlPlane(db_path, evidence_root=tmp_path)
    assert plane.organizations()[0]["name"] == "Sample Project"
    assert plane.tasks()[0]["title"] == "Inspect source health"
    assert plane.update_organization_status("sample-project", "paused", project["version"])["status"] == "paused"
    assert plane.update_task_status(task["id"], "cancelled", task["version"])["status"] == "cancelled"
    assert plane.memory()["pending_jobs"] == 0


def test_control_center_exposes_json_routes(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    bootstrap_runtime_database(db_path).close()
    plane = ControlPlane(db_path, evidence_root=tmp_path)

    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/status") as response:
            payload = json.load(response)
        assert payload["core"]["available"] is True
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            assert b"Arda Control Center" in response.read()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_control_center_enqueues_execution_contract_and_exposes_job(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    ProjectRegistry(db).create("sample-project", "Sample Project")
    db.close()
    plane = ControlPlane(db_path, evidence_root=tmp_path)

    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = {
        "project_id": "sample-project",
        "task_id": "Sample Project-021",
        "title": "Bounded source scan retry visibility",
        "repo": "/repo",
        "baseline_ref": "main",
        "baseline_sha": "a" * 40,
        "objective": "Expose bounded source scan retry state",
        "acceptance": ["tests pass", "no unrelated changes"],
    }
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/tasks",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        assert result["task"]["status"] == "ready"
        assert result["job"]["status"] == "queued"
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/jobs") as response:
            jobs = json.load(response)
        assert jobs[0]["id"] == result["job"]["id"]
        assert jobs[0]["status"] == "queued"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_control_center_cancels_queued_jobs_and_requests_cooperative_running_cancel(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    queue = JobQueue(db)
    running = queue.enqueue("test", "cooperative cancel", idempotency_key="cancel-api-running")
    lease = queue.claim("fixture-worker")
    queue.start(lease.id, lease.lease_token)
    assert lease.id == running.id
    queued = queue.enqueue("test", "cancel before claim", idempotency_key="cancel-api-queued")
    db.close()
    plane = ControlPlane(db_path, evidence_root=tmp_path)

    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for job_id in (queued.id, running.id):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/jobs/{job_id}/cancel",
                data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            assert result["cancel_requested_at"]
        jobs = {item["id"]: item for item in plane.jobs()}
        assert jobs[queued.id]["status"] == "cancelled"
        assert jobs[running.id]["status"] == "running"
        assert jobs[running.id]["cancel_requested_at"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_control_center_exposes_organization_snapshot_and_next_task(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    ProjectRegistry(db).create("sample-project", "Sample Project")
    task = TaskStore(db).create("sample-project", "Ready task", status="ready", priority="high", task_id="ORG-TASK")
    registry = OrganizationRegistry(db)
    registry.upsert_profile("sample-project", mission="Find opportunities", policy={"push": "approval"})
    objective = registry.create_objective("sample-project", "Reliability", status="active", weight=20, objective_id="OBJ-ORG")
    registry.set_task_meta(task["id"], objective_id=objective["id"], user_impact=50)
    db.close()
    plane = ControlPlane(db_path, evidence_root=tmp_path)

    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/organizations/sample-project") as response:
            snapshot = json.load(response)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/organizations/sample-project/next-task") as response:
            next_task = json.load(response)
        assert snapshot["profile"]["mission"] == "Find opportunities"
        assert snapshot["objectives"][0]["id"] == "OBJ-ORG"
        assert next_task["id"] == "ORG-TASK"
        assert next_task["selection_score"] == 30 + 20 + 50
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_selector_result_can_be_auto_dispatched_and_claimed(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    ProjectRegistry(db).create("sample-project", "Sample Project")
    task = TaskStore(db).create("sample-project", "Selected task", status="ready", task_id="AUTO-TASK")
    registry = OrganizationRegistry(db)
    registry.upsert_profile("sample-project", mission="Run ready work")
    registry.set_task_meta(task["id"], user_impact=80)
    selected = registry.select_next_task("sample-project")
    db.close()
    assert selected is not None
    assert selected["id"] == "AUTO-TASK"

    plane = ControlPlane(db_path, evidence_root=tmp_path)
    result = plane.enqueue_task({
        "project_id": "sample-project",
        "task_id": selected["id"],
        "title": selected["title"],
        "repo": "/repo",
        "baseline_ref": "main",
        "baseline_sha": "a" * 40,
        "objective": selected["objective"] if "objective" in selected else selected["title"],
        "acceptance": ["tests pass"],
    })
    assert result["task"]["id"] == selected["id"]
    assert result["job"]["status"] == "queued"
    claimed = JobQueue(bootstrap_runtime_database(db_path)).claim("autopilot-worker")
    assert claimed.id == result["job"]["id"]


def test_control_center_rejects_malformed_json_and_non_object_payload(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    bootstrap_runtime_database(db_path).close()
    plane = ControlPlane(db_path, evidence_root=tmp_path)

    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(raw: bytes):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/tasks",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request)
        except HTTPError as exc:
            return exc.code
        raise AssertionError("malformed payload unexpectedly succeeded")

    try:
        assert post(b"{") == 400
        assert post(b"[]") == 400
    finally:
        server.shutdown()
        thread.join(timeout=2)
