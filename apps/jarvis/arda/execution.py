"""M2.4B execution contract and rootless Podman runtime adapter."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


class ExecutionContractError(ValueError):
    """The request violates the v0.1 execution contract."""


class CapabilityMismatchError(ExecutionContractError):
    """The host cannot enforce a required capability."""


_DIGEST_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class MountSpec:
    source: Path
    target: str
    read_only: bool = True


@dataclass(frozen=True)
class ResourceLimits:
    pids: int | None = 32
    cpu_millis: int | None = 100
    memory_mb: int | None = None
    memory_required: bool = False

    def validate(self) -> None:
        for name, value in (("pids", self.pids), ("cpu_millis", self.cpu_millis), ("memory_mb", self.memory_mb)):
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ExecutionContractError(f"{name} must be a positive integer")
        if self.memory_required and self.memory_mb is None:
            raise ExecutionContractError("memory_required needs memory_mb")


@dataclass(frozen=True)
class OutputLimits:
    stdout_bytes: int = 65536
    stderr_bytes: int = 65536

    def validate(self) -> None:
        if self.stdout_bytes <= 0 or self.stderr_bytes <= 0:
            raise ExecutionContractError("output limits must be positive")


@dataclass(frozen=True)
class RuntimeCapabilities:
    rootless: bool | None = None
    network_isolation: bool = True
    read_only_mounts: bool = True
    writable_scratch: bool = True
    pid_limit: bool = True
    cpu_limit: bool = True
    memory_limit: bool = False
    seccomp: bool = True
    user_namespace: bool = True
    artifact_export: bool = True
    backend: str = "podman"
    backend_version: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rootless": self.rootless,
            "network_isolation": self.network_isolation,
            "read_only_mounts": self.read_only_mounts,
            "writable_scratch": self.writable_scratch,
            "pid_limit": self.pid_limit,
            "cpu_limit": self.cpu_limit,
            "memory_limit": self.memory_limit,
            "seccomp": self.seccomp,
            "user_namespace": self.user_namespace,
            "artifact_export": self.artifact_export,
            "backend": self.backend,
            "backend_version": self.backend_version,
        }


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str
    repository_snapshot: Path
    argv: tuple[str, ...]
    working_directory: Path | None = None
    disk_limit_bytes: int | None = None
    require_rootless: bool = False
    cwd: str = "/workspace/source"
    environment_allowlist: Mapping[str, str] = field(default_factory=dict)
    mounts: tuple[MountSpec, ...] = ()
    network_policy: str = "none"
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    timeout_seconds: float = 30.0
    output_limits: OutputLimits = field(default_factory=OutputLimits)
    expected_artifacts: tuple[str, ...] = ()

    def validate(self) -> None:
        if (
            not self.execution_id
            or not isinstance(self.execution_id, str)
            or len(self.execution_id) > 128
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for char in self.execution_id)
        ):
            raise ExecutionContractError("execution_id is required")
        if not self.argv or not all(isinstance(part, str) and part for part in self.argv):
            raise ExecutionContractError("argv must be a non-empty sequence of strings")
        if any(token in self.argv[0] for token in ("&&", ";", "|", ">", "<", "\n")):
            raise ExecutionContractError("argv must not contain a shell command string")
        if self.network_policy != "none":
            raise ExecutionContractError("only network_policy=none is supported in v0.1")
        if self.cwd not in {"/workspace/source", "/workspace/work"}:
            raise ExecutionContractError("cwd must be /workspace/source or /workspace/work")
        if not self.repository_snapshot.is_dir():
            raise ExecutionContractError("repository_snapshot must be an existing directory")
        if self.working_directory is not None:
            if not self.working_directory.is_dir() or self.working_directory.is_symlink():
                raise ExecutionContractError("working_directory must be an existing real directory")
            source = self.repository_snapshot.resolve()
            work = self.working_directory.resolve()
            if source == work or source in work.parents or work in source.parents:
                raise ExecutionContractError("source and writable workspace must be separate trees")
        if self.disk_limit_bytes is not None and self.disk_limit_bytes <= 0:
            raise ExecutionContractError("disk_limit_bytes must be positive")
        if self.timeout_seconds <= 0:
            raise ExecutionContractError("timeout_seconds must be positive")
        if any(not key or "=" in key for key in self.environment_allowlist):
            raise ExecutionContractError("environment keys must be non-empty and not contain '='")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.environment_allowlist.items()):
            raise ExecutionContractError("environment allowlist must contain strings")
        if any(
            not path or Path(path).is_absolute() or ".." in PurePosixPath(path).parts
            for path in self.expected_artifacts
        ):
            raise ExecutionContractError("expected_artifacts must be relative paths")
        self.resource_limits.validate()
        self.output_limits.validate()
        for mount in self.mounts:
            target = PurePosixPath(mount.target)
            if (
                not mount.source.is_dir()
                or not target.is_absolute()
                or not mount.target.startswith("/workspace/")
                or ".." in target.parts
            ):
                raise ExecutionContractError("mounts must be existing directories below /workspace")
        destinations = {mount.target for mount in self.mounts}
        if len(destinations) != len(self.mounts) or {"/workspace/source", "/workspace/work"} & destinations:
            raise ExecutionContractError("mount destination collides with a reserved workspace mount")


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    status: str
    backend: str
    backend_version: str
    requested: dict[str, Any]
    capabilities: dict[str, Any]
    enforcement: dict[str, str]
    exit_code: int | None
    termination_reason: str | None
    stdout: str
    stderr: str
    duration_seconds: float
    artifacts: dict[str, str]
    cleanup_ok: bool


class PodmanRuntime:
    """Execute one validated request in a rootless Podman container.

    Core lease/fencing and artifact publication deliberately stay outside this
    adapter. This class only returns bounded execution evidence.
    """

    def __init__(
        self,
        *,
        image: str,
        podman_path: str = "podman",
        capabilities: RuntimeCapabilities | None = None,
        process_factory=subprocess.Popen,
    ):
        self.image = image
        self.podman_path = podman_path
        self.capabilities = capabilities or RuntimeCapabilities()
        self.process_factory = process_factory
        if not _DIGEST_IMAGE.fullmatch(image):
            raise ExecutionContractError("image must be pinned by a sha256 digest")

    @staticmethod
    def _podman_env() -> dict[str, str]:
        """Pass only local runtime context to the trusted Podman client."""
        allowed = ("PATH", "HOME", "XDG_RUNTIME_DIR", "TMPDIR")
        env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
        # Ignore per-user/system containers.conf defaults that could add mounts.
        env["CONTAINERS_CONF"] = os.devnull
        env["CONTAINERS_CONF_OVERRIDE"] = os.devnull
        return env

    @classmethod
    def from_host(cls, *, image: str, podman_path: str = "podman"):
        """Create a runtime from a read-only host capability snapshot."""
        version = "unknown"
        try:
            completed = subprocess.run(
                [podman_path, "version", "--format", "{{.Client.Version}}"],
                check=False,
                capture_output=True,
                text=True,
                env=cls._podman_env(),
                timeout=5,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                version = completed.stdout.strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            pass
        rootless: bool | None = None
        controllers: list[str] = []
        try:
            info = subprocess.run(
                [podman_path, "info", "--format", "json"], check=False,
                capture_output=True, text=True, env=cls._podman_env(), timeout=5,
            )
            if info.returncode == 0:
                host = json.loads(info.stdout).get("host", {})
                security = host.get("security", {})
                if isinstance(security.get("rootless"), bool):
                    rootless = security["rootless"]
                raw_controllers = host.get("cgroupControllers", [])
                if isinstance(raw_controllers, list):
                    controllers = [str(value) for value in raw_controllers]
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            pass
        capabilities = RuntimeCapabilities(
            rootless=rootless,
            memory_limit="memory" in controllers,
            backend_version=version,
        )
        return cls(image=image, podman_path=podman_path, capabilities=capabilities)

    def build_argv(self, request: ExecutionRequest, scratch: Path, container_name: str | None = None) -> list[str]:
        request.validate()
        limits = request.resource_limits
        args = [
            self.podman_path, "run", "--rm", "--pull=never", "--network=none", "--read-only",
            "--userns=private", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
            "--volume", f"{request.repository_snapshot}:/workspace/source:ro",
            "--volume", f"{request.working_directory or scratch}:/workspace/work:rw",
            "--workdir", request.cwd,
        ]
        if request.disk_limit_bytes is not None:
            # Per-file kernel guard plus aggregate monitor in _collect.
            args.append(f"--ulimit=fsize={request.disk_limit_bytes}:{request.disk_limit_bytes}")
        if limits.pids is not None:
            args.append(f"--pids-limit={limits.pids}")
        if limits.cpu_millis is not None:
            args.append(f"--cpus={limits.cpu_millis / 1000:g}")
        if limits.memory_mb is not None and self.capabilities.memory_limit:
            args.append(f"--memory={limits.memory_mb}m")
        for key, value in sorted(request.environment_allowlist.items()):
            args.extend(("--env", f"{key}={value}"))
        for mount in request.mounts:
            mode = "ro" if mount.read_only else "rw"
            args.extend(("--volume", f"{mount.source}:{mount.target}:{mode}"))
        if container_name is not None:
            args.extend(("--name", container_name))
        args.extend((self.image, *request.argv))
        return args

    def run(self, request: ExecutionRequest, *, cancel_event: threading.Event | None = None) -> ExecutionResult:
        request.validate()
        if request.require_rootless and self.capabilities.rootless is not True:
            raise CapabilityMismatchError("rootless Podman could not be confirmed")
        if request.resource_limits.memory_required and not self.capabilities.memory_limit:
            raise CapabilityMismatchError("memory enforcement is unsupported by the host")
        scratch = Path(tempfile.mkdtemp(prefix=f"arda-{request.execution_id}-"))
        container_name = f"arda-{request.execution_id}-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        cleanup_ok = True
        result: ExecutionResult | None = None
        try:
            workspace = request.working_directory or scratch
            process = self.process_factory(
                self.build_argv(request, scratch, container_name),
                cwd=None,
                env=self._podman_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                start_new_session=True,
            )
            stdout, stderr, reason = self._collect(process, request, workspace=workspace,
                                                   cancel_event=cancel_event)
            artifacts, artifact_error = self._artifacts(workspace, request.expected_artifacts)
            if artifact_error is not None and reason is None:
                reason = artifact_error
            status = "succeeded" if process.returncode == 0 and reason is None else "failed"
            result = ExecutionResult(
                execution_id=request.execution_id,
                status=status,
                backend=self.capabilities.backend,
                backend_version=self.capabilities.backend_version,
                requested=self._requested(request),
                capabilities=self.capabilities.as_dict(),
                enforcement=self._enforcement(request),
                exit_code=process.returncode,
                termination_reason=reason,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=time.monotonic() - started,
                artifacts=artifacts,
                cleanup_ok=True,
            )
        finally:
            if not self._cleanup_container(container_name):
                cleanup_ok = False
            try:
                shutil.rmtree(scratch)
            except OSError:
                cleanup_ok = False
        if result is None:
            raise RuntimeError("execution did not produce a result")
        if not cleanup_ok:
            result = ExecutionResult(**{**result.__dict__, "cleanup_ok": False})
        return result

    def run_cancellable(self, request: ExecutionRequest, cancel_event: threading.Event) -> ExecutionResult:
        return self.run(request, cancel_event=cancel_event)

    def _cleanup_container(self, container_name: str) -> bool:
        """Remove a still-running container after client-side termination.

        ``--rm`` is insufficient when the Podman client itself is killed by a
        timeout/output guard. The unique name lets the finally block clean up
        only this execution without touching unrelated containers.
        """
        # Test doubles do not create real Podman containers.
        if self.process_factory is not subprocess.Popen:
            return True
        try:
            completed = subprocess.run(
                [self.podman_path, "rm", "-f", container_name],
                check=False,
                capture_output=True,
                text=True,
                env=self._podman_env(),
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode == 0:
            return True
        message = f"{completed.stdout}\n{completed.stderr}".lower()
        return "no container with name or id" in message or "no such container" in message

    def _enforcement(self, request: ExecutionRequest) -> dict[str, str]:
        return {
            "network": "enforced" if self.capabilities.network_isolation else "unsupported_host",
            "pids": "enforced" if request.resource_limits.pids is not None and self.capabilities.pid_limit else "not_requested",
            "cpu": "enforced" if request.resource_limits.cpu_millis is not None and self.capabilities.cpu_limit else "not_requested",
            "memory": "enforced" if request.resource_limits.memory_mb is not None and self.capabilities.memory_limit else (
                "unsupported_host" if request.resource_limits.memory_mb is not None else "not_requested"
            ),
            "timeout": "enforced",
            "disk": "monitored_with_per_file_cap" if request.disk_limit_bytes is not None else "not_requested",
            "rootless": "confirmed" if self.capabilities.rootless is True else "unconfirmed",
        }

    @staticmethod
    def _requested(request: ExecutionRequest) -> dict[str, Any]:
        return {
            "pids": request.resource_limits.pids,
            "cpu_millis": request.resource_limits.cpu_millis,
            "memory_mb": request.resource_limits.memory_mb,
            "memory_required": request.resource_limits.memory_required,
            "network": request.network_policy,
            "timeout_seconds": request.timeout_seconds,
            "disk_limit_bytes": request.disk_limit_bytes,
            "rootless_required": request.require_rootless,
        }

    @staticmethod
    def _collect(process: Any, request: ExecutionRequest, *, workspace: Path | None = None,
                 cancel_event: threading.Event | None = None) -> tuple[str, str, str | None]:
        selector = selectors.DefaultSelector()
        streams = {process.stdout: ("stdout", request.output_limits.stdout_bytes), process.stderr: ("stderr", request.output_limits.stderr_bytes)}
        for stream in streams:
            selector.register(stream, selectors.EVENT_READ)
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        reason = None
        deadline = time.monotonic() + request.timeout_seconds
        while streams:
            if cancel_event is not None and cancel_event.is_set():
                reason = "Core lease lost"
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                streams.clear()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "timeout"
                os.killpg(process.pid, signal.SIGKILL)
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                data = key.fileobj.read(8192)
                if not data:
                    selector.unregister(key.fileobj)
                    streams.pop(key.fileobj, None)
                    continue
                name, limit = streams[key.fileobj]
                buffers[name].extend(data)
                if len(buffers[name]) > limit:
                    reason = f"{name}_limit"
                    os.killpg(process.pid, signal.SIGKILL)
                    streams.clear()
                    break
            if (request.disk_limit_bytes is not None and workspace is not None
                    and PodmanRuntime._workspace_bytes(workspace, request.disk_limit_bytes) > request.disk_limit_bytes):
                reason = "disk_limit"
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                streams.clear()
                break
            if process.poll() is not None and not streams:
                break
        process.wait()
        if (reason is None and request.disk_limit_bytes is not None and workspace is not None
                and PodmanRuntime._workspace_bytes(workspace, request.disk_limit_bytes) > request.disk_limit_bytes):
            reason = "disk_limit"
        return bytes(buffers["stdout"]).decode("utf-8", "replace"), bytes(buffers["stderr"]).decode("utf-8", "replace"), reason

    @staticmethod
    def _workspace_bytes(root: Path, limit: int) -> int:
        """Count regular file bytes without following attacker-created symlinks."""
        total = 0
        entries_seen = 0
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                return limit + 1
            for entry in entries:
                entries_seen += 1
                if entries_seen > 100_000:
                    return limit + 1
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    return limit + 1
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += info.st_size
                elif entry.is_symlink():
                    try:
                        total += len(os.fsencode(os.readlink(entry.path)))
                    except OSError:
                        return limit + 1
                else:
                    return limit + 1
                if total > limit:
                    return total
        return total

    @staticmethod
    def _artifacts(scratch: Path, expected: Sequence[str]) -> tuple[dict[str, str], str | None]:
        found: dict[str, str] = {}
        for relative in expected:
            path = scratch / relative
            resolved = path.resolve()
            if not path.exists():
                return found, "artifact_missing"
            if scratch not in resolved.parents or not path.is_file() or path.is_symlink():
                return found, "artifact_boundary_violation"
            found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return found, None
