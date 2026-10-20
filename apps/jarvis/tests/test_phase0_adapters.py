"""Phase 0 (M3) real-CLI adapter contract tests.

All runner invocations are deterministic injected fakes with recorded
fixture outputs: no real model call, no paid call, ever. Scenario names
carry their P-codes where they map onto the Phase 0 matrix.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from arda.phase0_adapters import (
    CODEX_ADAPTER,
    CLAUDE_ADAPTER,
    OPENCODE_ADAPTER,
    AdapterContractError,
    CliProviderAdapter,
    RecordedOutput,
    SUPPORTED_PROVIDERS,
    SubprocessCommandRunner,
    build_claude_argv,
    build_codex_argv,
    build_opencode_argv,
    parse_claude,
    parse_codex,
    parse_opencode,
    phase0_cli_adapter,
)
from arda.phase0_control import TaskContract

_CODEX_SUCCESS = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": "th-1", "model": "gpt-5-fixture"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1000, "output_tokens": 5},
            }
        ),
    ]
)
_CLAUDE_SUCCESS = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "num_turns": 1,
        "total_cost_usd": 0.002456,
        "modelUsage": {"claude-sonnet-4-5": {"costUSD": 0.002456}},
    }
)
_OPENCODE_SUCCESS = "\n".join(
    [
        json.dumps({"type": "step-start"}),
        json.dumps({"type": "step-finish", "cost": 0.00312, "tokens": {"input": 1200, "output": 34}}),
    ]
)


def _task(tmp_path: Path, **overrides) -> TaskContract:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    fields = {
        "task_id": "t-cli",
        "provider": "openai-codex",
        "model": "gpt-5-fixture",
        "effort": "low",
        "paid": False,
        "cap": 1.0,
        "attempt_budget": 0.5,
        "workspace": workspace,
    }
    fields.update(overrides)
    return TaskContract(**fields)


class StaticRunner:
    def __init__(self, recorded: RecordedOutput):
        self.recorded = recorded
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def run(self, argv, *, cwd, timeout_seconds, max_output_bytes=1 << 20):
        self.calls.append((argv, cwd))
        assert timeout_seconds >= 1.0
        return self.recorded


def test_p12_codex_argv_contract(tmp_path):
    task = _task(tmp_path)
    argv = build_codex_argv(task, prompt="p")
    assert argv[0:3] == ("codex", "exec", "--json")
    assert ("--sandbox", "read-only") == argv[argv.index("--sandbox"):argv.index("--sandbox") + 2]
    assert argv[argv.index("-C") + 1] == str(task.workspace)
    assert argv[argv.index("-m") + 1] == task.model
    assert argv[argv.index("-c") + 1] == f'model_reasoning_effort="{task.effort}"'
    assert argv[-1] == "p"
    assert "--skip-git-repo-check" in argv and "--ephemeral" in argv
    argv2 = build_codex_argv(task, prompt="p")
    assert argv == argv2


def test_p12_claude_argv_contract(tmp_path):
    task = _task(tmp_path, provider="anthropic-claude", model="claude-fixture", effort="default")
    argv = build_claude_argv(task, prompt="p", max_budget_usd=0.5)
    assert argv[0:2] == ("claude", "--print")
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == task.model
    assert argv[argv.index("--max-turns") + 1] == "8"
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert {"Edit", "Write", "Bash", "WebSearch", "WebFetch"} <= set(denied)
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5"
    assert argv[-1] == "p"


def test_p12_claude_effort_contract(tmp_path):
    task = _task(tmp_path, provider="anthropic-claude", model="claude-fixture", effort="low")
    with pytest.raises(AdapterContractError):
        build_claude_argv(task, prompt="p")


def test_p12_opencode_argv_contract(tmp_path):
    task = _task(
        tmp_path, provider="opencode", model="provider/mock-model", effort="default"
    )
    argv = build_opencode_argv(task, prompt="p")
    assert argv[0:4] == ("opencode", "run", "--format", "json")
    assert argv[argv.index("--model") + 1] == task.model
    assert argv[argv.index("--dir") + 1] == str(task.workspace)
    assert "--variant" not in argv and argv[-1] == "p"
    effortful = build_opencode_argv(
        _task(tmp_path, provider="opencode", model="provider/mock-model", effort="high"),
        prompt="p",
    )
    assert effortful[effortful.index("--variant") + 1] == "high"


def test_p12_unknown_provider_contract(tmp_path):
    task = _task(tmp_path, provider="unknown-cli")
    with pytest.raises(AdapterContractError):
        CliProviderAdapter("unknown-cli", build_codex_argv, parse_codex)


def test_p12_provider_task_contract(tmp_path):
    runner = StaticRunner(RecordedOutput(argv=(), exit_code=0, stdout=""))
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    with pytest.raises(AdapterContractError):
        adapter(_task(tmp_path, provider="opencode"), "a1", None)


def test_p12_paid_call_gate(tmp_path):
    runner = StaticRunner(RecordedOutput(argv=(), exit_code=0, stdout=""))
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    with pytest.raises(AdapterContractError, match="allow_paid"):
        adapter(_task(tmp_path, paid=True), "a1", None)
    assert not runner.calls


def test_p12_missing_binary_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    runner = SubprocessCommandRunner()
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    with pytest.raises(AdapterContractError, match="not found on PATH"):
        adapter(_task(tmp_path), "a1", None)


def test_p04_codex_success_parse(tmp_path):
    parsed = parse_codex(
        RecordedOutput(argv=(), exit_code=0, stdout=_CODEX_SUCCESS)
    )
    assert parsed.observation.claimed_done is True
    assert parsed.observation.exit_code == 0
    assert parsed.observation.provider_requests == 1
    assert parsed.observation.side_effect_state == "none"
    assert parsed.observation.lost is False
    assert parsed.observed_model == "gpt-5-fixture"
    assert parsed.observation.usage is None


def test_p04_codex_success_parse_usage_estimator(tmp_path):
    parsed = parse_codex(
        RecordedOutput(argv=(), exit_code=0, stdout=_CODEX_SUCCESS),
        usage_estimator=lambda tokens: sum(tokens.values()) * 1e-6,
    )
    assert parsed.observation.usage is not None
    assert not math.isnan(parsed.observation.usage)


def test_p09_codex_failure_parse(tmp_path):
    result = RecordedOutput(argv=(), exit_code=1, stdout="")
    parsed = parse_codex(result)
    assert parsed.observation.claimed_done is False
    assert parsed.observation.side_effect_state == "unknown"
    assert parsed.observation.usage is None


def test_p09_codex_lost_parse(tmp_path):
    for stdout in ("not TestBedW json {", "", json.dumps({"type": "item.completed"})):
        parsed = parse_codex(RecordedOutput(argv=(), exit_code=0, stdout=stdout))
        assert parsed.observation.lost is True, stdout
    parsed = parse_codex(RecordedOutput(argv=(), exit_code=None, stdout="", timed_out=True))
    assert parsed.observation.timed_out is True
    assert parsed.observation.provider_requests == 0


def test_p04_codex_turn_failed_claimed_not_done(tmp_path):
    stream = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "turn.failed", "error": {"message": "quota"}}),
        ]
    )
    parsed = parse_codex(RecordedOutput(argv=(), exit_code=0, stdout=stream))
    assert parsed.observation.claimed_done is False
    assert parsed.observation.side_effect_state == "unknown"
    assert parsed.observation.exit_code == 0


def test_p04_claude_success_parse(tmp_path):
    parsed = parse_claude(RecordedOutput(argv=(), exit_code=0, stdout=_CLAUDE_SUCCESS))
    assert parsed.observation.claimed_done is True
    assert parsed.observation.usage == 0.002456
    assert parsed.observed_model == "claude-sonnet-4-5"
    assert parsed.observation.provider_requests == 1


def test_p09_claude_error_parse(tmp_path):
    raw = json.loads(_CLAUDE_SUCCESS)
    raw.update(is_error=True)
    parsed = parse_claude(RecordedOutput(argv=(), exit_code=0, stdout=json.dumps(raw)))
    assert parsed.observation.claimed_done is False
    assert parsed.observation.usage is None
    assert parsed.observation.side_effect_state == "unknown"
    for stdout in ("{{", "[]", ""):
        parsed = parse_claude(RecordedOutput(argv=(), exit_code=0, stdout=stdout))
        assert parsed.observation.lost is True, stdout


def test_p04_opencode_success_parse(tmp_path):
    parsed = parse_opencode(RecordedOutput(argv=(), exit_code=0, stdout=_OPENCODE_SUCCESS))
    assert parsed.observation.claimed_done is True
    assert parsed.observation.usage == 0.00312
    assert parsed.observation.provider_requests == 1


def test_p09_opencode_lost_parse(tmp_path):
    parsed = parse_opencode(RecordedOutput(argv=(), exit_code=0, stdout=json.dumps([])))
    assert parsed.observation.lost is True
    parsed = parse_opencode(RecordedOutput(argv=(), exit_code=0, stdout="\n\n"))
    assert parsed.observation.lost is True
    parsed = parse_opencode(RecordedOutput(argv=(), exit_code=0, stdout="garbage bytes"))
    assert parsed.observation.lost is True


def test_p12_adapter_model_normalization(tmp_path):
    stream = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps({"model": "gpt-5-fixture"}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}),
        ]
    )
    runner = StaticRunner(RecordedOutput(argv=(), exit_code=0, stdout=stream))
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    observation = adapter(_task(tmp_path), "a1", None)
    assert observation.observed_model == "openai-codex/gpt-5-fixture"


def test_p12_provider_drift_remains_raw(tmp_path):
    stream = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps({"model": "gpt-3-drifted"}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )
    runner = StaticRunner(RecordedOutput(argv=(), exit_code=0, stdout=stream))
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    observation = adapter(_task(tmp_path), "a1", None)
    assert observation.observed_model == "gpt-3-drifted"


def test_p12_supported_provider_catalogue():
    assert SUPPORTED_PROVIDERS == ("openai-codex", "anthropic-claude", "opencode")
    for provider in SUPPORTED_PROVIDERS:
        from arda.phase0_adapters import PROVIDER_ADAPTERS

        assert provider in PROVIDER_ADAPTERS


def test_p12_codex_usage_full_token_taxonomy(tmp_path):
    stream = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 5,
                        "cache_write_input_tokens": 2,
                        "output_tokens": 3,
                        "reasoning_output_tokens": 4,
                    },
                }
            ),
        ]
    )
    captured: list[dict] = []
    parsed = parse_codex(
        RecordedOutput(argv=(), exit_code=0, stdout=stream),
        usage_estimator=lambda usage: (
            captured.append(dict(usage)) or float(sum(usage.values()))
        ),
    )
    assert captured == [
        {
            "input_tokens": 10,
            "cached_input_tokens": 5,
            "cache_write_input_tokens": 2,
            "output_tokens": 3,
            "reasoning_output_tokens": 4,
        }
    ]
    assert parsed.observation.usage == 24.0


def test_p12_codex_no_model_event_observed_empty(tmp_path):
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "th-x"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
            ),
        ]
    )
    parsed = parse_codex(RecordedOutput(argv=(), exit_code=0, stdout=stream))
    assert parsed.observed_model == ""


# --------------------------------------------------------------------------
# Controller integration: Phase0Controller + CLI adapter (fixture runner)
# --------------------------------------------------------------------------


def _policy_data_for(provider_model: str) -> dict:
    return {
        "policy_version": "phase0-policy-cli-v1",
        "allowed_models": [provider_model],
        "allowed_efforts": ["default", "high", "low"],
        "max_attempts_for_task": 2,
        "parallel_attempts": 2,
    }


def test_p14_controller_runs_unpaid_cli_attempt(tmp_path):
    from test_phase0_control import acceptance_ok, make_controller, make_registry

    controller = make_controller(
        tmp_path,
        data=_policy_data_for("openai-codex/gpt-5-fixture"),
        registry=make_registry(
            extra=[("openai-codex", "gpt-5-fixture", ("low", "medium"), False)]
        ),
    )
    runner = StaticRunner(
        RecordedOutput(argv=(), exit_code=0, stdout=_CODEX_SUCCESS)
    )
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    task = _task(tmp_path, paid=False)
    outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
    assert outcome.status == "done"
    assert outcome.execution_result == "exit_0"
    assert outcome.acceptance_result == "passed"
    assert outcome.accounting_result == "not_reserved"
    argv, cwd = runner.calls[0]
    assert argv[0] == "codex" and "exec" in argv
    assert cwd == task.workspace
    types = [e["type"] for e in outcome.events]
    assert "attempt_leased" in types and "process_spawned" in types
    observed = next(e for e in outcome.events if e["type"] == "runtime_model_observed")
    assert observed["payload"]["model"] == "openai-codex/gpt-5-fixture"


def test_p09_controller_reconciles_paid_cli_usage(tmp_path):
    from test_phase0_control import acceptance_ok, make_controller, make_registry
    from arda.phase0_control import BudgetLedger

    controller = make_controller(
        tmp_path,
        data=_policy_data_for("openai-codex/gpt-5-fixture"),
        registry=make_registry(
            extra=[("openai-codex", "gpt-5-fixture", ("low", "medium"), False)]
        ),
    )
    runner = StaticRunner(
        RecordedOutput(argv=(), exit_code=0, stdout=_CODEX_SUCCESS)
    )
    adapter = phase0_cli_adapter(
        CODEX_ADAPTER, runner, allow_paid=True, argv_options={"parser_options": {"usage_estimator": lambda tokens: sum(tokens.values()) * 1e-6}}
    )
    task = _task(tmp_path, paid=True, cap=1.0, attempt_budget=0.5)
    outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
    assert outcome.status == "done"
    assert outcome.accounting_result == "reconciled"
    reserved = next(e for e in outcome.events if e["type"] == "budget_reserved")
    assert reserved["payload"]["amount_micros"] == 500000
    reported = next(e for e in outcome.events if e["type"] == "usage_reported")
    assert reported["payload"]["usage_micros"] == 1005
    account = BudgetLedger(controller.db).account(task.task_id)
    assert account["spent_micros"] == 1005


def test_p09_controller_blocks_paid_adapter_without_gate(tmp_path):
    from test_phase0_control import acceptance_ok, make_controller, make_registry

    controller = make_controller(
        tmp_path,
        data=_policy_data_for("openai-codex/gpt-5-fixture"),
        registry=make_registry(
            extra=[("openai-codex", "gpt-5-fixture", ("low", "medium"), False)]
        ),
    )
    runner = StaticRunner(RecordedOutput(argv=(), exit_code=0, stdout=_CODEX_SUCCESS))
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    task = _task(tmp_path, paid=True, cap=1.0, attempt_budget=0.5)
    outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
    assert outcome.status == "blocked"
    assert outcome.reason.startswith("runner_exception")
    assert runner.calls == []


def test_p09_unparseable_cli_output_is_lost(tmp_path):
    from test_phase0_control import acceptance_ok, make_controller, make_registry

    controller = make_controller(
        tmp_path,
        data=_policy_data_for("openai-codex/gpt-5-fixture"),
        registry=make_registry(
            extra=[("openai-codex", "gpt-5-fixture", ("low", "medium"), False)]
        ),
    )
    runner = StaticRunner(
        RecordedOutput(argv=(), exit_code=0, stdout="semi-parsed garbage")
    )
    adapter = phase0_cli_adapter(CODEX_ADAPTER, runner)
    task = _task(tmp_path, paid=False)
    outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
    assert outcome.status == "failed"
    assert outcome.reason == "attempt_lost"
    assert outcome.execution_result == "lost"
    assert outcome.accounting_result == "not_reserved"
