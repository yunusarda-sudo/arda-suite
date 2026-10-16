"""Failure-first M2.4B ExecutionContract tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from arda.execution import (
    CapabilityMismatchError,
    ExecutionContractError,
    ExecutionRequest,
    MountSpec,
    OutputLimits,
    PodmanRuntime,
    ResourceLimits,
    RuntimeCapabilities,
)


IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64


def request(tmp_path: Path, **overrides) -> ExecutionRequest:
    values = {
        "execution_id": "exec-test",
        "repository_snapshot": tmp_path / "repo",
        "argv": ("pytest", "-q"),
    }
    values.update(overrides)
    values["repository_snapshot"].mkdir(exist_ok=True)
    return ExecutionRequest(**values)


def fake_process_factory(script: str):
    def factory(_argv, **kwargs):
        return subprocess.Popen([sys.executable, "-c", script], **kwargs)

    return factory


def test_empty_argv_is_rejected_before_backend_start(tmp_path):
    req = request(tmp_path, argv=())
    with pytest.raises(ExecutionContractError, match="argv"):
        req.validate()


def test_image_must_be_digest_pinned(tmp_path):
    with pytest.raises(ExecutionContractError, match="digest"):
        PodmanRuntime(image="docker.io/library/alpine:latest")


def test_shell_string_is_not_an_execution_contract(tmp_path):
    req = request(tmp_path, argv=("pytest && rm -rf /",))
    runtime = PodmanRuntime(image=IMAGE, process_factory=lambda *_args, **_kwargs: pytest.fail("started"))
    with pytest.raises(ExecutionContractError):
        runtime.run(req)


def test_network_policy_and_unsafe_cwd_are_rejected(tmp_path):
    with pytest.raises(ExecutionContractError, match="network_policy"):
        request(tmp_path, network_policy="egress").validate()
    with pytest.raises(ExecutionContractError, match="cwd"):
        request(tmp_path, cwd="/etc").validate()


def test_mount_destination_collision_and_artifact_traversal_are_rejected(tmp_path):
    with pytest.raises(ExecutionContractError, match="collides"):
        request(
            tmp_path,
            mounts=(MountSpec(tmp_path / "repo", "/workspace/source"),),
        ).validate()
    with pytest.raises(ExecutionContractError, match="relative"):
        request(tmp_path, expected_artifacts=("../../etc/passwd",)).validate()


def test_required_memory_capability_mismatch_does_not_start_process(tmp_path):
    req = request(
        tmp_path,
        resource_limits=ResourceLimits(memory_mb=512, memory_required=True),
    )
    runtime = PodmanRuntime(image=IMAGE, process_factory=lambda *_args, **_kwargs: pytest.fail("started"))
    with pytest.raises(CapabilityMismatchError, match="memory"):
        runtime.run(req)


def test_optional_memory_is_reported_unsupported_and_not_passed_to_podman(tmp_path):
    req = request(tmp_path, resource_limits=ResourceLimits(memory_mb=512, memory_required=False))
    runtime = PodmanRuntime(image=IMAGE, capabilities=RuntimeCapabilities(memory_limit=False))
    args = runtime.build_argv(req, tmp_path / "scratch")
    assert "--memory=512m" not in args
    assert runtime._enforcement(req)["memory"] == "unsupported_host"


def test_podman_command_has_no_inherited_environment_and_strict_mounts(tmp_path):
    req = request(tmp_path, environment_allowlist={"SAFE": "1"})
    runtime = PodmanRuntime(image=IMAGE)
    args = runtime.build_argv(req, tmp_path / "scratch")
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--env" in args
    assert "SAFE=1" in args
    assert all("ARDA_SPIKE_SECRET" not in value for value in args)
    assert "--pull=never" in args


def test_runtime_process_environment_is_not_inherited(tmp_path):
    captured = {}

    def factory(_argv, **kwargs):
        captured.update(kwargs["env"])
        return subprocess.Popen([sys.executable, "-c", "pass"], **kwargs)

    result = PodmanRuntime(image=IMAGE, process_factory=factory).run(request(tmp_path))
    assert result.status == "succeeded"
    assert "ARDA_SPIKE_SECRET" not in captured
    assert set(captured) <= {"PATH", "HOME", "XDG_RUNTIME_DIR", "TMPDIR",
                             "CONTAINERS_CONF", "CONTAINERS_CONF_OVERRIDE"}
    assert "PATH" in captured


def test_custom_workspace_is_used_for_artifact_collection(tmp_path):
    work = tmp_path / "work"
    work.mkdir()

    def factory(_argv, **kwargs):
        (work / "candidate.patch").write_text("diff", encoding="utf-8")
        return subprocess.Popen([sys.executable, "-c", "pass"], **kwargs)

    req = request(tmp_path, working_directory=work, expected_artifacts=("candidate.patch",))
    result = PodmanRuntime(image=IMAGE, process_factory=factory).run(req)

    assert result.status == "succeeded"
    assert result.artifacts["candidate.patch"]


def test_timeout_is_failure_and_reports_termination(tmp_path):
    req = request(tmp_path, timeout_seconds=0.05)
    runtime = PodmanRuntime(
        image=IMAGE,
        process_factory=fake_process_factory("import time; time.sleep(2)"),
    )
    result = runtime.run(req)
    assert result.status == "failed"
    assert result.termination_reason == "timeout"
    assert result.cleanup_ok is True


def test_output_limit_kills_noisy_process_and_never_returns_unbounded_output(tmp_path):
    req = request(tmp_path, output_limits=OutputLimits(stdout_bytes=128, stderr_bytes=128))
    runtime = PodmanRuntime(
        image=IMAGE,
        process_factory=fake_process_factory("import sys; sys.stdout.write('x' * 100000)"),
    )
    result = runtime.run(req)
    assert result.status == "failed"
    assert result.termination_reason == "stdout_limit"
    assert len(result.stdout.encode()) <= 8192 + 128


def test_nonzero_exit_is_not_successful_execution(tmp_path):
    req = request(tmp_path)
    runtime = PodmanRuntime(image=IMAGE, process_factory=fake_process_factory("raise SystemExit(7)"))
    result = runtime.run(req)
    assert result.status == "failed"
    assert result.exit_code == 7


def test_artifact_symlink_escape_is_rejected(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (scratch / "report.json").symlink_to(outside)
    artifacts, reason = PodmanRuntime._artifacts(scratch, ("report.json",))
    assert artifacts == {}
    assert reason == "artifact_boundary_violation"


def test_missing_expected_artifact_is_not_successful_publication(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    artifacts, reason = PodmanRuntime._artifacts(scratch, ("report.json",))
    assert artifacts == {}
    assert reason == "artifact_missing"


def test_capability_snapshot_is_explicit(tmp_path):
    req = request(tmp_path)
    runtime = PodmanRuntime(
        image=IMAGE,
        capabilities=RuntimeCapabilities(memory_limit=False, backend_version="5.4.2")
    )
    assert runtime.capabilities.as_dict()["memory_limit"] is False
    assert runtime.capabilities.as_dict()["backend_version"] == "5.4.2"
    assert runtime._requested(req)["memory_mb"] is None
