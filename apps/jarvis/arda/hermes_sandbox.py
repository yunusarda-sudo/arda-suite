"""Hermes-to-Podman execution boundary.

The MCP server deliberately exposes one command tool. The source tree is
read-only and the only writable host mount is a per-task disposable workspace.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import stat
import posixpath
import subprocess
import tempfile
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from arda.execution import ExecutionRequest, OutputLimits, PodmanRuntime, ResourceLimits
from arda.autonomous_work_loop import WorkResult, WorkTask
from arda.execution_bridge import ExecutionResult


_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


class SandboxMCPServer:
    """Minimal JSON-RPC MCP server with a fixed, per-execution workspace."""

    def __init__(self, *, runtime: PodmanRuntime, repository_snapshot: Path,
                 working_directory: Path, image: str, max_tool_calls: int = 100,
                 max_duration_seconds: int = 1800, max_command_bytes: int = 16_384,
                 timeout_seconds: int = 300, memory_mb: int = 2048,
                 disk_limit_bytes: int = 512 * 1024 * 1024, audit_path: Path | None = None,
                 cancel_event: threading.Event | None = None):
        if not _IMAGE.fullmatch(image):
            raise ValueError("image must be pinned by sha256 digest")
        if getattr(runtime, "image", image) != image:
            raise ValueError("runtime image does not match the pinned sandbox image")
        self.runtime = runtime
        self.repository_snapshot = Path(repository_snapshot).resolve(strict=True)
        self.working_directory = Path(working_directory).resolve(strict=True)
        if not self.repository_snapshot.is_dir() or not self.working_directory.is_dir():
            raise ValueError("source and workspace must be directories")
        if (self.repository_snapshot == self.working_directory
                or self.repository_snapshot in self.working_directory.parents
                or self.working_directory in self.repository_snapshot.parents):
            raise ValueError("source and workspace must be separate trees")
        if min(max_tool_calls, max_duration_seconds, max_command_bytes,
               timeout_seconds, memory_mb, disk_limit_bytes) <= 0:
            raise ValueError("sandbox limits must be positive")
        self.image = image
        self.max_tool_calls = max_tool_calls
        self.max_duration_seconds = max_duration_seconds
        self.max_command_bytes = max_command_bytes
        self.timeout_seconds = timeout_seconds
        self.memory_mb = memory_mb
        self.disk_limit_bytes = disk_limit_bytes
        self.audit_path = Path(audit_path) if audit_path is not None else None
        self.cancel_event = cancel_event or threading.Event()
        self.started = time.monotonic()
        self.calls = 0

    def handle_request(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = message.get("id")
        method = message.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "arda-sandbox", "version": "0.1.0"}}}
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{
                "name": "sandbox_exec", "description": "Run a command inside the isolated task workspace.",
                "inputSchema": {"type": "object", "properties": {
                    "command": {"type": "string", "maxLength": self.max_command_bytes},
                    "timeout_seconds": {"type": "integer", "minimum": 1,
                                         "maximum": self.timeout_seconds}},
                    "required": ["command"], "additionalProperties": False}}]}}
        if method == "tools/call":
            return self._call(request_id, message.get("params", {}))
        if request_id is None:
            return {}
        return {"jsonrpc": "2.0", "id": request_id, "error": {
            "code": -32601, "message": "method not found"}}

    def _call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._tool_result(request_id, "tool parameters must be an object", True)
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return self._tool_result(request_id, "tool arguments must be an object", True)
        command = args.get("command")
        error = None
        if params.get("name") != "sandbox_exec":
            error = "tool not allowed"
        elif not isinstance(command, str) or not command.strip():
            error = "command must be a non-empty string"
        elif len(command.encode("utf-8")) > self.max_command_bytes:
            error = "command is too large"
        elif set(args) - {"command", "timeout_seconds"}:
            error = "unsupported arguments"
        elif self.calls >= self.max_tool_calls or time.monotonic() - self.started >= self.max_duration_seconds:
            error = "sandbox session budget exhausted"
        if error:
            return self._tool_result(request_id, error, True)
        timeout = args.get("timeout_seconds", self.timeout_seconds)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            return self._tool_result(request_id, "timeout_seconds must be a positive integer", True)
        self.calls += 1
        request = ExecutionRequest(
            execution_id=f"hermes-{uuid.uuid4().hex}", repository_snapshot=self.repository_snapshot,
            working_directory=self.working_directory, disk_limit_bytes=self.disk_limit_bytes,
            require_rootless=True, argv=("/bin/sh", "-lc", command), cwd="/workspace/work",
            environment_allowlist={}, network_policy="none",
            resource_limits=ResourceLimits(pids=64, cpu_millis=1000, memory_mb=self.memory_mb,
                                           memory_required=True), timeout_seconds=min(timeout, self.timeout_seconds),
            output_limits=OutputLimits(stdout_bytes=65536, stderr_bytes=65536))
        try:
            run_cancellable = getattr(self.runtime, "run_cancellable", None)
            if callable(run_cancellable):
                result = run_cancellable(request, self.cancel_event)
            else:
                result = self.runtime.run(request)
        except Exception as exc:  # fail closed and avoid returning host paths
            self._audit_call(command, "runtime_error")
            return self._tool_result(request_id, f"sandbox execution failed: {type(exc).__name__}", True)
        outcome = "succeeded" if result.status == "succeeded" and result.cleanup_ok else "failed"
        self._audit_call(command, outcome)
        text = json.dumps({"status": result.status, "exit_code": result.exit_code,
                           "termination_reason": result.termination_reason,
                           "cleanup_ok": result.cleanup_ok, "stdout": result.stdout,
                           "stderr": result.stderr}, ensure_ascii=False)
        return self._tool_result(request_id, text, result.status != "succeeded" or not result.cleanup_ok)

    def _audit_call(self, command: str, outcome: str) -> None:
        if self.audit_path is None:
            return
        record = json.dumps({"tool": "sandbox_exec", "command_sha256": hashlib.sha256(
            command.encode("utf-8")).hexdigest(), "outcome": outcome}, separators=(",", ":")) + "\n"
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, record.encode("utf-8"))
        finally:
            os.close(fd)

    @staticmethod
    def _tool_result(request_id: Any, text: str, is_error: bool) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}


def serve_stdio(server: SandboxMCPServer, *, input_stream=None, output_stream=None) -> None:
    """Serve newline-delimited JSON-RPC. Protocol output is the only stdout."""
    source = input_stream or sys.stdin.buffer
    sink = output_stream or sys.stdout.buffer
    while True:
        line = source.readline(1_048_577)
        if not line:
            return
        if len(line) > 1_048_576 or not line.endswith(b"\n"):
            raise ValueError("MCP message exceeds 1 MiB")
        request = json.loads(line)
        if not isinstance(request, dict):
            raise ValueError("MCP request must be an object")
        response = server.handle_request(request)
        if response:
            sink.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            sink.flush()


def main() -> int:
    """Hermes MCP child entry point; paths and policy are supplied by trusted parent."""
    source = Path(os.environ["ARDA_SANDBOX_SOURCE"])
    work = Path(os.environ["ARDA_SANDBOX_WORK"])
    image = os.environ["ARDA_SANDBOX_IMAGE"]
    runtime = PodmanRuntime.from_host(image=image, podman_path=os.environ.get("ARDA_PODMAN_PATH", "podman"))
    cancel_event = threading.Event()
    cancel_path = os.environ.get("ARDA_SANDBOX_CANCEL_FILE")
    watcher_stop = threading.Event()
    watcher = None
    if cancel_path:
        def watch_cancel_file() -> None:
            while not watcher_stop.wait(0.1):
                try:
                    if Path(cancel_path).is_file():
                        cancel_event.set()
                        return
                except OSError:
                    cancel_event.set()
                    return
        watcher = threading.Thread(target=watch_cancel_file, name="arda-cancel-watch", daemon=True)
        watcher.start()
    def request_cancel(_signum, _frame) -> None:
        cancel_event.set()
    signal.signal(signal.SIGTERM, request_cancel)
    signal.signal(signal.SIGINT, request_cancel)
    server = SandboxMCPServer(runtime=runtime, repository_snapshot=source, working_directory=work,
                              image=image,
                              max_tool_calls=int(os.environ.get("ARDA_SANDBOX_MAX_CALLS", "100")),
                              max_duration_seconds=int(os.environ.get("ARDA_SANDBOX_MAX_SECONDS", "1800")),
                              max_command_bytes=int(os.environ.get("ARDA_SANDBOX_MAX_COMMAND_BYTES", "16384")),
                              timeout_seconds=int(os.environ.get("ARDA_SANDBOX_COMMAND_TIMEOUT", "300")),
                              memory_mb=int(os.environ.get("ARDA_SANDBOX_MEMORY_MB", "2048")),
                              disk_limit_bytes=int(os.environ.get("ARDA_SANDBOX_DISK_BYTES", str(512*1024*1024))),
                              audit_path=Path(os.environ["ARDA_SANDBOX_AUDIT"]) if os.environ.get("ARDA_SANDBOX_AUDIT") else None,
                              cancel_event=cancel_event)
    try:
        serve_stdio(server)
    finally:
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


class HermesPodmanTaskExecutor:
    """Run a Hermes coding turn with one sandbox-only MCP tool and Core-owned checks."""

    def __init__(self, *, runtime: PodmanRuntime, image: str, hermes_cli: str,
                 evaluator_commands: Mapping[str, Sequence[Sequence[str]]],
                 provider_env_keys: Sequence[str], podman_path: str = "podman",
                 memory_mb: int = 2048, disk_limit_bytes: int = 512 * 1024 * 1024):
        if not _IMAGE.fullmatch(image):
            raise ValueError("image must be pinned by sha256 digest")
        self.runtime = runtime
        self.image = image
        self.hermes_cli = hermes_cli
        self.podman_path = podman_path
        self.evaluator_commands = {str(key): tuple(tuple(str(x) for x in command) for command in value)
                                   for key, value in evaluator_commands.items()}
        self.provider_env_keys = tuple(provider_env_keys)
        if any(not key or "=" in key for key in self.provider_env_keys):
            raise ValueError("provider environment key names are invalid")
        reserved = {"PATH", "HOME", "HERMES_HOME", "PYTHONPATH", "XDG_RUNTIME_DIR", "TMPDIR",
                    "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "CONTAINERS_CONF", "CONTAINERS_CONF_OVERRIDE"}
        if any(key in reserved or key.startswith(("ARDA_", "GIT_", "PODMAN_", "CONTAINERS_"))
               for key in self.provider_env_keys):
            raise ValueError("provider environment allowlist contains a runtime control variable")
        if memory_mb <= 0 or disk_limit_bytes <= 0:
            raise ValueError("runtime limits must be positive")
        self.memory_mb = memory_mb
        self.disk_limit_bytes = disk_limit_bytes

    def execute(self, task: WorkTask) -> ExecutionResult:
        return self.execute_cancellable(task, threading.Event())

    def execute_cancellable(self, task: WorkTask, cancel_event: threading.Event) -> ExecutionResult:
        task.validate()
        if cancel_event.is_set():
            return ExecutionResult(WorkResult.INVALID.value, "Core lease was lost", evidence={})
        if not task.allowed_paths:
            return ExecutionResult(WorkResult.INVALID.value, "task has no allowed_paths", evidence={})
        commands = self.evaluator_commands.get(task.task_type)
        if not commands or any(not command or any(not part for part in command) for command in commands):
            return ExecutionResult(WorkResult.INVALID.value,
                                   f"no valid independent evaluator configured for task_type={task.task_type}", evidence={})
        if self.runtime.capabilities.rootless is not True or not self.runtime.capabilities.memory_limit:
            return ExecutionResult(WorkResult.INVALID.value, "rootless Podman and memory enforcement are required", evidence={})
        missing = [key for key in self.provider_env_keys if not os.environ.get(key)]
        if missing:
            return ExecutionResult(WorkResult.INVALID.value,
                                   "required Hermes provider credentials are unavailable", evidence={"missing_keys": missing})
        repo = Path(task.repo).expanduser().resolve()
        if not repo.is_dir():
            return ExecutionResult(WorkResult.INVALID.value, "task repository is unavailable", evidence={})
        try:
            actual = self._git(repo, "rev-parse", task.baseline_ref).decode().strip()
        except (OSError, subprocess.SubprocessError):
            return ExecutionResult(WorkResult.INVALID.value, "baseline ref could not be resolved", evidence={})
        if actual != task.baseline_sha:
            return ExecutionResult(WorkResult.INVALID.value, "task baseline no longer matches its ref", evidence={})

        started = time.monotonic()
        root = Path(tempfile.mkdtemp(prefix="arda-hermes-task-"))
        try:
            source, work = root / "source", root / "work"
            source.mkdir(); work.mkdir()
            self._extract_snapshot(repo, task.baseline_sha, source)
            shutil.copytree(source, work, dirs_exist_ok=True, symlinks=True)
            self._init_disposable_git(work, task)
            audit_path = root / "sandbox-audit.jsonl"
            hermes = self._run_hermes(task, root, source, work, audit_path, cancel_event)
            audit = self._read_audit(audit_path)
            if cancel_event.is_set():
                return ExecutionResult(WorkResult.INVALID.value, "Core lease was lost",
                                       evidence={"hermes": hermes, "sandbox_audit": audit})
            if hermes["error"]:
                return ExecutionResult(WorkResult.FAIL.value, hermes["error"], evidence={"hermes": hermes, "sandbox_audit": audit})
            if cancel_event.is_set():
                return ExecutionResult(WorkResult.INVALID.value, "Core lease was lost", evidence={"hermes": hermes})
            if not audit or not any(record.get("outcome") == "succeeded" for record in audit):
                return ExecutionResult(WorkResult.INVALID.value, "Hermes completed without invoking the sandbox tool",
                                      evidence={"hermes": hermes, "sandbox_audit": audit})
            changed = self._changed_paths(work)
            if not changed:
                return ExecutionResult(WorkResult.FAIL.value, "Hermes produced no candidate changes",
                                       evidence={"hermes": hermes, "sandbox_audit": audit})
            scope_error = self._scope_error(task, changed)
            if scope_error:
                return ExecutionResult(WorkResult.FAIL.value, scope_error,
                                       evidence={"hermes": hermes, "sandbox_audit": audit, "changed_paths": changed})
            before_evaluation = self._workspace_digest(work)
            evaluation = self._evaluate(task, source, work, commands,
                                        remaining_seconds=task.hard_timeout_seconds - (time.monotonic() - started),
                                        cancel_event=cancel_event)
            if cancel_event.is_set():
                return ExecutionResult(WorkResult.INVALID.value, "Core lease was lost", evidence={"hermes": hermes})
            if before_evaluation != self._workspace_digest(work):
                return ExecutionResult(WorkResult.INVALID.value, "independent evaluator modified the candidate workspace",
                                       evidence={"hermes": hermes, "sandbox_audit": audit,
                                                 "changed_paths": changed, "evaluation": evaluation})
            if cancel_event.is_set():
                return ExecutionResult(WorkResult.INVALID.value, "Core lease was lost", evidence={"hermes": hermes})
            if not evaluation["passed"]:
                return ExecutionResult(WorkResult.FAIL.value, "independent evaluator failed",
                                       evidence={"hermes": hermes, "sandbox_audit": audit,
                                                 "changed_paths": changed, "evaluation": evaluation})
            # Only trusted code writes candidate Git objects; no worker ref is imported.
            candidate = self._candidate_commit(repo, task, work)
            if cancel_event.is_set():
                return ExecutionResult(WorkResult.INVALID.value, "Core lease was lost",
                                       evidence={"candidate_sha": candidate})
            return ExecutionResult(WorkResult.PASS.value, "sandboxed Hermes and independent evaluation passed",
                                   candidate_sha=candidate,
                                   evidence={"task_id": task.task_id, "contract_hash": task.contract_hash,
                                             "hermes": hermes, "sandbox_audit": audit,
                                             "changed_paths": changed, "evaluation": evaluation,
                                             "candidate_sha": candidate, "baseline_sha": task.baseline_sha})
        except Exception as exc:  # errors can never become a PASS
            return ExecutionResult(WorkResult.INVALID.value, f"sandbox task failed: {type(exc).__name__}", evidence={})
        finally:
            shutil.rmtree(root)

    def _run_hermes(self, task: WorkTask, root: Path, source: Path, work: Path, audit_path: Path,
                    cancel_event: threading.Event | None = None) -> dict[str, Any]:
        cancel_event = cancel_event or threading.Event()
        hermes_home = root / "hermes-home"
        hermes_home.mkdir(mode=0o700)
        query_file = root / "task.txt"
        query_file.write_text(self._prompt(task), encoding="utf-8")
        mcp_env = {"ARDA_SANDBOX_SOURCE": str(source), "ARDA_SANDBOX_WORK": str(work),
                   "ARDA_SANDBOX_IMAGE": self.image, "ARDA_SANDBOX_AUDIT": str(audit_path),
                   "ARDA_SANDBOX_CANCEL_FILE": str(root / "cancel"),
                   "ARDA_PODMAN_PATH": self.podman_path, "HOME": os.environ.get("HOME", str(Path.home())),
                   "ARDA_SANDBOX_MAX_SECONDS": str(min(task.hard_timeout_seconds, 1800)),
                   "ARDA_SANDBOX_MAX_CALLS": str(min(max(task.max_turns * 4, 1), 200)),
                   "PATH": os.environ.get("PATH", ""),
                   "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        if os.environ.get("XDG_RUNTIME_DIR"):
            mcp_env["XDG_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
        config = {"mcp_servers": {"arda-sandbox": {
            "command": sys.executable, "args": ["-m", "arda.hermes_sandbox"], "env": mcp_env,
            "enabled": True, "timeout": min(task.hard_timeout_seconds, 300),
            "tools": {"include": ["sandbox_exec"], "resources": False, "prompts": False}}},
            "platform_toolsets": {"cli": ["arda-sandbox"]},
            "agent": {"max_turns": task.max_turns,
                      "disabled_toolsets": ["hermes-cli", "terminal", "file", "web", "browser",
                                            "memory", "skills", "vision", "image_gen", "todo", "tts",
                                            "delegation", "code_execution", "cronjob", "session_search",
                                            "clarify", "computer_use", "homeassistant", "kanban"]}}
        (hermes_home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(hermes_home), "HERMES_HOME": str(hermes_home),
               "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        for key in self.provider_env_keys:
            env[key] = os.environ[key]
        argv = [self.hermes_cli, "chat", "--provider", task.provider, "--model", task.model,
                "--query-file", str(query_file), "--oneshot", "--format", "stream-json",
                "--max-turns", str(task.max_turns), "--source", "arda-worker"]
        completed = self._run_bounded(argv, cwd=work, env=env,
                                      timeout_seconds=task.hard_timeout_seconds,
                                      stdout_limit=2 * 1024 * 1024, stderr_limit=64 * 1024,
                                      cancel_event=cancel_event, cancel_file=root / "cancel")
        if completed["termination_reason"]:
            return {"error": f"Hermes {completed['termination_reason']}", "exit_code": completed["exit_code"],
                    "tool_calls": 0, "stdout_sha256": completed["stdout_sha256"],
                    "stderr_sha256": completed["stderr_sha256"]}
        events = []
        tool_calls = 0
        for line in completed["stdout"].splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "tool_use" and str(event.get("name", "")).endswith("sandbox_exec"):
                tool_calls += 1
            if event.get("type") in {"tool_use", "tool_result", "result"}:
                events.append({"type": event.get("type"), "name": event.get("name"),
                               "is_error": event.get("is_error"), "exit_code": event.get("exit_code")})
        return {"error": None if completed["exit_code"] == 0 else "Hermes exited unsuccessfully",
                "exit_code": completed["exit_code"], "tool_calls": tool_calls,
                "stdout_sha256": completed["stdout_sha256"], "stderr_sha256": completed["stderr_sha256"],
                "events": events[-500:]}

    @staticmethod
    def _run_bounded(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str],
                     timeout_seconds: float, stdout_limit: int, stderr_limit: int,
                     cancel_event: threading.Event | None = None,
                     cancel_file: Path | None = None) -> dict[str, Any]:
        process = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        limits = {process.stdout: stdout_limit, process.stderr: stderr_limit}
        buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
        digests = {process.stdout: hashlib.sha256(), process.stderr: hashlib.sha256()}
        selector = selectors.DefaultSelector()
        for stream in limits:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        reason = None
        while selector.get_map():
            if cancel_event is not None and cancel_event.is_set():
                reason = "Core lease lost"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "hard timeout"
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    selector.unregister(stream)
                    continue
                digests[stream].update(chunk)
                if len(buffers[stream]) + len(chunk) > limits[stream]:
                    reason = "output limit"
                    break
                buffers[stream].extend(chunk)
            if reason:
                break
            if process.poll() is not None and not selector.get_map():
                break
        if reason:
            if reason == "Core lease lost" and cancel_file is not None:
                fd = os.open(cancel_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=10 if reason == "Core lease lost" else 1 if reason else max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        selector.close()
        for stream in limits:
            stream.close()
        return {"exit_code": process.returncode, "termination_reason": reason,
                "stdout": bytes(buffers[process.stdout]).decode("utf-8", "replace"),
                "stdout_sha256": digests[process.stdout].hexdigest(),
                "stderr_sha256": digests[process.stderr].hexdigest()}

    @staticmethod
    def _prompt(task: WorkTask) -> str:
        return ("Work only in the repository available through the sandbox_exec tool. "
                "All shell commands must be executed using sandbox_exec; no other tools are authorized. "
                "Do not claim success unless the independent acceptance checks can pass.\n\n"
                f"Objective: {task.objective}\nAcceptance criteria:\n" +
                "\n".join(f"- {item}" for item in task.acceptance) +
                "\nAllowed paths:\n" + "\n".join(f"- {item}" for item in task.allowed_paths) +
                "\nForbidden paths:\n" + "\n".join(f"- {item}" for item in task.forbidden_paths))

    def _evaluate(self, task: WorkTask, source: Path, work: Path, commands: Sequence[Sequence[str]],
                  *, remaining_seconds: float, cancel_event: threading.Event | None = None) -> dict[str, Any]:
        cancel_event = cancel_event or threading.Event()
        checks = []
        if len(commands) > 20 or remaining_seconds <= 0:
            return {"passed": False, "checks": [], "error": "evaluation budget exhausted"}
        deadline = time.monotonic() + remaining_seconds
        for index, command in enumerate(commands):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"passed": False, "checks": checks, "error": "evaluation timeout budget exhausted"}
            request = ExecutionRequest(
                execution_id=f"eval-{task.task_id[:40]}-{index}", repository_snapshot=source,
                working_directory=work, argv=tuple(command), cwd="/workspace/work", require_rootless=True,
                disk_limit_bytes=self.disk_limit_bytes, environment_allowlist={}, network_policy="none",
                resource_limits=ResourceLimits(pids=64, cpu_millis=1000, memory_mb=self.memory_mb, memory_required=True),
                timeout_seconds=min(remaining, 300), output_limits=OutputLimits())
            try:
                run_cancellable = getattr(self.runtime, "run_cancellable", None)
                result = (run_cancellable(request, cancel_event) if callable(run_cancellable)
                          else self.runtime.run(request))
            except Exception as exc:
                return {"passed": False, "checks": checks, "error": type(exc).__name__}
            checks.append({"status": result.status, "exit_code": result.exit_code,
                           "termination_reason": result.termination_reason, "cleanup_ok": result.cleanup_ok,
                           "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                           "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest()})
            if result.status != "succeeded" or not result.cleanup_ok:
                return {"passed": False, "checks": checks, "error": "evaluation failed"}
        return {"passed": bool(checks), "checks": checks}

    def _candidate_commit(self, repo: Path, task: WorkTask, work: Path) -> str:
        index_path = work.parent / "trusted-index"
        env = self._git_env()
        env.update({"GIT_INDEX_FILE": str(index_path), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "ARDA Worker",
                    "GIT_AUTHOR_EMAIL": "arda-worker@localhost", "GIT_COMMITTER_NAME": "ARDA Worker",
                    "GIT_COMMITTER_EMAIL": "arda-worker@localhost"})
        subprocess.run(["git", "-C", str(repo), "read-tree", task.baseline_sha], env=env, check=True,
                       capture_output=True, timeout=30)
        current = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], env=env, check=True,
                                 capture_output=True, timeout=30).stdout.split(b"\0")
        files, dirs = self._walk_regular_files(work)
        current_set = {item.decode("utf-8", "strict") for item in current if item}
        final_set = set(files)
        for path in sorted(current_set - final_set):
            subprocess.run(["git", "-C", str(repo), "update-index", "--force-remove", "--", path],
                           env=env, check=True, capture_output=True, timeout=30)
        for relative, path in files.items():
            mode = {0o100644: "100644", 0o100755: "100755", 0o120000: "120000"}[self._workspace_mode(path)]
            oid = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                                 input=self._workspace_file_bytes(path),
                                 env=env, check=True, capture_output=True, timeout=30).stdout.decode().strip()
            subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo",
                            f"{mode},{oid},{relative}"], env=env, check=True, capture_output=True, timeout=30)
        tree = subprocess.run(["git", "-C", str(repo), "write-tree"], env=env, check=True,
                              capture_output=True, timeout=30).stdout.decode().strip()
        commit = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", task.baseline_sha,
                                 "-m", f"ARDA candidate: {task.task_id}"], env=env, check=True,
                                capture_output=True, timeout=30).stdout.decode().strip()
        return commit

    @classmethod
    def _walk_regular_files(cls, root: Path) -> tuple[dict[str, Path], set[str]]:
        files: dict[str, Path] = {}
        directories: set[str] = set()
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(current)
            if base == root and ".git" in dirnames:
                dirnames.remove(".git")
            for name in list(dirnames):
                path = base / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    dirnames.remove(name)
                    cls._validate_workspace_link(root, path)
                    files[relative] = path
                else:
                    directories.add(relative)
            for name in filenames:
                path = base / name
                relative = path.relative_to(root).as_posix()
                if relative == ".git" or relative.startswith(".git/"):
                    continue
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    cls._validate_workspace_link(root, path)
                    files[relative] = path
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("worker produced a non-regular file")
                if "\0" in relative or relative.startswith("/") or ".." in Path(relative).parts:
                    raise ValueError("worker produced an unsafe path")
                files[relative] = path
        return files, directories

    def _changed_paths(self, work: Path) -> tuple[str, ...]:
        files, _ = self._walk_regular_files(work)
        # Compare candidate content and executable mode with the frozen source.
        baseline, _ = self._walk_regular_files(work.parent / "source")
        paths = set(files) | set(baseline)
        return tuple(sorted(path for path in paths if path not in files or path not in baseline
                            or self._workspace_file_bytes(files[path]) != self._workspace_file_bytes(baseline[path])
                            or self._workspace_mode(files[path]) != self._workspace_mode(baseline[path])))

    @classmethod
    def _workspace_digest(cls, work: Path) -> str:
        files, _ = cls._walk_regular_files(work)
        digest = hashlib.sha256()
        for relative, path in sorted(files.items()):
            info = os.stat(path, follow_symlinks=False)
            digest.update(relative.encode("utf-8")); digest.update(b"\0")
            digest.update(str(cls._workspace_mode(path)).encode("ascii")); digest.update(b"\0")
            digest.update(hashlib.sha256(cls._workspace_file_bytes(path)).digest())
        return digest.hexdigest()

    @staticmethod
    def _scope_error(task: WorkTask, changed: Sequence[str]) -> str | None:
        if any(any(fnmatch.fnmatch(path, pattern) for pattern in task.forbidden_paths) for path in changed):
            return "forbidden path changed"
        if any(not any(fnmatch.fnmatch(path, pattern) for pattern in task.allowed_paths) for path in changed):
            return "unrelated path changed"
        return None

    def _extract_snapshot(self, repo: Path, baseline: str, destination: Path) -> None:
        """Materialize exact Git blobs without archive filters or shared metadata."""
        listing = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", baseline],
                                 env=self._git_env(), check=True, capture_output=True, timeout=60).stdout
        entries = listing.split(b"\0")
        if len(entries) > 100_001:
            raise ValueError("baseline has too many paths")
        total_bytes = 0
        for record in entries:
            if not record:
                continue
            metadata, separator, raw_path = record.partition(b"\t")
            if not separator:
                raise ValueError("invalid Git tree record")
            fields = metadata.decode("ascii").split()
            if len(fields) != 3:
                raise ValueError("invalid Git tree metadata")
            mode, object_type, oid = fields
            relative = raw_path.decode("utf-8", "strict")
            rel_path = Path(relative)
            if rel_path.is_absolute() or ".." in rel_path.parts or ".git" in rel_path.parts:
                raise ValueError("baseline contains an unsafe path")
            target = destination / rel_path
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("baseline path escapes the source snapshot")
            if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
                raise ValueError("baseline contains an unsupported Git entry")
            size = int(subprocess.run(["git", "-C", str(repo), "cat-file", "-s", oid], env=self._git_env(),
                                      check=True, capture_output=True, timeout=30).stdout)
            total_bytes += size
            if total_bytes > self.disk_limit_bytes:
                raise ValueError("baseline exceeds configured disk budget")
            content = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", oid], env=self._git_env(),
                                     check=True, capture_output=True, timeout=60).stdout
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "120000":
                link = os.fsdecode(content)
                self._validate_link_target(relative, link)
                target.symlink_to(link)
            else:
                target.write_bytes(content)
                target.chmod(0o755 if mode == "100755" else 0o644)

    @staticmethod
    def _validate_link_target(relative: str, link: str) -> None:
        if not link or os.path.isabs(link) or "\\" in link:
            raise ValueError("workspace symlink target is absolute or invalid")
        parent = posixpath.dirname(relative)
        normalized = posixpath.normpath(posixpath.join(parent, link))
        if normalized == ".." or normalized.startswith("../"):
            raise ValueError("workspace symlink escapes its root")

    @classmethod
    def _validate_workspace_link(cls, root: Path, path: Path) -> None:
        relative = path.relative_to(root).as_posix()
        cls._validate_link_target(relative, os.readlink(path))

    @staticmethod
    def _workspace_file_bytes(path: Path) -> bytes:
        return os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()

    @staticmethod
    def _workspace_mode(path: Path) -> int:
        if path.is_symlink():
            return 0o120000
        return 0o100755 if os.stat(path, follow_symlinks=False).st_mode & 0o111 else 0o100644

    def _init_disposable_git(self, work: Path, task: WorkTask) -> None:
        env = self._git_env()
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "ARDA Workspace", "GIT_AUTHOR_EMAIL": "arda@localhost",
                    "GIT_COMMITTER_NAME": "ARDA Workspace", "GIT_COMMITTER_EMAIL": "arda@localhost"})
        subprocess.run(["git", "init", "-q"], cwd=work, env=env, check=True, capture_output=True)
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "add", "-A"], cwd=work, env=env,
                       check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", f"baseline {task.baseline_sha}"],
                       cwd=work, env=env, check=True, capture_output=True, timeout=60)

    @staticmethod
    def _read_audit(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines()[:1000]:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                result.append(value)
        return result

    @staticmethod
    def _git_env() -> dict[str, str]:
        # Strip ambient Git overrides such as GIT_DIR, GIT_INDEX_FILE and
        # GIT_OBJECT_DIRECTORY before trusted candidate creation.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if os.environ.get(key)}
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        return env

    @staticmethod
    def _git(repo: Path, *args: str) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *args], env=HermesPodmanTaskExecutor._git_env(),
                              check=True, capture_output=True, timeout=30).stdout


def build_executor() -> HermesPodmanTaskExecutor:
    """Bridge factory configured only by explicit deployment environment."""
    image = os.environ["ARDA_SANDBOX_IMAGE"]
    evaluator_commands = json.loads(os.environ["ARDA_SANDBOX_EVALUATOR_COMMANDS"])
    provider_keys = json.loads(os.environ.get("ARDA_HERMES_PROVIDER_ENV_KEYS", "[]"))
    if not isinstance(provider_keys, list) or not provider_keys:
        raise ValueError("ARDA_HERMES_PROVIDER_ENV_KEYS must explicitly list provider credentials")
    podman_path = os.environ.get("ARDA_PODMAN_PATH", "podman")
    return HermesPodmanTaskExecutor(
        runtime=PodmanRuntime.from_host(image=image, podman_path=podman_path), image=image,
        hermes_cli=os.environ.get("ARDA_HERMES_CLI", "hermes"),
        evaluator_commands=evaluator_commands, provider_env_keys=provider_keys, podman_path=podman_path,
        memory_mb=int(os.environ.get("ARDA_SANDBOX_MEMORY_MB", "2048")),
        disk_limit_bytes=int(os.environ.get("ARDA_SANDBOX_DISK_BYTES", str(512*1024*1024))))
