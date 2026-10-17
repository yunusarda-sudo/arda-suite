"""Small SQLite-backed Arda Control Plane and LAN-safe Control Center.

The browser is only a projection and command client. Core SQLite remains the
source of truth; this module never invents task or organization state.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from arda.awareness import ProjectRegistry, TaskStore
from arda.autonomous_work_loop import WorkTask
from arda.core import JobQueue, bootstrap_runtime_database, runtime_database_path
from arda.diagnostics import Diagnostics
from arda.execution_bridge import ExecutionBridge
from arda.organization import OrganizationRegistry


def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a JSON boolean")
    return value


def _jsonable(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) if not isinstance(row, dict) else row for row in rows]


class ControlPlane:
    def __init__(self, db_path: str | Path | None = None, *, evidence_root: str | Path | None = None,
                 execution_bridge: ExecutionBridge | None = None):
        self.db_path = Path(db_path or runtime_database_path()).expanduser()
        bootstrap_runtime_database(self.db_path).close()
        self.evidence_root = Path(evidence_root or os.environ.get("ARDA_HARNESS_ROOT", "~/.arda/harness")).expanduser()
        self.execution_bridge = execution_bridge or ExecutionBridge(self.db_path)

    def _read(self) -> Diagnostics:
        return Diagnostics(self.db_path)

    def _evidence_roots(self) -> list[Path]:
        roots = [self.evidence_root]
        for raw in os.environ.get("ARDA_HARNESS_LEGACY_ROOTS", "").split(os.pathsep):
            if raw:
                candidate = Path(raw).expanduser()
                if candidate not in roots:
                    roots.append(candidate)
        return roots

    def status(self) -> dict[str, Any]:
        with self._read() as diagnostics:
            return {"core": diagnostics.status(), "doctor": diagnostics.doctor(), "host": self.host()}

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.status(), "tasks": self.tasks(), "jobs": self.jobs(),
                "memory": self.memory(), "evidence": self.evidence()}

    def organizations(self) -> list[dict[str, Any]]:
        with self._read() as diagnostics:
            return diagnostics.projects()

    def organization_snapshot(self, project_id: str) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return OrganizationRegistry(db).snapshot(project_id)
        finally:
            db.close()

    def select_next_task(self, project_id: str) -> dict[str, Any] | None:
        db = bootstrap_runtime_database(self.db_path)
        try:
            registry = OrganizationRegistry(db)
            registry.reconcile_objective_lifecycle(project_id)
            return registry.select_next_task(project_id)
        finally:
            db.close()

    def set_autopilot(self, project_id: str, enabled: bool) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return dict(OrganizationRegistry(db).set_autopilot(project_id, enabled))
        finally:
            db.close()

    def create_backlog_task(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"task_id", "title", "contract"}
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"missing task fields: {', '.join(missing)}")
        db = bootstrap_runtime_database(self.db_path)
        try:
            task = TaskStore(db).create(project_id, str(payload["title"]),
                                        description=str(payload.get("description", "")), status="ready",
                                        priority=str(payload.get("priority", "normal")), source="organization-autopilot",
                                        task_id=str(payload["task_id"]),
                                        human_approved=bool(payload.get("human_approved", False)))
            contract = dict(payload["contract"])
            contract["_human_approved"] = _strict_bool(payload.get("human_approved", False), "human_approved")
            meta = OrganizationRegistry(db).set_task_meta(
                task["id"], objective_id=payload.get("objective_id"),
                user_impact=int(payload.get("user_impact", 0)),
                dependency_status=str(payload.get("dependency_status", "ready")),
                risk=str(payload.get("risk", "low")), contract=contract,
                estimated_cost=int(payload.get("estimated_cost", 1)),
            )
            return {"task": dict(task), "metadata": dict(meta)}
        finally:
            db.close()

    def dispatch_next_task(self, project_id: str) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            registry = OrganizationRegistry(db)
            profile = registry.profile(project_id)
            if not profile["autopilot_enabled"]:
                registry.record_dispatch(project_id, "AUTOPILOT_OFF")
                return {"state": "OFF", "reason": "AUTOPILOT_OFF"}
            registry.reconcile_objective_lifecycle(project_id)
            profile = registry.profile(project_id)
            selected = registry.select_next_task(project_id, require_contract=True)
            if selected is None:
                registry.record_dispatch(project_id, "IDLE_NO_READY_WORK")
                return {"state": "IDLE", "reason": "IDLE_NO_READY_WORK"}
            contract = registry.task_contract(selected["id"])
            if not contract:
                registry.record_dispatch(project_id, "TASK_CONTRACT_MISSING")
                return {"state": "BLOCKED", "reason": "TASK_CONTRACT_MISSING", "task_id": selected["id"]}
            # The organization profile is the source of truth for the next
            # accepted baseline. Keep the persisted task contract immutable,
            # but dispatch the current baseline to the isolated executor.
            current_baseline = profile["accepted_baseline"]
            if current_baseline:
                contract = dict(contract)
                contract["baseline_ref"] = current_baseline
                contract["baseline_sha"] = current_baseline
            from arda.autonomous_work_loop import WorkTask
            task = WorkTask.from_canonical(contract)
            task_row = TaskStore(db).get(selected["id"])
            if task_row["status"] == "ready":
                TaskStore(db).update_status(selected["id"], "in_progress", expected_version=task_row["version"])
            try:
                result = self.execution_bridge.enqueue_existing(
                    task, project_id=project_id, priority=task_row["priority"],
                    timeout_seconds=task.hard_timeout_seconds,
                    human_approved=_strict_bool(contract.get("_human_approved", False), "human_approved"),
                )
            except Exception:
                current = TaskStore(db).get(selected["id"])
                if current["status"] == "in_progress":
                    TaskStore(db).update_status(selected["id"], "ready", expected_version=current["version"])
                raise
            registry.record_dispatch(project_id, "DISPATCHED")
            return {"state": "DISPATCHED", "reason": "DISPATCHED", "selection": selected, **result}
        finally:
            db.close()

    def update_organization_profile(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return dict(OrganizationRegistry(db).upsert_profile(
                project_id, mission=str(payload.get("mission", "")),
                accepted_baseline=payload.get("accepted_baseline"),
                policy=payload.get("policy"), budget=payload.get("budget")))
        finally:
            db.close()

    def create_organization_objective(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return dict(OrganizationRegistry(db).create_objective(
                project_id, str(payload["title"]), description=str(payload.get("description", "")),
                status=str(payload.get("status", "planned")), weight=int(payload.get("weight", 0)),
                objective_id=payload.get("id"),
                activation_policy=str(payload.get("activation_policy", "manual_only")),
                depends_on=tuple(str(value) for value in payload.get("depends_on", []))))
        finally:
            db.close()

    def tasks(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC, id DESC").fetchall()]

    def jobs(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT id, project_id, type, objective, status, leased_by, attempt_count, "
                "cancel_requested_at, "
                "started_at, finished_at, error, result, created_at, updated_at "
                "FROM jobs ORDER BY updated_at DESC, id DESC").fetchall()]

    def cancel_job(self, job_id: str, *, requested_by: str = "control-center-operator") -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            job = JobQueue(db).request_cancel(job_id, requested_by=requested_by)
            row = db.execute("SELECT cancel_requested_at FROM jobs WHERE id=?", (job_id,)).fetchone()
            return {"job_id": job.id, "status": job.status,
                    "cancel_requested_at": row["cancel_requested_at"]}
        finally:
            db.close()

    def enqueue_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a UI task contract and place it in the durable queue."""
        required = {"project_id", "task_id", "objective", "acceptance", "repo",
                    "baseline_ref", "baseline_sha"}
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"missing task fields: {', '.join(missing)}")
        task = WorkTask(
            task_id=str(payload["task_id"]), repo=str(payload["repo"]),
            baseline_ref=str(payload["baseline_ref"]), baseline_sha=str(payload["baseline_sha"]),
            objective=str(payload["objective"]), acceptance=tuple(str(v) for v in payload["acceptance"]),
            allowed_paths=tuple(str(v) for v in payload.get("allowed_paths", ())),
            forbidden_paths=tuple(str(v) for v in payload.get("forbidden_paths", ())),
            provider=str(payload.get("provider", "openai-codex")),
            model=str(payload.get("model", "gpt-5.6-luna")),
            reasoning=str(payload.get("reasoning", "medium")),
            max_turns=int(payload.get("max_turns", 50)),
            soft_budget_seconds=int(payload.get("soft_budget_seconds", 780)),
            hard_timeout_seconds=int(payload.get("hard_timeout_seconds", 900)),
            risk=str(payload.get("risk", "low")),
            task_type=str(payload.get("task_type", "maintenance")),
            affected_subsystem=str(payload.get("affected_subsystem", "")),
        )
        return self.execution_bridge.enqueue(
            task, project_id=str(payload["project_id"]), title=payload.get("title"),
            priority=str(payload.get("priority", "normal")),
            timeout_seconds=payload.get("timeout_seconds"),
            human_approved=_strict_bool(payload.get("human_approved", False), "human_approved"),
        )

    def agents(self) -> list[dict[str, Any]]:
        agents = [{"id": "hermes", "name": "Hermes", "status": "unknown"},
                  {"id": "verifier", "name": "Verifier", "status": "unknown"}]
        try:
            service = subprocess.run(["systemctl", "--user", "is-active", "arda-memory-worker.service"],
                                     capture_output=True, text=True, check=False).stdout.strip()
        except FileNotFoundError:
            service = "unknown"
        agents.append({"id": "memory-worker", "name": "Memory Worker", "status": service or "unknown"})
        return agents

    def memory(self) -> dict[str, Any]:
        roots = self._evidence_roots()
        pending = sum(len(list((root / "memory-jobs").glob("*.json"))) for root in roots)
        completed = sum(len(list((root / "memory-jobs").glob("*.done"))) for root in roots)
        refs = 0
        for episodes in (root / "episodes.sqlite3" for root in roots):
            if episodes.exists():
                with sqlite3.connect(f"file:{episodes.resolve()}?mode=ro", uri=True) as db:
                    refs += db.execute("SELECT COUNT(*) FROM episodes WHERE memory_refs <> '[]'").fetchone()[0]
        return {"health": "healthy" if pending == 0 else "pending", "pending_jobs": pending,
                "completed_jobs": completed, "episodes_with_memory_refs": refs}

    def evidence(self) -> dict[str, Any]:
        artifacts = []
        paths = [path for root in self._evidence_roots() for path in root.glob("artifacts/*/evidence.json")]
        for path in sorted(paths):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                artifacts.append({"task_id": payload.get("task", {}).get("task_id"),
                                 "result": payload.get("result"), "accepted_sha": payload.get("accepted_sha"),
                                 "path": str(path)})
            except (OSError, json.JSONDecodeError):
                artifacts.append({"path": str(path), "result": "unreadable"})
        return {"episodes": artifacts, "root": str(self.evidence_root)}

    @staticmethod
    def host() -> dict[str, Any]:
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
        memory = swap = None
        try:
            import psutil
            memory = {"total": psutil.virtual_memory().total, "used": psutil.virtual_memory().used,
                      "percent": psutil.virtual_memory().percent}
            swap = {"total": psutil.swap_memory().total, "used": psutil.swap_memory().used,
                    "percent": psutil.swap_memory().percent}
        except ImportError:
            pass
        return {"hostname": os.uname().nodename, "load_1m": load, "memory": memory, "swap": swap,
                "disk_free": shutil.disk_usage(Path.cwd()).free, "uptime_seconds": _uptime()}

    def approvals(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone()
            if not exists:
                return []
            return [dict(row) for row in db.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()]

    def update_organization_status(self, project_id: str, status: str, expected_version: int) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return dict(ProjectRegistry(db).update_status(project_id, status, expected_version=expected_version))
        finally:
            db.close()

    def update_task_status(self, task_id: str, status: str, expected_version: int) -> dict[str, Any]:
        db = bootstrap_runtime_database(self.db_path)
        try:
            return dict(TaskStore(db).update_status(task_id, status, expected_version=expected_version))
        finally:
            db.close()


def _uptime() -> float | None:
    try:
        return time.time() - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return None


class ControlCenterHandler(BaseHTTPRequestHandler):
    plane: ControlPlane
    MAX_BODY_BYTES = 1_048_576

    def _send(self, value: Any, status: int = 200, content_type: str = "application/json") -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode() if content_type == "application/json" else value.encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send({"error": "authorization required"}, 401)
            return
        path = urlparse(self.path).path
        routes = {"/api/status": self.plane.status, "/api/organizations": self.plane.organizations,
                  "/api/tasks": self.plane.tasks, "/api/jobs": self.plane.jobs,
                  "/api/agents": self.plane.agents,
                  "/api/memory": self.plane.memory, "/api/evidence": self.plane.evidence,
                  "/api/host": self.plane.host, "/api/approvals": self.plane.approvals}
        if path == "/health":
            self._send({"ok": True, "service": "arda-control-plane"})
            return
        if path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                self.wfile.write(f"event: snapshot\ndata: {json.dumps(self.plane.snapshot(), default=str)}\n\n".encode())
                self.wfile.flush()
                time.sleep(5)
            return
        if path.startswith("/api/organizations/"):
            parts = path.split("/")
            if len(parts) == 5 and parts[4] == "autopilot":
                try:
                    snapshot = self.plane.organization_snapshot(parts[3])
                    profile = snapshot["profile"]
                    self._send({"enabled": bool(profile.get("autopilot_enabled")),
                                "state": profile.get("autopilot_state", "OFF"),
                                "last_dispatch_at": profile.get("last_dispatch_at"),
                                "last_dispatch_reason": profile.get("last_dispatch_reason")})
                except KeyError:
                    self._send({"error": "organization unavailable"}, 404)
                except Exception:
                    self._send({"error": "control plane unavailable"}, 500)
                return
            if len(parts) == 4:
                try:
                    self._send(self.plane.organization_snapshot(parts[3]))
                except KeyError:
                    self._send({"error": "organization unavailable"}, 404)
                except Exception:
                    self._send({"error": "control plane unavailable"}, 500)
                return
            if len(parts) == 5 and parts[4] == "next-task":
                try:
                    self._send(self.plane.select_next_task(parts[3]))
                except KeyError:
                    self._send({"error": "organization unavailable"}, 404)
                except Exception:
                    self._send({"error": "control plane unavailable"}, 500)
                return
        if path == "/":
            self._send(_DASHBOARD_HTML, content_type="text/html")
            return
        handler = routes.get(path)
        if handler is None:
            self._send({"error": "not found"}, 404)
            return
        try:
            self._send(handler())
        except Exception:
            self._send({"error": "control plane unavailable"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.split("/")
        try:
            if not self._authorized():
                self._send({"error": "authorization required"}, 401)
                return
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 0 or content_length > self.MAX_BODY_BYTES:
                self._send({"error": "request body too large"}, 413)
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                self._send({"error": "application/json required"}, 415)
                return
            try:
                payload = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError:
                self._send({"error": "valid JSON required"}, 400)
                return
            if not isinstance(payload, dict):
                self._send({"error": "JSON object required"}, 400)
                return
            result = None
            if path == ["", "api", "tasks"]:
                result = self.plane.enqueue_task(payload)
            elif len(path) == 5 and path[1:3] == ["api", "organizations"] and path[4] == "profile":
                result = self.plane.update_organization_profile(path[3], payload)
            elif len(path) == 5 and path[1:3] == ["api", "organizations"] and path[4] == "objectives":
                result = self.plane.create_organization_objective(path[3], payload)
            elif len(path) == 5 and path[1:3] == ["api", "organizations"] and path[4] == "autopilot":
                result = self.plane.set_autopilot(path[3], _strict_bool(payload["enabled"], "enabled"))
            elif len(path) == 5 and path[1:3] == ["api", "organizations"] and path[4] == "dispatch":
                result = self.plane.dispatch_next_task(path[3])
            elif len(path) == 5 and path[1:3] == ["api", "organizations"] and path[4] == "tasks":
                result = self.plane.create_backlog_task(path[3], payload)
            elif len(path) == 5 and path[1:3] == ["api", "organizations"] and path[4] == "status":
                result = self.plane.update_organization_status(path[3], payload["status"], int(payload["expected_version"]))
            elif len(path) == 5 and path[1:3] == ["api", "tasks"] and path[4] == "status":
                result = self.plane.update_task_status(path[3], payload["status"], int(payload["expected_version"]))
            elif len(path) == 5 and path[1:3] == ["api", "jobs"] and path[4] == "cancel":
                result = self.plane.cancel_job(path[3])
            else:
                self._send({"error": "not found"}, 404)
                return
            self._send(result)
        except (KeyError, ValueError, TypeError):
            self._send({"error": "invalid control-plane request"}, 422)
        except sqlite3.Error:
            self._send({"error": "control plane unavailable"}, 500)
        except Exception:
            self._send({"error": "control plane unavailable"}, 500)

    def _authorized(self) -> bool:
        address = self.server.server_address[0]
        if address in {"127.0.0.1", "::1", "localhost"}:
            return True
        token = os.environ.get("ARDA_CONTROL_TOKEN")
        return bool(token and self.headers.get("Authorization", "") == f"Bearer {token}")

    def log_message(self, *_args: Any) -> None:
        return


_DASHBOARD_HTML = """<!doctype html><meta charset=utf-8><title>Arda Control Center</title>
<style>body{font:15px system-ui;margin:2rem;background:#0f172a;color:#e2e8f0}main{max-width:1100px;margin:auto}section{background:#1e293b;border-radius:14px;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;color:#cbd5e1}h1{color:#f8fafc}button{margin:.25rem;padding:.5rem 1rem;border:0;border-radius:8px}</style>
<main><h1>Arda Control Center <span id=online>●</span></h1><section><h2>System</h2><pre id=status>Loading…</pre></section><section><h2>Organizations</h2><div id=orgControls></div><pre id=orgs>Loading…</pre><pre id=orgDetail>Loading…</pre></section><section><h2>Tasks</h2><pre id=tasks>Loading…</pre></section><section><h2>Execution Jobs</h2><pre id=jobs>Loading…</pre></section><section><h2>Agents / Memory / Evidence</h2><pre id=ops>Loading…</pre></section></main>
<script>async function get(p){let r=await fetch(p);return r.json()}async function command(p,b){let r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}function renderJobs(items){jobs.replaceChildren();for(const item of items){const row=document.createElement('div'),details=document.createElement('pre');details.textContent=JSON.stringify(item,null,2);row.append(details);if(['queued','retry_wait','leased','running'].includes(item.status)){const button=document.createElement('button');button.textContent='Cancel job';button.onclick=()=>cancelJob(item.id);row.append(button)}jobs.append(row)}}async function refresh(){try{let [s,o,t,j,a,m,e]=await Promise.all(['/api/status','/api/organizations','/api/tasks','/api/jobs','/api/agents','/api/memory','/api/evidence'].map(get));status.textContent=JSON.stringify(s,null,2);orgs.textContent=JSON.stringify(o,null,2);if(o[0]){let detail=await get('/api/organizations/'+o[0].id);orgDetail.textContent=JSON.stringify(detail,null,2);let p=detail.profile;orgControls.innerHTML='<b>'+o[0].name+' · Autopilot '+(p.autopilot_state||'OFF')+'</b> <button onclick="toggleAuto(\''+o[0].id+'\','+(p.autopilot_enabled?'false':'true')+')">'+(p.autopilot_enabled?'Pause':'Start')+'</button><button onclick="dispatch(\''+o[0].id+'\')">Dispatch next</button>'}tasks.textContent=JSON.stringify(t,null,2);renderJobs(j);ops.textContent=JSON.stringify({agents:a,memory:m,evidence:e},null,2);online.textContent='● ONLINE'}catch(e){online.textContent='● OFFLINE'}}async function toggleAuto(id,enabled){await command('/api/organizations/'+id+'/autopilot',{enabled});refresh()}async function dispatch(id){await command('/api/organizations/'+id+'/dispatch',{});refresh()}async function cancelJob(id){await command('/api/jobs/'+encodeURIComponent(id)+'/cancel',{});refresh()}refresh();setInterval(refresh,5000);</script>"""


def serve(*, host: str = "127.0.0.1", port: int = 8080, db: str | Path | None = None) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"} and not os.environ.get("ARDA_CONTROL_TOKEN"):
        raise RuntimeError("ARDA_CONTROL_TOKEN is required for non-loopback Control Plane binding")
    plane = ControlPlane(db)
    class Handler(ControlCenterHandler):
        pass
    Handler.plane = plane
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arda Control Plane + Control Center")
    parser.add_argument("--host", default=os.environ.get("ARDA_CONTROL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARDA_CONTROL_PORT", "8080")))
    parser.add_argument("--db", type=Path, default=runtime_database_path())
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db=args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
