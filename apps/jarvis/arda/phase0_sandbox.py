"""Phase 0 (M2): OS-level sandbox boundary probes over rootless Podman.

Bridges the M1 fake-runner controller onto the M2.4B real ``PodmanRuntime``
from :mod:`arda.execution` and packages deterministic boundary probes
(network denial, read-only rootfs/source, host-env leak, pid limits,
timeout/output enforcement, scratch artifact capture).

The probe image is pinned by sha256 digest so evidence stays reproducible.
"""

from __future__ import annotations

import subprocess
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from arda.execution import (
    ExecutionRequest,
    ExecutionResult,
    OutputLimits,
    PodmanRuntime,
    ResourceLimits,
)
from arda.phase0_control import RunObservation

PODMAN_PROBE_IMAGE_DIGEST = (
    "sha256:45e09956dc667c5eff3583c9d94830261fb1ca0be10a0a7db36266edf5de9e1d"
)
PODMAN_PROBE_IMAGE = f"docker.io/library/alpine@{PODMAN_PROBE_IMAGE_DIGEST}"

_NETWORK_SCRIPT = (
    "wget -q -T 5 http://1.1.1.1/ 2>/dev/null;"
    'if [ $? -eq 0 ]; then echo ONLINE; exit 0; else echo OFFLINE; exit 7; fi'
)
_ROOTFS_WRITE_SCRIPT = (
    'if echo x >> /blocked.txt 2>/dev/null; then echo WROTE; exit 0;'
    'else echo DENIED; exit 13; fi'
)
_SOURCE_WRITE_SCRIPT = (
    'if echo x >> /workspace/source/host-marker.txt 2>/dev/null;'
    ' then echo WROTE; exit 0; else echo DENIED; exit 13; fi'
)
_ENV_SCRIPT = (
    'if env | grep -q ARDA_PROBE_SECRET_MARKER;'
    ' then echo LEAKED; exit 9; else echo CLEAN; fi'
)
_FORK_SCRIPT = (
    "ok=1; for i in 1 2 3 4 5 6 7 8; do (sleep 4 &) || ok=0; done;"
    'if [ $ok -eq 1 ]; then echo FORKED; exit 0; else echo FORK_FAILED; exit 11; fi'
)
_STDOUT_LIMIT_SCRIPT = "yes 'x' | head -c 200000; exit 0"
_SCRATCH_SCRIPT = "printf 'phase0-proof' > /workspace/work/proof.txt; exit 0"


@dataclass(frozen=True)
class SandboxProbe:
    """A single named boundary probe request."""

    name: str
    request: ExecutionRequest

    def run(self, runtime: PodmanRuntime) -> ExecutionResult:
        return runtime.run(self.request)


def make_probe(
    name: str,
    *,
    argv: tuple[str, ...],
    snapshot: Path,
    environment_allowlist: Mapping[str, str] | None = None,
    expected_artifacts: tuple[str, ...] = (),
    resource_limits: ResourceLimits | None = None,
    output_limits: OutputLimits | None = None,
    timeout_seconds: float = 30.0,
) -> SandboxProbe:
    suffix = name.replace("_", "-")
    request = ExecutionRequest(
        execution_id=f"probe-{suffix}",
        repository_snapshot=snapshot,
        argv=argv,
        environment_allowlist=dict(environment_allowlist or {}),
        expected_artifacts=expected_artifacts,
        resource_limits=resource_limits or ResourceLimits(),
        output_limits=output_limits or OutputLimits(),
        timeout_seconds=timeout_seconds,
    )
    return SandboxProbe(name=name, request=request)


def make_snapshot(seed: Mapping[str, str] | None = None) -> Path:
    """Create a disposable source snapshot directory (optionally seeded)."""
    base = Path(tempfile.mkdtemp(prefix="arda-snapshot-"))
    source = base / "source"
    source.mkdir()
    for relative, content in (seed or {}).items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return source


def cleanup_snapshot(snapshot: Path) -> None:
    shutil.rmtree(snapshot.parent, ignore_errors=True)


def network_probe(snapshot: Path) -> SandboxProbe:
    return make_probe("network", argv=("/bin/sh", "-c", _NETWORK_SCRIPT), snapshot=snapshot)


def rootfs_write_probe(snapshot: Path) -> SandboxProbe:
    return make_probe("rootfs-write", argv=("/bin/sh", "-c", _ROOTFS_WRITE_SCRIPT), snapshot=snapshot)


def source_write_probe(snapshot: Path, seed: Mapping[str, str] | None = None) -> SandboxProbe:
    return make_probe("source-write", argv=("/bin/sh", "-c", _SOURCE_WRITE_SCRIPT), snapshot=snapshot)


def env_leak_probe(snapshot: Path, marker: str = "ARDA_PROBE_SECRET_MARKER") -> SandboxProbe:
    return make_probe("env-leak", argv=("/bin/sh", "-c", _ENV_SCRIPT), snapshot=snapshot)


def env_declared_probe(
    snapshot: Path, marker: str = "ARDA_PROBE_SECRET_MARKER", value: str = "phase0-declared"
) -> SandboxProbe:
    """Positive control: a DECLARED env key must actually reach the container."""
    return make_probe(
        "env-declared",
        argv=("/bin/sh", "-c", _ENV_SCRIPT),
        snapshot=snapshot,
        environment_allowlist={marker: value},
    )


def pids_limit_probe(snapshot: Path) -> SandboxProbe:
    return make_probe(
        "pids-limit",
        argv=("/bin/sh", "-c", _FORK_SCRIPT),
        snapshot=snapshot,
        resource_limits=ResourceLimits(pids=4, cpu_millis=100),
    )


def timeout_probe(snapshot: Path) -> SandboxProbe:
    return make_probe(
        "timeout",
        argv=("/bin/sleep", "30"),
        snapshot=snapshot,
        timeout_seconds=3.0,
    )


def stdout_limit_probe(snapshot: Path) -> SandboxProbe:
    return make_probe(
        "stdout-limit",
        argv=("/bin/sh", "-c", _STDOUT_LIMIT_SCRIPT),
        snapshot=snapshot,
        output_limits=OutputLimits(stdout_bytes=65536),
    )


def scratch_capture_probe(snapshot: Path) -> SandboxProbe:
    return make_probe(
        "scratch-capture",
        argv=("/bin/sh", "-c", _SCRATCH_SCRIPT),
        snapshot=snapshot,
        expected_artifacts=("proof.txt",),
    )


def podman_sandbox_available() -> str | None:
    """Return None when a live Podman machine with the probe image exists."""
    try:
        completed = subprocess.run(
            ["podman", "info"], check=False, capture_output=True, text=True, timeout=30
        )
        if completed.returncode != 0:
            return f"podman machine unavailable: {completed.stderr.strip()[:200]}"
    except (OSError, subprocess.SubprocessError) as error:
        return f"podman not usable: {error}"
    try:
        inspected = subprocess.run(
            ["podman", "image", "inspect", PODMAN_PROBE_IMAGE],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if inspected.returncode != 0:
            return f"probe image missing locally: {PODMAN_PROBE_IMAGE}"
    except (OSError, subprocess.SubprocessError) as error:
        return f"podman inspect failed: {error}"
    return None


def execution_result_to_observation(result: ExecutionResult) -> RunObservation:
    """Map ExecutionResult onto the M1 controller's RunObservation contract.

    Termination reasons are surfaced explicitly: timeout/output-limit kills are
    not silent successes, and any non-clean exit keeps side-effect state
    explicit rather than claiming none happened.
    """
    reason = result.termination_reason
    side_effect_state = "none" if reason is None else "unknown"
    achieved = result.status == "succeeded" and reason is None
    return RunObservation(
        exit_code=result.exit_code,
        claimed_done=achieved,
        observed_model="",
        observed_effort="",
        usage=None,
        provider_requests=0,
        side_effect_state=side_effect_state,
        changed_paths=(),
        lost=False,
        timed_out=reason == "timeout",
    )


def sandbox_adapter(
    runtime: PodmanRuntime,
    *,
    snapshot: Path,
    artifact: str = "proof.txt",
    script: str = _SCRATCH_SCRIPT,
) -> Callable[[Any, str, Path | None], RunObservation]:
    """Adapter: drive one real sandboxed process per controller attempt."""
    def adapter(task, attempt_id, workspace):
        execution_id = f"phase0-{attempt_id}".replace("_", "-")[:128]
        request = ExecutionRequest(
            execution_id=execution_id,
            repository_snapshot=snapshot,
            argv=("/bin/sh", "-c", script),
            expected_artifacts=(artifact,),
            timeout_seconds=60.0,
        )
        result = runtime.run(request)
        return execution_result_to_observation(result)

    return adapter
