"""Sandbox-denial classifier for bounded process observations.

Adapted from openai/codex ``codex-rs/sandboxing/src/denial.rs``
(Apache-2.0 License, Copyright OpenAI): classifying whether a process
failure is *likely* a sandbox denial, because there is no fully
deterministic way to attribute failures to the sandbox when user
configuration may also fail.  The predicate is intentionally conservative
and side-effect free.

Phase 0 use: the M2 boundary probes and the M3 CLI adapters can label
observed failures as sandbox-related (deny evidence), user/tool error,
or unknown.  A non-denial label is not a PASS signal; callers must keep
the fail-closed controller semantics (exit-0 gate, unknown side effects).
"""

from __future__ import annotations

import re

DENIAL_CLASS_SANDBOX = "sandbox_denied"
DENIAL_CLASS_TOOL = "tool_error"
DENIAL_CLASS_UNKNOWN = "unknown_failure"

_QUICK_REJECT_EXIT_CODES = frozenset({2, 126, 127})
_EXIT_CODE_SIGNAL_BASE = 128
_SIGSYS = 31  # macOS/x86_64 & Linux aarch64 agree; seccomp kills map here.

_SANDBOX_DENIED_KEYWORDS: tuple[str, ...] = (
    # From codex sandboxing/denial.rs (Apache-2.0)
    "operation not permitted",
    "permission denied",
    "read-only file system",
    "seccomp",
    "sandbox",
    "landlock",
    "failed to write file",
    # Phase 0 additions for our podman/agent sandbox probes
    "resource temporarily unavailable",
    "cannot fork",
    "can't fork",
)

_KEYWORD_RE = re.compile(
    "|".join(re.escape(keyword) for keyword in _SANDBOX_DENIED_KEYWORDS),
    re.IGNORECASE,
)


def is_likely_sandbox_denied(
    exit_code: int | None, *, stderr: str = "", stdout: str = ""
) -> bool:
    """Conservatively attribute a failure to sandbox enforcement.

    Fail-closed framing: ``True`` means "likely sandbox denial"; ``False``
    means "cannot tell" — never "definitely not the sandbox".
    """
    if exit_code is None or exit_code == 0:
        return False
    if exit_code in _QUICK_REJECT_EXIT_CODES:
        # Shell/runner usage errors (syntax, not-found, no-exec) — expected
        # to be caused by the caller, not by enforcement.
        return False
    if exit_code == _EXIT_CODE_SIGNAL_BASE + _SIGSYS:
        # seccomp SIGSYS kill is definitive enforcement territory.
        return True
    return bool(_KEYWORD_RE.search(stderr) or _KEYWORD_RE.search(stdout))


def denial_class(
    exit_code: int | None, *, stderr: str = "", stdout: str = "", timed_out: bool = False
) -> str:
    """Map an observation onto the Phase 0 denial taxonomy.

    ``sandbox_denied`` — likely enforcement; run boundary probes to confirm.
    ``tool_error``     — exit codes 2/126/127 (caller/CLI usage error).
    ``unknown_failure``— anything else, including timeouts (isolation of the
                          cause is not proven; keep side_effect_state unknown).
    """
    if timed_out or exit_code is None:
        return DENIAL_CLASS_UNKNOWN
    if is_likely_sandbox_denied(exit_code, stderr=stderr, stdout=stdout):
        return DENIAL_CLASS_SANDBOX
    if exit_code in _QUICK_REJECT_EXIT_CODES:
        return DENIAL_CLASS_TOOL
    return DENIAL_CLASS_UNKNOWN
