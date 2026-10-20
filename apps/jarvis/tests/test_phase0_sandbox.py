"""Phase 0 (M2) real-podman sandbox boundary proof tests.

Each test drives a REAL container process through ``PodmanRuntime`` and
asserts the OS-level enforcement observed with alpine 3.20 (pinned digest).
Tests skip (never fail) when no live Podman machine exists, but MUST be
verified at least once on a machine with a running runtime before the M2
gate can be declared PASS.
"""

from __future__ import annotations

import os
from hashlib import sha256

import pytest

from arda.execution import (
    ExecutionContractError,
    ExecutionRequest,
    PodmanRuntime,
)
from arda.phase0_sandbox import (
    PODMAN_PROBE_IMAGE,
    cleanup_snapshot,
    env_declared_probe,
    env_leak_probe,
    execution_result_to_observation,
    make_snapshot,
    network_probe,
    podman_sandbox_available,
    pids_limit_probe,
    rootfs_write_probe,
    sandbox_adapter,
    scratch_capture_probe,
    source_write_probe,
    stdout_limit_probe,
    timeout_probe,
)

_UNAVAILABLE = podman_sandbox_available()
requires_sandbox = pytest.mark.skipif(
    _UNAVAILABLE is not None,
    reason=f"live podman sandbox unavailable: {_UNAVAILABLE}",
)
requires_sandbox.__doc__ = "Skip contract: never fake a PASS; absence = skip, not pass."


@pytest.fixture(scope="module")
def runtime() -> PodmanRuntime:
    return PodmanRuntime(image=PODMAN_PROBE_IMAGE)


@requires_sandbox
def test_p_sandbox_network_denied(runtime):
    snapshot = make_snapshot()
    try:
        result = network_probe(snapshot).run(runtime)
        assert result.exit_code == 7
        assert "OFFLINE" in result.stdout
        assert "ONLINE" not in result.stdout
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_rootfs_write_denied(runtime):
    snapshot = make_snapshot({"seed.txt": "untouched\n"})
    try:
        result = rootfs_write_probe(snapshot).run(runtime)
        assert result.exit_code == 13
        assert "DENIED" in result.stdout
        assert "Read-only file system" in result.stderr
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_source_snapshot_untouched(runtime):
    seed = {"host-marker.txt": "original-value\n"}
    snapshot = make_snapshot(seed)
    try:
        result = source_write_probe(snapshot).run(runtime)
        assert result.exit_code == 13
        assert "DENIED" in result.stdout
        assert (snapshot / "host-marker.txt").read_text() == seed["host-marker.txt"]
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_host_env_not_leaked(runtime):
    secret = "phase0-leak-marker-42f3"
    marker = "ARDA_PROBE_SECRET_MARKER"
    os.environ[marker] = secret
    snapshot = make_snapshot()
    try:
        result = env_leak_probe(snapshot).run(runtime)
        assert result.exit_code == 0
        assert "CLEAN" in result.stdout
        assert "LEAKED" not in result.stdout
        assert secret not in result.stdout and secret not in result.stderr

        declared = env_declared_probe(snapshot).run(runtime)
        assert declared.exit_code == 9
        assert "LEAKED" in declared.stdout
    finally:
        os.environ.pop(marker, None)
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_pids_limit_enforced(runtime):
    snapshot = make_snapshot()
    try:
        result = pids_limit_probe(snapshot).run(runtime)
        assert result.exit_code == 11
        assert "FORK_FAILED" in result.stdout
        assert "can't fork" in result.stderr
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_timeout_enforced(runtime):
    snapshot = make_snapshot()
    try:
        result = timeout_probe(snapshot).run(runtime)
        assert result.termination_reason == "timeout"
        assert result.exit_code == -9
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_stdout_limit_enforced(runtime):
    snapshot = make_snapshot()
    try:
        result = stdout_limit_probe(snapshot).run(runtime)
        assert result.termination_reason == "stdout_limit"
        assert len(result.stdout) > 0
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_scratch_artifacts_captured_and_hashed(runtime):
    snapshot = make_snapshot()
    try:
        result = scratch_capture_probe(snapshot).run(runtime)
        assert result.status == "succeeded"
        expected = sha256(b"phase0-proof").hexdigest()
        assert result.artifacts.get("proof.txt") == expected
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_unknown_artifact_negative_probe(runtime):
    snapshot = make_snapshot()
    try:
        request = ExecutionRequest(
            execution_id="probe-artifact-missing",
            repository_snapshot=snapshot,
            argv=("/bin/sh", "-c", "exit 0"),
            expected_artifacts=("never-written.txt",),
            timeout_seconds=30.0,
        )
        result = runtime.run(request)
        assert result.status == "failed"
        assert result.termination_reason == "artifact_missing"
    finally:
        cleanup_snapshot(snapshot)


@requires_sandbox
def test_p_sandbox_contract_rejects_shell_strings():
    snapshot = make_snapshot()
    try:
        request = ExecutionRequest(
            execution_id="bad-shell",
            repository_snapshot=snapshot,
            argv=("sh -c 'echo pwned'; echo",),
        )
        with pytest.raises(ExecutionContractError):
            request.validate()
    finally:
        cleanup_snapshot(snapshot)


def test_execution_result_to_observation_mapping():
    from arda.execution import ExecutionResult

    def result(**overrides):
        base = dict(
            execution_id="mapped",
            status="succeeded",
            backend="podman",
            backend_version="6",
            requested={},
            capabilities={},
            enforcement={},
            exit_code=0,
            termination_reason=None,
            stdout="",
            stderr="",
            duration_seconds=0.1,
            artifacts={},
            cleanup_ok=True,
        )
        base.update(overrides)
        return ExecutionResult(**base)

    clean = execution_result_to_observation(result())
    assert clean.exit_code == 0
    assert clean.claimed_done is True
    assert clean.side_effect_state == "none"
    assert clean.timed_out is False

    timed = execution_result_to_observation(result(termination_reason="timeout", exit_code=-9))
    assert timed.timed_out is True
    assert timed.side_effect_state == "unknown"
    assert timed.claimed_done is False


@requires_sandbox
def test_p_sandbox_controller_drives_real_process(tmp_path):
    from test_phase0_control import acceptance_ok, make_controller, make_registry, make_task

    snapshot = make_snapshot()
    try:
        runtime = PodmanRuntime(image=PODMAN_PROBE_IMAGE)
        controller = make_controller(tmp_path, data=None)
        task = make_task(tmp_path, task_id="t-podman", paid=False, cap=0.0, attempt_budget=0.0)
        adapter = sandbox_adapter(runtime, snapshot=snapshot)
        outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
        assert outcome.status == "done"
        assert outcome.execution_result == "exit_0"
        assert outcome.acceptance_result == "passed"
        assert outcome.accounting_result == "not_reserved"
        spawned = [e for e in outcome.events if e["type"] == "process_spawned"]
        assert spawned
        evidence_total = controller.db.scalar(
            "SELECT COUNT(*) FROM artifacts WHERE job_id = ?", (outcome.job_id,)
        )
        assert evidence_total == 1
    finally:
        cleanup_snapshot(snapshot)
