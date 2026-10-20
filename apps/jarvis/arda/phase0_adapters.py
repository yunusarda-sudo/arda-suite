"""Phase 0 (M3): real-CLI provider adapters for the Phase0Controller.

Adapters translate a TaskContract into a real CLI invocation (codex exec,
claude --print, opencode run), then map the CLI's JSON output onto the
controller's RunObservation contract.  The controller/acceptance semantics
are unchanged: a CLI that cannot produce a parseable, matching result yields
lost observations, never PASS.

Paid-model calls are gated: the built-in adapters refuse paid TaskContracts
unless ``allow_paid=True`` is passed explicitly.  Tests inject a deterministic
CommandRunner; no paid model call happens in the test suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from arda.phase0_control import (
    Phase0Error,
    RunObservation,
    TaskContract,
)

SUPPORTED_PROVIDERS = ("openai-codex", "anthropic-claude", "opencode")
UNPAID_EFFORT = "default"
_DEFAULT_MAX_TIMEOUT_SECONDS = 1800.0
_DEFAULT_MAX_OUTPUT_BYTES = 1 << 20


class AdapterContractError(Phase0Error):
    """Adapter contract violation: bad provider, effort, budget gate, CLI surface."""


@dataclass(frozen=True)
class RecordedOutput:
    """Deterministic substitute for (or capture of) one CLI process invocation."""

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str = ""
    timed_out: bool = False

    @property
    def clean_exit(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class CommandRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_seconds: float,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> RecordedOutput: ...


class SubprocessCommandRunner:
    """Real runner: spawn the CLI binary, bounded capture, hard timeout."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_seconds: float,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> RecordedOutput:
        if shutil.which(argv[0]) is None:
            raise AdapterContractError(f"CLI binary not found on PATH: {argv[0]}")
        try:
            completed = subprocess.run(
                list(argv),
                cwd=None if cwd is None else str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return RecordedOutput(
                argv=argv,
                exit_code=None,
                stdout=error.stdout if isinstance(error.stdout, str) else "",
                stderr=error.stderr if isinstance(error.stderr, str) else "",
                timed_out=True,
            )
        return RecordedOutput(
            argv=argv,
            exit_code=completed.returncode,
            stdout=completed.stdout[:max_output_bytes],
            stderr=completed.stderr[:max_output_bytes],
        )


def _cwd_for(task: TaskContract, workspace: Path | None) -> Path | None:
    return task.workspace if task.workspace is not None else workspace


_CLAUDE_DENIED_TOOLS = ("Edit", "Write", "Bash", "WebSearch", "WebFetch")


def build_codex_argv(
    task: TaskContract, *, workspace: Path | None = None, prompt: str
) -> tuple[str, ...]:
    return (
        "codex", "exec",
        "--json",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "-C", str(_cwd_for(task, workspace) or Path.cwd()),
        "-m", task.model,
        "-c", f'model_reasoning_effort="{task.effort}"',
        prompt,
    )


def build_claude_argv(
    task: TaskContract, *, workspace: Path | None = None, prompt: str,
    max_turns: int = 8, max_budget_usd: float | None = None,
) -> tuple[str, ...]:
    if task.effort != UNPAID_EFFORT:
        raise AdapterContractError(
            f"effort {task.effort!r} unsupported by the claude CLI surface; "
            f"only {UNPAID_EFFORT!r} is contract-valid"
        )
    argv = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", task.model,
        "--max-turns", str(max_turns),
        "--disallowedTools", ",".join(_CLAUDE_DENIED_TOOLS),
    ]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", f"{max_budget_usd:g}"]
    argv.append(prompt)
    return tuple(argv)


def build_opencode_argv(
    task: TaskContract, *, workspace: Path | None = None, prompt: str
) -> tuple[str, ...]:
    argv = [
        "opencode", "run",
        "--format", "json",
        "--model", task.model,
        "--dir", str(_cwd_for(task, workspace) or Path.cwd()),
    ]
    if task.effort != UNPAID_EFFORT:
        argv += ["--variant", task.effort]
    argv.append(prompt)
    return tuple(argv)


@dataclass(frozen=True)
class ParsedAttempt:
    observation: RunObservation
    provider_requests: int
    observed_model: str
    observed_effort: str


def _timed_out_observation() -> RunObservation:
    # The CLI was killed: no completion evidence, but execution happened.
    return RunObservation(
        exit_code=None,
        claimed_done=False,
        provider_requests=0,
        side_effect_state="unknown",
        lost=False,
        timed_out=True,
    )


def _lost(result: RecordedOutput, message: str) -> ParsedAttempt:
    return ParsedAttempt(
        RunObservation(
            exit_code=result.exit_code,
            claimed_done=False,
            provider_requests=0,
            side_effect_state="unknown",
            lost=True,
            timed_out=result.timed_out,
        ),
        0,
        "",
        message,
    )


def _finish(
    result: RecordedOutput,
    *,
    provider_requests: int,
    observed_model: str = "",
    observed_effort: str = "",
    usage: float | None = None,
    claimed_done: bool | None = None,
) -> ParsedAttempt:
    achieved = result.clean_exit if claimed_done is None else claimed_done
    return ParsedAttempt(
        RunObservation(
            exit_code=result.exit_code,
            claimed_done=achieved,
            observed_model=observed_model,
            observed_effort=observed_effort,
            usage=usage,
            provider_requests=provider_requests,
            side_effect_state="none" if achieved else "unknown",
            lost=False,
            timed_out=result.timed_out,
        ),
        provider_requests,
        observed_model,
        observed_effort,
    )


def _json_envelopes(stdout: str) -> list[dict[str, Any]] | None:
    """Strict: any non-JSON line makes the whole output unreliable (fail-closed)."""
    envelopes: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        envelopes.append(value)
    return envelopes


def _count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _cost(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _model_from_item(envelope: Mapping[str, Any]) -> str | None:
    item = envelope.get("item")
    if isinstance(item, Mapping):
        candidate = item.get("model")
        return candidate if isinstance(candidate, str) else None
    return None


def _token_usage(payload: Any) -> int | None:
    total = _count(payload.get("input_tokens")) + _count(payload.get("output_tokens"))
    return total or None


def parse_codex(
    result: RecordedOutput, *, usage_estimator: Callable[[int], float | None] | None = None
) -> ParsedAttempt:
    if result.timed_out:
        return ParsedAttempt(_timed_out_observation(), 0, "", "")
    envelopes = _json_envelopes(result.stdout)
    if envelopes is None:
        return _lost(result, "unparseable codex JSONL output")
    provider_requests = 0
    observed_model = ""
    usage: float | None = None
    for envelope in envelopes:
        if envelope.get("type") == "turn.started":
            provider_requests += 1
        for candidate in (envelope.get("model"), _model_from_item(envelope)):
            if isinstance(candidate, str) and candidate:
                observed_model = candidate
        if envelope.get("type") == "turn.completed":
            tokens = envelope.get("usage", {})
            total = _count(tokens.get("input_tokens")) + _count(tokens.get("output_tokens"))
            if callable(usage_estimator):
                usage = usage_estimator(total)
        if envelope.get("type") == "turn.failed":
            error = envelope.get("error", {})
            description = error.get("message", "") if isinstance(error, Mapping) else str(error)
            return _finish(
                result,
                provider_requests=provider_requests,
                observed_model=observed_model,
                claimed_done=False,
            )
    if not provider_requests:
        return _lost(result, "codex output had no turn events")
    return _finish(result, provider_requests=provider_requests, observed_model=observed_model, usage=usage)


def parse_claude(result: RecordedOutput) -> ParsedAttempt:
    if result.timed_out:
        return ParsedAttempt(_timed_out_observation(), 0, "", "")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return _lost(result, "unparseable claude output")
    if not isinstance(raw, dict):
        return _lost(result, "claude output not a JSON object")
    model_usage = raw.get("modelUsage", {})
    observed_model = ""
    if isinstance(model_usage, dict) and model_usage:
        observed_model = str(next(iter(model_usage)))
    provider_requests = _count(raw.get("num_turns"))
    usage = None if raw.get("is_error") else _cost(raw.get("total_cost_usd"))
    return _finish(
        result,
        provider_requests=provider_requests,
        observed_model=observed_model,
        usage=usage,
        claimed_done=False if raw.get("is_error") else None,
    )


def parse_opencode(
    result: RecordedOutput, *, usage_estimator: Callable[[int], float | None] | None = None
) -> ParsedAttempt:
    if result.timed_out:
        return ParsedAttempt(_timed_out_observation(), 0, "", "")
    envelopes = _json_envelopes(result.stdout)
    if envelopes is None:
        return _lost(
            result,
            "opencode output empty" if not result.stdout.strip() else "unparseable opencode output",
        )
    provider_requests = 0
    cost_total: float | None = None
    token_total: int | None = None
    observed_model = ""
    for envelope in envelopes:
        if envelope.get("type") == "step-finish":
            provider_requests += 1
            cost = _cost(envelope.get("cost"))
            if cost is not None:
                cost_total = (cost_total or 0.0) + cost
            tokens = envelope.get("tokens")
            if isinstance(tokens, Mapping):
                rendered = _count(tokens.get("input")) + _count(tokens.get("output"))
                token_total = (token_total or 0) + rendered
        for candidate in (envelope.get("model"), _model_from_item(envelope)):
            if isinstance(candidate, str) and candidate:
                observed_model = candidate
    if not provider_requests:
        return _lost(result, "opencode output had no step-finish events")
    if callable(usage_estimator) and token_total:
        usage = usage_estimator(token_total)
    elif cost_total is not None:
        usage = cost_total
    else:
        usage = None
    return _finish(
        result,
        provider_requests=provider_requests,
        observed_model=observed_model,
        usage=usage,
    )


@dataclass(frozen=True)
class CliProviderAdapter:
    """Provider-specific assembly: argv builder plus output parser."""

    provider: str
    argv_builder: Callable[..., tuple[str, ...]]
    parser: Callable[..., ParsedAttempt]

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise AdapterContractError(f"unsupported provider: {self.provider}")


CODEX_ADAPTER = CliProviderAdapter("openai-codex", build_codex_argv, parse_codex)
CLAUDE_ADAPTER = CliProviderAdapter("anthropic-claude", build_claude_argv, parse_claude)
OPENCODE_ADAPTER = CliProviderAdapter("opencode", build_opencode_argv, parse_opencode)
PROVIDER_ADAPTERS: Mapping[str, CliProviderAdapter] = {
    adapter.provider: adapter for adapter in (CODEX_ADAPTER, CLAUDE_ADAPTER, OPENCODE_ADAPTER)
}


def phase0_cli_adapter(
    adapter: CliProviderAdapter,
    runner: CommandRunner | None = None,
    *,
    allow_paid: bool = False,
    timeout_seconds: float = _DEFAULT_MAX_TIMEOUT_SECONDS,
    argv_options: Mapping[str, Any] | None = None,
) -> Callable[[TaskContract, str, Path | None], RunObservation]:
    """Adapter cast for Phase0Controller.run: one CLI invocation per attempt."""

    runner = runner if runner is not None else SubprocessCommandRunner()
    options = dict(argv_options or {})

    def cli_adapter(task: TaskContract, attempt_id: str, workspace: Path | None) -> RunObservation:
        if adapter.provider != task.provider:
            raise AdapterContractError(
                f"adapter provider {adapter.provider} != task provider {task.provider}"
            )
        if task.paid and not allow_paid:
            raise AdapterContractError(
                "paid provider call is not authorized; pass allow_paid=True explicitly"
            )
        prompt = options.get("prompt") or f"phase0 task {task.task_id} attempt {attempt_id}"
        argv = adapter.argv_builder(task, workspace=workspace, prompt=prompt)
        recorded = runner.run(
            argv,
            cwd=_cwd_for(task, workspace),
            timeout_seconds=max(1.0, float(timeout_seconds)),
        )
        parsed = adapter.parser(recorded, **dict(options.get("parser_options", {})))
        # Controller contract: observed_model is compared against "provider/model".
        observed = parsed.observed_model
        if observed and observed == task.model:
            observed = f"{task.provider}/{task.model}"
        return replace(
            parsed.observation,
            observed_model=observed,
            observed_effort=(
                task.effort if parsed.observed_effort else parsed.observed_effort
            ),
        )

    return cli_adapter
