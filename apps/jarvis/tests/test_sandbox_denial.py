"""Tests for the sandbox-denial classifier ported from openai/codex.

Expected behaviors are locked to the REAL M2 podman probe observations
(network probe off, rootfs read-only denial, fork denial) and the codex
denial.rs semantics (quick-reject exit codes, seccomp SIGSYS).
"""

from arda.sandbox_denial import (
    DENIAL_CLASS_SANDBOX,
    DENIAL_CLASS_TOOL,
    DENIAL_CLASS_UNKNOWN,
    denial_class,
    is_likely_sandbox_denied,
)


def test_p_sandbox_read_only_file_system_is_denied():
    signal = is_likely_sandbox_denied(
        13,
        stderr="/bin/sh: can't create /blocked.txt: Read-only file system",
        stdout="DENIED",
    )
    assert signal is True
    assert denial_class(13, stderr="can't create: Read-only file system") == (
        DENIAL_CLASS_SANDBOX
    )


def test_p_sandbox_fork_denial_maps_to_denied():
    assert denial_class(1, stderr="can't fork: Resource temporarily unavailable") == (
        DENIAL_CLASS_SANDBOX
    )


def test_p_sandbox_network_probe_is_not_denial():
    assert is_likely_sandbox_denied(7, stdout="OFFLINE", stderr="") is False
    assert denial_class(7, stdout="OFFLINE") == DENIAL_CLASS_UNKNOWN


def test_p_sandbox_shell_usage_errors_are_tool_not_denial():
    for exit_code in (2, 126, 127):
        assert denial_class(exit_code, stderr="/bin/sh: syntax error") == (
            DENIAL_CLASS_TOOL
        )


def test_p_sandbox_seccomp_sigsys_word_is_denied():
    assert denial_class(159, stderr="seccomp denied syscall") == DENIAL_CLASS_SANDBOX
    assert is_likely_sandbox_denied(128 + 31, stderr="") is True


def test_p_sandbox_success_never_denied_and_timeout_stays_unknown():
    assert denial_class(0, stderr="anything") == DENIAL_CLASS_UNKNOWN
    assert denial_class(None, timed_out=True) == DENIAL_CLASS_UNKNOWN
    assert is_likely_sandbox_denied(None, stderr="permission denied") is False


def test_p_sandbox_mixed_streams_scenario(tmp_path):
    # probe the classifier individually
    assert is_likely_sandbox_denied(13, stdout="x", stderr="permission denied") is True
    assert is_likely_sandbox_denied(3, stdout="sandbox audit line", stderr="") is True
    assert is_likely_sandbox_denied(3, stdout="hello", stderr="") is False
