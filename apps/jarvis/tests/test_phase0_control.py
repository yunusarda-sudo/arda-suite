"""Phase 0 (M1) fake-runner contract harness tests (P01-P14).

Every test drives deterministic, caller-injected runners; no real model or
provider call is performed. Scenario tests are named with their P-code so
the acceptance report can map them one-to-one onto the Phase 0 matrix.
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arda.autonomous_work_loop import (
    AutonomousWorkLoop,
    CommandEvaluator,
    GitWorktree,
    WorkTask,
)
from arda.core import Database
from arda.phase0_control import (
    ACCEPTANCE_FAILED,
    ACCEPTANCE_NOT_RUN,
    ACCEPTANCE_PASSED,
    ACCOUNTING_INCOMPLETE,
    ACCOUNTING_NOT_RESERVED,
    ACCOUNTING_OVER_CAP,
    ACCOUNTING_RECONCILED,
    BudgetExceeded,
    BudgetLedger,
    EXECUTION_EXIT_0,
    EXECUTION_LOST,
    EXECUTION_NOT_STARTED,
    EXECUTION_TIMEOUT,
    EXECUTION_UNKNOWN,
    EffortUnsupported,
    ModelDescriptor,
    ModelRegistry,
    ModelUnresolved,
    Phase0Controller,
    PolicyUnreadable,
    RunObservation,
    TaskContract,
    TASK_BLOCKED,
    TASK_DENIED,
    TASK_DONE,
    TASK_FAILED,
    ensure_schema,
    load_policy_file,
    scope_error,
    to_micros,
    validate_evidence,
    VERDICT_INSUFFICIENT,
    VERDICT_OK,
    VERDICT_STALE,
    VERDICT_VERSION_MISMATCH,
)

BASE_CLOCK = datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc)
ARCHIVE_SHA = "a" * 40


def make_db(tmp_path: Path, name: str = "phase0.sqlite3") -> Database:
    db = Database(tmp_path / name)
    db.migrate()
    return db


def policy_path(
    tmp_path: Path, data: dict | None = None, *, raw: str | None = None
) -> Path:
    path = tmp_path / "policy.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(json.dumps(data or _policy_data()), encoding="utf-8")
    return path


def _policy_data(**overrides) -> dict:
    data = {
        "policy_version": "phase0-policy-v1",
        "allowed_models": ["fake/mock-small", "fake/mock-large"],
        "allowed_efforts": ["low", "medium"],
        "max_attempts_for_task": 2,
        "parallel_attempts": 2,
    }
    data.update(overrides)
    return data


def make_clock(state: dict | None = None):
    state = state if state is not None else {"now": BASE_CLOCK}

    def clock():
        return state["now"]

    return state, clock


def make_registry(**overrides) -> ModelRegistry:
    descriptors = [
        ModelDescriptor("fake", "mock-small", ("low", "medium")),
        ModelDescriptor("fake", "mock-large", ("low", "medium", "high")),
    ]
    for provider, model, efforts, paid in overrides.pop("extra", []):
        descriptors.append(ModelDescriptor(provider, model, efforts, paid=paid))
    return ModelRegistry(descriptors, version="phase0-registry-v1")


def make_controller(
    tmp_path: Path,
    *,
    data: dict | None = None,
    raw: str | None = None,
    clock=None,
    registry: ModelRegistry | None = None,
    lease_seconds: int = 600,
    **kwargs,
) -> Phase0Controller:
    db = make_db(tmp_path)
    clock = clock or (lambda: BASE_CLOCK)
    registry = registry or make_registry()
    path = policy_path(tmp_path, data, raw=raw)
    return Phase0Controller(
        db,
        registry=registry,
        policy_path=path,
        clock=clock,
        lease_seconds=lease_seconds,
        **kwargs,
    )


def make_task(tmp_path: Path, **overrides) -> TaskContract:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    fields = {
        "task_id": "t-scenario",
        "provider": "fake",
        "model": "mock-small",
        "effort": "low",
        "cap": 1.0,
        "attempt_budget": 0.7,
        "workspace": workspace,
    }
    fields.update(overrides)
    return TaskContract(**fields)


def recording_adapter(log: list, **observation) :
    def adapter(task, attempt_id, workspace):
        log.append(
            {
                "attempt_id": attempt_id,
                "provider": task.provider,
                "model": task.model,
                "effort": task.effort,
            }
        )
        return RunObservation(**observation)

    return adapter


def acceptance_ok(task, workspace):
    return (True, ({"command": ["fake-ci"], "exit_code": 0},))


def event(controller, task_id: str, event_type: str) -> dict:
    matches = [
        e
        for e in controller.events(task_id)
        if e["type"] == event_type
    ]
    assert matches, f"expected {event_type} for {task_id}"
    return matches[-1]


# --------------------------------------------------------------------------
# Primitives: registry and fail-closed policy loader
# --------------------------------------------------------------------------


def test_model_registry_rejects_unknown_model():
    registry = make_registry()
    with pytest.raises(ModelUnresolved) as error:
        registry.resolve("fake", "mock-missing", "low")
    assert "mock-missing" in str(error.value)


def test_model_registry_rejects_unsupported_effort():
    registry = make_registry()
    with pytest.raises(EffortUnsupported):
        registry.resolve("fake", "mock-small", "high")
    assert registry.supported_efforts("fake", "mock-small") == ("low", "medium")


def test_load_policy_file_happy_path(tmp_path):
    policy = load_policy_file(policy_path(tmp_path))
    assert policy.policy_version == "phase0-policy-v1"
    assert ("fake", "mock-small") in policy.allowed_models
    assert policy.max_attempts_for_task == 2


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[]",
        "{}",
        json.dumps({k: v for k, v in _policy_data().items() if k != "allowed_efforts"}),
        json.dumps({**_policy_data(), "unexpected": True}),
        json.dumps(_policy_data(max_attempts_for_task=0)),
        json.dumps(_policy_data(parallel_attempts=4)),
        json.dumps(_policy_data(allowed_models=["fake-only"])),
        json.dumps(_policy_data(allowed_models="fake/mock-small")),
    ],
)
def test_load_policy_file_fail_closed(tmp_path, raw):
    with pytest.raises(PolicyUnreadable):
        load_policy_file(policy_path(tmp_path, raw=raw))


def test_load_policy_file_missing_is_fail_closed(tmp_path):
    with pytest.raises(PolicyUnreadable):
        load_policy_file(tmp_path / "absent.json")


# --------------------------------------------------------------------------
# Evidence validator (P08 primitives)
# --------------------------------------------------------------------------


def test_validate_evidence_verdicts():
    base = {
        "upstream_version": "1.2.3",
        "repo_version": "1.2.3",
        "fresh_at": "2026-10-19T08:00:00+00:00",
        "source_refs": ["https://example.com/a"],
    }
    assert validate_evidence(base, required_version="1.2.3")[0] == VERDICT_OK
    drifted = {**base, "upstream_version": "1.3.0"}
    assert validate_evidence(drifted, required_version="1.2.3")[0] == VERDICT_VERSION_MISMATCH
    stale = {**base, "fresh_at": "2026-10-18T08:00:00+00:00"}
    assert validate_evidence(
        stale, required_version="1.2.3", fresh_after=datetime(2026, 10, 19, tzinfo=timezone.utc)
    )[0] == VERDICT_STALE
    assert validate_evidence({}, required_version="1.2.3")[0] == VERDICT_INSUFFICIENT
    insufficient = {**base, "source_refs": []}
    assert validate_evidence(insufficient, required_version="1.2.3")[0] == VERDICT_INSUFFICIENT
    bad_version = {**base, "repo_version": "dev"}
    assert validate_evidence(bad_version, required_version="1.2.3")[0] == VERDICT_INSUFFICIENT


# --------------------------------------------------------------------------
# P01 / P02: registry is versioned and enforcement happens before spawn
# --------------------------------------------------------------------------


def test_p01_unknown_model_denied_before_spawn(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path, model="mock-nonexistent")
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_DENIED
    assert outcome.policy_result == "denied"
    assert outcome.execution_result == EXECUTION_NOT_STARTED
    assert outcome.acceptance_result == ACCEPTANCE_NOT_RUN
    assert outcome.accounting_result == ACCOUNTING_NOT_RESERVED
    assert outcome.provider_requests == 0
    assert log == []
    payload = event(controller, task.task_id, "model_resolved")["payload"]
    assert payload["result"] == "model_unresolved"
    assert payload["model"] == "mock-nonexistent"
    assert controller.ledger.has_unresolved() is False


def test_p02_unsupported_effort_denied_and_preserved(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path, effort="high")
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_DENIED
    assert outcome.execution_result == EXECUTION_NOT_STARTED
    assert log == []
    payload = event(controller, task.task_id, "model_resolved")["payload"]
    assert payload["result"] == "effort_unsupported"
    assert payload["requested_effort"] == "high"
    assert payload["supported_efforts"] == ["low", "medium"]


def test_p02_effort_not_in_policy_allowlist(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path, model="mock-large", effort="high")
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_DENIED
    assert log == []
    payload = event(controller, task.task_id, "model_resolved")["payload"]
    assert payload["result"] == "effort_not_allowed"
    assert payload["requested_effort"] == "high"


def test_model_resolved_event_carries_registry_version(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    controller.run(
        task,
        adapter=recording_adapter(
            log, exit_code=0, claimed_done=True, usage=0.01, changed_paths=("src/a.py",)
        ),
        acceptance=acceptance_ok,
    )
    payload = event(controller, task.task_id, "model_resolved")["payload"]
    assert payload["registry_version"] == "phase0-registry-v1"


# --------------------------------------------------------------------------
# P03: policy file errors fail closed
# --------------------------------------------------------------------------


def test_p03_corrupt_policy_fails_closed(tmp_path):
    controller = make_controller(tmp_path, raw="{broken json")
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_DENIED
    assert outcome.policy_result == "denied"
    assert outcome.execution_result == EXECUTION_NOT_STARTED
    assert outcome.accounting_result == ACCOUNTING_NOT_RESERVED
    assert outcome.provider_requests == 0
    assert log == []
    assert "policy_unreadable" in outcome.reason
    record = event(controller, task.task_id, "policy_checked")
    assert record["payload"]["result"] == "denied"
    assert "policy_unreadable" in record["payload"]["reason"]
    assert record["policy_version"] is None


# --------------------------------------------------------------------------
# P04 / P05: fallback allowlist + shared budget
# --------------------------------------------------------------------------


def _fail_first_attempt(controller, task, log, *, usage=0.05):
    return controller.run(
        task,
        adapter=recording_adapter(
            log, exit_code=3, claimed_done=False, usage=usage, changed_paths=("src/a.py",)
        ),
        acceptance=acceptance_ok,
    )


def test_p04_unapproved_fallback_never_reaches_paid_transport(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    first = _fail_first_attempt(controller, task, log)
    assert first.status == TASK_FAILED
    spent_before = controller.ledger.account(task.task_id)["spent_micros"]
    assert spent_before == to_micros(0.05)

    outcome = controller.attempt_fallback(
        task,
        target=("acme", "hidden-model", "low"),
        adapter=recording_adapter(log, exit_code=0),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DENIED
    assert outcome.reason.startswith("fallback")
    assert outcome.provider_requests == 0
    assert controller.ledger.account(task.task_id)["spent_micros"] == spent_before
    assert controller.ledger.attempts(task.task_id) == 1
    payload = event(controller, task.task_id, "fallback_decision")["payload"]
    assert payload["result"] == "denied"
    assert payload["reason"] == "fallback_not_in_policy_allowlist"
    assert payload["target"] == {
        "provider": "acme",
        "model": "hidden-model",
        "effort": "low",
    }
    # TheForbidden target must not appear in any spawned invocation.
    assert [call["model"] for call in log] == ["mock-small"]


def test_p04_fallback_effort_rejected_by_policy(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    _fail_first_attempt(controller, task, log)
    outcome = controller.attempt_fallback(
        task,
        target=("fake", "mock-large", "high"),
        adapter=recording_adapter(log, exit_code=0),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DENIED
    payload = event(controller, task.task_id, "fallback_decision")["payload"]
    assert payload["reason"] == "fallback_not_in_policy_allowlist"
    assert [call["effort"] for call in log] == ["low"]


def test_p05_approved_fallback_re_reserves_budget_and_keeps_first_spend(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    first = _fail_first_attempt(controller, task, log)
    assert first.status == TASK_FAILED

    outcome = controller.attempt_fallback(
        task,
        target=("fake", "mock-large", "medium"),
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-large",
            observed_effort="medium",
            usage=0.20,
            changed_paths=("src/b.py",),
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DONE
    assert outcome.acceptance_result == ACCEPTANCE_PASSED
    assert outcome.accounting_result == ACCOUNTING_RECONCILED
    assert outcome.provider_requests == 1
    assert [e["type"] for e in outcome.events].count("budget_reserved") == 1
    task_events = controller.events(task.task_id)
    resolved_events = [e for e in task_events if e["type"] == "model_resolved"]
    assert len(resolved_events) == 2
    assert resolved_events[0]["payload"]["model"] == "mock-small"
    assert resolved_events[1]["payload"]["model"] == "mock-large"
    fallback = event(controller, task.task_id, "fallback_decision")["payload"]
    assert fallback["result"] == "allowed"
    assert fallback["target"] == {
        "provider": "fake",
        "model": "mock-large",
        "effort": "medium",
    }
    payload = event(controller, task.task_id, "budget_reserved")["payload"]
    assert payload["result"] == "active"
    assert payload["amount_micros"] == to_micros(0.7)
    models = [call["model"] for call in log]
    assert models == ["mock-small", "mock-large"]
    expected_spent = to_micros(0.05) + to_micros(0.20)
    assert controller.ledger.account(task.task_id)["spent_micros"] == expected_spent
    assert controller.ledger.attempts(task.task_id) == 2
    assert controller.ledger.has_unresolved() is False


def test_p05_fallback_blocked_when_first_spend_is_unreconciled(tmp_path):
    controller = make_controller(tmp_path, data=_policy_data(max_attempts_for_task=3))
    log: list = []
    task = make_task(tmp_path)
    _fail_first_attempt(controller, task, log, usage=None)
    assert controller.ledger.has_unresolved() is True
    outcome = controller.attempt_fallback(
        task,
        target=("fake", "mock-large", "medium"),
        adapter=recording_adapter(log, exit_code=0),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DENIED
    assert "unreconciled" in outcome.reason
    assert outcome.provider_requests == 0
    assert [call["model"] for call in log] == ["mock-small"]


def test_fallback_respects_max_attempt_limit(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    _fail_first_attempt(controller, task, log)
    second = controller.attempt_fallback(
        task,
        target=("fake", "mock-large", "medium"),
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-large",
            observed_effort="medium",
            usage=0.20,
            changed_paths=("src/b.py",),
        ),
        acceptance=acceptance_ok,
    )
    assert second.status == TASK_DONE
    outcome = controller.attempt_fallback(
        task,
        target=("fake", "mock-large", "medium"),
        adapter=recording_adapter(log, exit_code=0),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DENIED
    payload = event(controller, task.task_id, "fallback_decision")["payload"]
    assert payload["reason"] == "attempt_limit_reached"


# --------------------------------------------------------------------------
# P06: atomic budget reservation
# --------------------------------------------------------------------------


def test_p06_ledger_denies_over_cap(tmp_path):
    db = make_db(tmp_path, name="ledger-over-cap.sqlite3")
    ensure_schema(db)
    ledger = BudgetLedger(db, clock=lambda: BASE_CLOCK)
    ledger.ensure_account("t-cap", to_micros(1.0))
    ledger.reserve("t-cap", "job-1", 1, to_micros(0.7))
    with pytest.raises(BudgetExceeded):
        ledger.reserve("t-cap", "job-2", 1, to_micros(0.7))
    ledger.reconcile("job-1", 1, usage_micros=to_micros(0.5))
    assert ledger.account("t-cap")["spent_micros"] == to_micros(0.5)
    assert ledger.account("t-cap")["reserved_micros"] == 0
    with pytest.raises(BudgetExceeded):
        ledger.reserve("t-cap", "job-3", 1, to_micros(0.7))
    with pytest.raises(BudgetExceeded):
        ledger.reserve("t-cap", "job-4", 1, to_micros(to_micros(0.6)))


def test_p06_concurrent_reservation_is_atomic(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    seeded = Database(path)
    seeded.migrate()
    ensure_schema(seeded)
    ledger_seed = BudgetLedger(seeded, clock=lambda: BASE_CLOCK)
    ledger_seed.ensure_account("cap-task", to_micros(1.0))
    bar = threading.Barrier(3)
    granted: list[str] = []
    denied: list[str] = []

    def worker(index: int) -> None:
        db = Database(path)
        ensure_schema(db)
        ledger = BudgetLedger(db, clock=lambda: BASE_CLOCK)
        bar.wait()
        try:
            reservation = ledger.reserve("cap-task", f"job-{index}", 1, to_micros(0.7))
            granted.append(reservation.reservation_id)
        except BudgetExceeded as error:
            denied.append(str(error))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 1
    assert len(denied) == 2
    final = Database(path)
    ensure_schema(final)
    account = BudgetLedger(final, clock=lambda: BASE_CLOCK).account("cap-task")
    assert account["reserved_micros"] == to_micros(0.7)
    assert account["spent_micros"] == 0


def test_p06_controller_blocks_second_attempt_over_cap(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    first = controller.run(
        task,
        adapter=recording_adapter(
            log, exit_code=3, claimed_done=False, usage=0.5, changed_paths=("src/a.py",)
        ),
        acceptance=acceptance_ok,
    )
    assert first.status == TASK_FAILED
    second = controller.run(task, adapter=recording_adapter(log, exit_code=0))
    assert second.status == TASK_BLOCKED
    assert second.accounting_result == ACCOUNTING_OVER_CAP
    assert second.execution_result == EXECUTION_NOT_STARTED
    assert second.provider_requests == 0
    assert "budget_exceeded" in second.reason
    payload = event(controller, task.task_id, "budget_reserved")["payload"]
    assert payload["result"] == "denied"
    assert [call["model"] for call in log] == ["mock-small"]


# --------------------------------------------------------------------------
# P07: research gate
# --------------------------------------------------------------------------


def test_p07_missing_research_decision_blocks_spawn(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(
        tmp_path, research_required=True, research_decision_id="dec-p07"
    )
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_BLOCKED
    assert outcome.policy_result == "needs_research"
    assert outcome.execution_result == EXECUTION_NOT_STARTED
    assert outcome.provider_requests == 0
    assert log == []
    payload = event(controller, task.task_id, "research_validated")["payload"]
    assert payload["verdict"] == "no_decision"


def test_p07_registered_evidence_admits_attempt(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(
        tmp_path, research_required=True, research_decision_id="dec-p07ok"
    )
    verdict = controller.register_research(
        task.task_id,
        "dec-p07ok",
        {
            "upstream_version": "1.2.3",
            "repo_version": "1.2.3",
            "fresh_at": "2026-10-19T07:30:00+00:00",
            "source_refs": ["https://example.com/doc"],
        },
        required_version="1.2.3",
    )
    assert verdict == VERDICT_OK
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log, exit_code=0, claimed_done=True, usage=0.01, changed_paths=("src/a.py",)
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DONE
    assert any(e["type"] == "process_spawned" for e in outcome.events)
    payload = event(controller, task.task_id, "research_validated")["payload"]
    assert payload["satisfied"] is True


# --------------------------------------------------------------------------
# P08: version-mismatched evidence is rejected
# --------------------------------------------------------------------------


def test_p08_version_mismatch_evidence_blocks_review(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(
        tmp_path,
        research_required=True,
        research_decision_id="dec-p08",
        required_version="1.2.3",
    )
    controller.register_research(
        task.task_id,
        "dec-p08",
        {
            "upstream_version": "1.3.0",
            "repo_version": "1.3.0",
            "fresh_at": "2026-10-19T07:30:00+00:00",
            "source_refs": ["https://example.com/doc"],
        },
        required_version="1.2.3",
    )
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_BLOCKED
    assert outcome.policy_result == "needs_review"
    assert "version_mismatch" in outcome.reason
    assert outcome.provider_requests == 0
    assert log == []
    payload = event(controller, task.task_id, "research_validated")["payload"]
    assert payload["verdict"] == "version_mismatch"


def test_p08_stale_evidence_blocks_review(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(
        tmp_path,
        research_required=True,
        research_decision_id="dec-p08stale",
        required_version="1.2.3",
        fresh_after=datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc),
    )
    controller.register_research(
        task.task_id,
        "dec-p08stale",
        {
            "upstream_version": "1.2.3",
            "repo_version": "1.2.3",
            "fresh_at": "2026-10-19T06:00:00+00:00",
            "source_refs": ["https://example.com/doc"],
        },
        required_version="1.2.3",
        fresh_after=datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc),
    )
    outcome = controller.run(task, adapter=recording_adapter(log))
    assert outcome.status == TASK_BLOCKED
    assert outcome.policy_result == "needs_review"
    assert "stale" in outcome.reason


# --------------------------------------------------------------------------
# P09: exit 0 with failing evaluator is rejected
# --------------------------------------------------------------------------


def test_p09_exit0_with_failing_evaluator_is_rejected(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    evaluator = CommandEvaluator(
        [["python3", "-c", "import sys; sys.exit(1)"]]
    )
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-small",
            usage=0.02,
            changed_paths=("src/a.py",),
        ),
        acceptance=lambda task_, workspace: evaluator.evaluate(
            _dummy_worktask(), workspace
        ),
    )
    assert outcome.execution_result == EXECUTION_EXIT_0
    assert outcome.acceptance_result == ACCEPTANCE_FAILED
    assert outcome.status == TASK_FAILED
    assert "acceptance failed" in outcome.reason
    payload = event(controller, task.task_id, "acceptance_checked")["payload"]
    assert payload["result"] == "failed"
    assert payload["exit_code"] == 0


def test_p09_passing_evaluator_with_exit0_completes(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    evaluator = CommandEvaluator([["python3", "-c", "import sys; sys.exit(0)"]])
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-small",
            usage=0.02,
            changed_paths=("src/a.py",),
        ),
        acceptance=lambda task_, workspace: evaluator.evaluate(
            _dummy_worktask(), workspace
        ),
    )
    assert outcome.status == TASK_DONE
    assert outcome.acceptance_result == ACCEPTANCE_PASSED
    assert outcome.accounting_result == ACCOUNTING_RECONCILED
    job = controller.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (outcome.job_id,)
    ).fetchone()
    assert job["status"] == "succeeded"


def _dummy_worktask() -> WorkTask:
    return WorkTask(
        task_id="acceptance-probe",
        repo="/tmp/acceptance-probe",
        baseline_ref="HEAD",
        baseline_sha=ARCHIVE_SHA,
        objective="independent acceptance",
        acceptance=("acceptance-probe",),
    )


# --------------------------------------------------------------------------
# P10: scope violations
# --------------------------------------------------------------------------


def test_p10_forbidden_path_change_rejects_attempt(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(
        tmp_path,
        allowed_paths=("src/**",),
        forbidden_paths=(".github/workflows/*",),
    )
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            usage=0.02,
            changed_paths=(".github/workflows/deploy.yml",),
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert outcome.acceptance_result == ACCEPTANCE_FAILED
    assert outcome.reason.startswith("scope_violation: forbidden path changed")
    payload = event(controller, task.task_id, "diff_checked")["payload"]
    assert payload["result"] == "violation"
    artifact_total = controller.db.scalar(
        "SELECT COUNT(*) FROM artifacts WHERE job_id = ?", (outcome.job_id,)
    )
    assert artifact_total == 1


def test_p10_unrelated_path_change_rejects_attempt(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path, allowed_paths=("src/**",))
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            usage=0.02,
            changed_paths=("README.md",),
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert outcome.reason.startswith("scope_violation: unrelated path changed")


def test_p10_clean_scope_completes(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(
        tmp_path, allowed_paths=("src/**",), forbidden_paths=(".env*",)
    )
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-small",
            usage=0.02,
            changed_paths=("src/site.py",),
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DONE


def test_scope_semantics_match_work_loop():
    sha = "a" * 40
    cases = [
        ((), (), ("src/a.py",), None),
        (("src/**",), (), ("README.md",), "unrelated path changed"),
        (("src/**",), ("deploy/**",), ("deploy/x.yml",), "forbidden path changed"),
        (("src/**",), (), (), None),
    ]
    for allowed, forbidden, changed, expected in cases:
        work_task = WorkTask(
            task_id="parity",
            repo="repo",
            baseline_ref="HEAD",
            baseline_sha=sha,
            objective="parity",
            acceptance=(),
            allowed_paths=allowed,
            forbidden_paths=forbidden,
        )
        assert AutonomousWorkLoop._scope_error(work_task, changed) == expected
        assert scope_error(allowed, forbidden, changed) == expected


def test_p10_real_worktree_side_effect_is_detected(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    (repo / "src" / "app.py").write_text("print('ok')\n")
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    sha = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    worktrees = GitWorktree(tmp_path / "worktrees")
    work_task = WorkTask(
        task_id="real-scope",
        repo=str(repo),
        baseline_ref=sha,
        baseline_sha=sha,
        objective="scope probe",
        acceptance=("real-scope",),
    )
    workspace = worktrees.create(work_task)
    try:
        (workspace / "deploy_this").mkdir()
        (workspace / "deploy_this" / "pipeline.yml").write_text("key: value\n")
        changed = AutonomousWorkLoop._changed_paths(work_task, workspace)
        assert changed == ("deploy_this/pipeline.yml",)

        controller = make_controller(tmp_path)
        task = TaskContract(
            task_id="real-scope",
            provider="fake",
            model="mock-small",
            effort="low",
            cap=1.0,
            attempt_budget=0.7,
            workspace=workspace,
            allowed_paths=("src/**",),
            forbidden_paths=("deploy_this/**",),
        )

        def adapter(contract, attempt_id, ws_home):
            assert ws_home == workspace
            return RunObservation(
                exit_code=0,
                claimed_done=True,
                observed_model="fake/mock-small",
                usage=0.02,
                changed_paths=changed,
            )

        outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
        assert outcome.status == TASK_FAILED
        assert outcome.reason.startswith("scope_violation")
        assert event(controller, "real-scope", "diff_checked")["payload"]["result"] == "violation"
    finally:
        try:
            worktrees.remove(repo, workspace)
        except Exception:
            pass


# --------------------------------------------------------------------------
# P11: heartbeat loss and reconcile
# --------------------------------------------------------------------------


def test_p11_heartbeat_loss_reconciles_without_silent_retry(tmp_path):
    state, clock = make_clock()
    controller = make_controller(tmp_path, clock=clock, lease_seconds=120)
    log: list = []
    task = make_task(tmp_path)

    def lost_adapter(contract, attempt_id, workspace):
        log.append({"attempt_id": attempt_id})
        state["now"] += timedelta(seconds=301)
        return RunObservation(lost=True, side_effect_state="unknown")

    outcome = controller.run(task, adapter=lost_adapter, acceptance=acceptance_ok)
    assert outcome.status == TASK_FAILED
    assert outcome.execution_result == EXECUTION_LOST
    assert outcome.acceptance_result == ACCEPTANCE_NOT_RUN
    assert outcome.accounting_result == ACCOUNTING_INCOMPLETE
    assert outcome.reason == "attempt_lost"
    reconcile = event(controller, task.task_id, "reconcile_started")["payload"]
    assert reconcile["reason"] == "runner lost before completion"
    side_effect = event(controller, task.task_id, "side_effects_inspected")["payload"]
    assert side_effect["side_effect_state"] == "unknown"
    reservation = controller.ledger.reservation(outcome.job_id, 1)
    assert reservation.status == "unresolved"
    assert reservation.unresolved is True
    job = controller.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (outcome.job_id,)
    ).fetchone()
    assert job["status"] == "failed"
    follow_up = controller.run(task, adapter=recording_adapter(log, exit_code=0))
    assert follow_up.status == TASK_DENIED
    assert "unreconciled" in follow_up.reason


def test_p11_lost_attempt_with_active_lease_fails_explicitly(tmp_path):
    controller = make_controller(tmp_path, lease_seconds=600)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(log, lost=True, side_effect_state="unknown"),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert outcome.execution_result == EXECUTION_LOST
    assert event(controller, task.task_id, "side_effects_inspected")["payload"][
        "side_effect_state"
    ] == "unknown"


# --------------------------------------------------------------------------
# P12: runtime risk escalation
# --------------------------------------------------------------------------


def test_p12_sensitive_write_requires_risk_escalation(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(tmp_path, risk="low")
    decision = controller.write_check(task, "migrations/0002_init.sql")
    assert decision.allowed is False
    assert decision.reason == "risk_escalation_required"
    assert decision.escalated_risk == "high"
    assert decision.side_effect == "none"
    payload = event(controller, task.task_id, "policy_checked")["payload"]
    assert payload["result"] == "denied"
    assert payload["escalated_risk"] == "high"
    assert "0002_init.sql" in payload["path"]
    sensitive_file = task.workspace / "migrations" / "0002_init.sql"
    assert sensitive_file.exists() is False


def test_p12_high_risk_task_may_write_sensitive_path(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(tmp_path, risk="high")
    decision = controller.write_check(task, "migrations/0002_init.sql")
    assert decision.allowed is True
    assert decision.escalated_risk is None


def test_p12_adapters_must_bypass_denied_writes(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path, allowed_paths=("src/**",), forbidden_paths=(".env*",))
    writes: list[str] = []

    def adapter(contract, attempt_id, workspace):
        for path in ("src/feature.py", ".env.production", "auth/session.py"):
            decision = controller.write_check(contract, path, attempt_id=attempt_id)
            if decision.allowed:
                (workspace / path).parent.mkdir(parents=True, exist_ok=True)
                (workspace / path).write_text("content\n")
                writes.append(path)
        return RunObservation(exit_code=0, claimed_done=True, usage=0.01, changed_paths=())

    outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
    assert outcome.status == TASK_DONE
    assert writes == ["src/feature.py"]
    assert not (task.workspace / ".env.production").exists()
    assert not (task.workspace / "auth").exists()


def test_p12_boundary_forbidden_and_scope_writes(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(
        tmp_path, allowed_paths=("src/**",), forbidden_paths=(".github/*",)
    )
    denied_forbidden = controller.write_check(task, ".github/workflows/deploy.yml")
    assert denied_forbidden.allowed is False
    assert denied_forbidden.reason == "forbidden_path"
    denied_scope = controller.write_check(task, "docs/notes.md")
    assert denied_scope.allowed is False
    assert denied_scope.reason == "outside_allowed_scope"
    allowed = controller.write_check(task, "src/ui.py")
    assert allowed.allowed is True


# --------------------------------------------------------------------------
# P13: unknown tools denied by default
# --------------------------------------------------------------------------


def test_p13_unknown_tool_denied_without_side_effect(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(tmp_path)
    decision = controller.tool_call(task, "shell_root_admin")
    assert decision.allowed is False
    assert decision.reason == "unknown_tool_denied"
    assert decision.side_effect == "none"
    payload = event(controller, task.task_id, "tool_denied")["payload"]
    assert payload["tool"] == "shell_root_admin"
    assert payload["reason"] == "unknown_tool_denied"


def test_p13_allowlisted_tool_passes(tmp_path):
    controller = make_controller(tmp_path, allowed_tools=("read_file",))
    task = make_task(tmp_path)
    decision = controller.tool_call(task, "read_file")
    assert decision.allowed is True
    assert controller.events(task.task_id) == ()


# --------------------------------------------------------------------------
# P14: missing usage stays unresolved and gates new paid work
# --------------------------------------------------------------------------


def test_p14_missing_usage_never_becomes_zero(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(log, exit_code=0, claimed_done=True, usage=None),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DONE
    assert outcome.accounting_result == ACCOUNTING_INCOMPLETE
    reservation = controller.ledger.reservation(outcome.job_id, 1)
    assert reservation.status == "unresolved"
    assert reservation.amount_micros == to_micros(0.7)
    assert controller.ledger.account(task.task_id)["spent_micros"] == 0
    assert controller.ledger.has_unresolved() is True
    assert controller.events(task.task_id) and not [
        e for e in controller.events(task.task_id) if e["type"] == "usage_reported"
    ]
    payload = event(controller, task.task_id, "accounting_unresolved")["payload"]
    assert payload["status"] == "unresolved"


def test_p14_unresolved_spend_blocks_new_paid_attempts(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path, task_id="t-tagged")
    controller.run(
        task,
        adapter=recording_adapter(log, exit_code=0, claimed_done=True, usage=None),
        acceptance=acceptance_ok,
    )
    other = make_task(tmp_path, task_id="t-second")
    outcome_other = controller.run(
        other,
        adapter=recording_adapter(log, exit_code=0),
        acceptance=acceptance_ok,
    )
    assert outcome_other.status == TASK_DENIED
    assert "unreconciled" in outcome_other.reason
    assert outcome_other.provider_requests == 0
    assert [call["model"] for call in log] == ["mock-small"]


def test_p14_partial_usage_is_reconciled_only_when_reported(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log, exit_code=0, claimed_done=True, usage=0.15
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.accounting_result == ACCOUNTING_RECONCILED
    account = controller.ledger.account(task.task_id)
    assert account["spent_micros"] == to_micros(0.15)
    assert account["reserved_micros"] == 0


# --------------------------------------------------------------------------
# Controller contract: four separated results and universals
# --------------------------------------------------------------------------


def test_positive_flow_completes_with_all_four_results(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-small",
            usage=0.03,
            changed_paths=("src/a.py",),
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DONE
    assert outcome.policy_result == "allowed"
    assert outcome.execution_result == EXECUTION_EXIT_0
    assert outcome.acceptance_result == ACCEPTANCE_PASSED
    assert outcome.accounting_result == ACCOUNTING_RECONCILED
    assert outcome.attempt_id == f"{task.task_id}-attempt-1"
    assert outcome.provider_requests == 1
    types = [e["type"] for e in outcome.events]
    assert "attempt_leased" in types and "task_finalized" in types


def test_runner_unknown_exit_state_is_reported(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(log, exit_code=None),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert outcome.execution_result == EXECUTION_UNKNOWN
    assert outcome.acceptance_result == ACCEPTANCE_NOT_RUN


def test_runner_exception_reports_unknown_side_effects(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(tmp_path)

    def adapter(contract, attempt_id, workspace):
        raise RuntimeError("simulated runner crash")

    outcome = controller.run(task, adapter=adapter, acceptance=acceptance_ok)
    assert outcome.status == TASK_BLOCKED
    assert outcome.execution_result == EXECUTION_UNKNOWN
    assert outcome.accounting_result == ACCOUNTING_INCOMPLETE
    assert "runner_exception" in outcome.reason
    side_effect = event(controller, task.task_id, "side_effects_inspected")["payload"]
    assert side_effect["side_effect_state"] == "unknown"
    reservation = controller.ledger.reservation(outcome.job_id, 1)
    assert reservation.status == "unresolved"


def test_runner_timeout_fails_attempt(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log, exit_code=None, timed_out=True, side_effect_state="unknown"
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert outcome.execution_result == EXECUTION_TIMEOUT
    payload = event(controller, task.task_id, "side_effects_inspected")["payload"]
    assert payload["side_effect_state"] == "unknown"


def test_p01_runtime_observed_model_mismatch_fails_attempt(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-large",
            observed_effort="low",
            usage=0.02,
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert "runtime_model_mismatch" in outcome.reason
    assert outcome.acceptance_result == ACCEPTANCE_NOT_RUN


def test_p02_runtime_observed_effort_mismatch_fails_attempt(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            log,
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-small",
            observed_effort="high",
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_FAILED
    assert "runtime_effort_mismatch" in outcome.reason


def test_missing_acceptance_never_marks_done(tmp_path):
    controller = make_controller(tmp_path)
    log: list = []
    task = make_task(tmp_path)
    outcome = controller.run(
        task,
        adapter=recording_adapter(log, exit_code=0, claimed_done=True, usage=0.01),
    )
    assert outcome.status == TASK_BLOCKED
    assert outcome.acceptance_result == ACCEPTANCE_NOT_RUN
    assert "independent acceptance required" in outcome.reason


def test_events_are_persisted_for_audit(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(tmp_path)
    controller.run(
        task,
        adapter=recording_adapter([], exit_code=0, claimed_done=True, usage=0.01),
        acceptance=acceptance_ok,
    )
    rows = controller.db.execute(
        "SELECT type, policy_version, task_id, attempt_id FROM phase0_events WHERE task_id = ?",
        (task.task_id,),
    ).fetchall()
    assert rows
    assert all(row["task_id"] == task.task_id for row in rows)
    assert all(row["policy_version"] == "phase0-policy-v1" for row in rows)
    assert {row["type"] for row in rows} >= {"task_started", "task_finalized"}


def test_unpaid_task_completes_without_ledger(tmp_path):
    controller = make_controller(tmp_path)
    task = make_task(tmp_path, task_id="t-free", paid=False)
    outcome = controller.run(
        task,
        adapter=recording_adapter(
            [],
            exit_code=0,
            claimed_done=True,
            observed_model="fake/mock-small",
            changed_paths=("src/a.py",),
        ),
        acceptance=acceptance_ok,
    )
    assert outcome.status == TASK_DONE
    assert outcome.accounting_result == ACCOUNTING_NOT_RESERVED
    assert controller.ledger.has_unresolved() is False
