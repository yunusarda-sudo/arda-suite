from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import pytest

from arda.awareness import ProjectRegistry, TaskStore
from arda.core import bootstrap_runtime_database
from arda.organization import OrganizationRegistry


def _mission(registry, **overrides):
    values = {
        "project_id": "sample-project",
        "title": "Stabilize OPVIA",
        "objective": "Resolve verified delivery blockers",
        "acceptance_metrics": ("CI green",),
        "allowed_repositories": ("acme/opvia",),
        "allowed_domains": ("github.com",),
        "delegated_scopes": ("signals.read", "github.read", "coding.propose", "coding.execute"),
        "allowed_risks": ("low", "medium"),
        "budget": {"max_tasks": 4, "max_task_cost": 2},
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "mission_id": "MISSION-OPVIA",
    }
    values.update(overrides)
    return registry.create_mission(**values)


def test_profile_objective_and_deterministic_ready_task_selection(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        tasks = TaskStore(db)
        low = tasks.create("sample-project", "Low impact", status="ready", priority="normal", task_id="TASK-LOW")
        high = tasks.create("sample-project", "High impact", status="ready", priority="high", task_id="TASK-HIGH")
        registry = OrganizationRegistry(db)
        registry.upsert_profile(
            "sample-project", mission="Make opportunities easier to find",
            accepted_baseline="a" * 40,
            policy={"push": "approval", "secrets": "deny"},
            budget={"hard_timeout_seconds": 900},
        )
        objective = registry.create_objective("sample-project", "Source reliability", status="active", weight=20,
                                              objective_id="OBJ-001")
        registry.set_task_meta(low["id"], user_impact=10, risk="low")
        registry.set_task_meta(high["id"], objective_id=objective["id"], user_impact=40, risk="medium")
        selected = registry.select_next_task("sample-project")
        assert selected["id"] == "TASK-HIGH"
        assert selected["selection_score"] == 30 + 20 + 40 - 5
        snapshot = registry.snapshot("sample-project")
        assert snapshot["profile"]["policy"]["secrets"] == "deny"
        assert snapshot["next_task"]["id"] == "TASK-HIGH"
    finally:
        db.close()


def test_mission_signal_proposal_are_scoped_deduplicated_and_budgeted(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        mission = _mission(registry)
        signal, inserted = registry.ingest_signal(
            mission["id"], provider="github", source_key="actions-run:41:attempt:1",
            signal_type="ci.failure", source_updated_at=datetime.now(timezone.utc).isoformat(),
            payload={"repo": "acme/opvia", "head_sha": "a" * 40, "conclusion": "failure"},
        )
        duplicate, inserted_again = registry.ingest_signal(
            mission["id"], provider="github", source_key="actions-run:41:attempt:1",
            signal_type="ci.failure", source_updated_at=datetime.now(timezone.utc).isoformat(),
            payload={"repo": "acme/opvia", "head_sha": "a" * 40, "conclusion": "failure"},
        )
        assert inserted is True
        assert inserted_again is False
        assert duplicate["id"] == signal["id"]
        assert len(registry.pending_signals(mission["id"])) == 1

        draft = {"repo": "acme/opvia", "objective": "Fix failing CI", "acceptance": ["CI passes"],
                 "risk": "low", "estimated_cost": 1, "allowed_paths": ["src/**"]}
        proposal, created = registry.record_proposal(
            mission["id"], signal["id"], draft=draft,
            rationale="A current CI run failed on the mission repository.", decision="accepted",
        )
        duplicate_proposal, created_again = registry.record_proposal(
            mission["id"], signal["id"], draft=draft,
            rationale="retry after restart", decision="accepted",
        )
        assert created is True
        assert created_again is False
        assert duplicate_proposal["id"] == proposal["id"]
        assert proposal["status"] == "accepted"
        assert json.loads(proposal["contract_json"]) == draft
        assert db.execute("SELECT status FROM director_signals WHERE id=?", (signal["id"],)).fetchone()[0] == "processed"
    finally:
        db.close()


def test_mission_rejects_unauthorized_signal_and_out_of_scope_proposal(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        mission = _mission(registry, delegated_scopes=("signals.read",))
        try:
            registry.ingest_signal(
                mission["id"], provider="github", source_key="run:1", signal_type="ci.failure",
                source_updated_at=datetime.now(timezone.utc).isoformat(),
                payload={"repo": "acme/opvia"},
            )
        except ValueError as exc:
            assert "delegate" in str(exc)
        else:
            raise AssertionError("GitHub signal was accepted without github.read scope")

        mission = registry.create_mission(
            "sample-project", "Second mission", "test scope", acceptance_metrics=("test",),
            allowed_repositories=("acme/opvia",), delegated_scopes=("github.read", "coding.propose"),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            mission_id="MISSION-SECOND",
        )
        signal, _ = registry.ingest_signal(
            mission["id"], provider="github", source_key="run:2", signal_type="ci.failure",
            source_updated_at=datetime.now(timezone.utc).isoformat(), payload={"repo": "acme/opvia"},
        )
        row, created = registry.record_proposal(
            mission["id"], signal["id"],
            draft={"repo": "attacker/other", "objective": "escape", "acceptance": ["done"],
                   "allowed_paths": ["src/**"], "risk": "low", "estimated_cost": 1},
            rationale="untrusted proposal", decision="accepted",
        )
        assert created is True
        assert row["status"] == "rejected"
        assert json.loads(row["decision_json"])["reason"] == "REPOSITORY_OUT_OF_SCOPE"
    finally:
        db.close()


def test_mission_pause_expiry_and_optimistic_state_transition(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        now = datetime.now(timezone.utc)
        current = [now]
        registry = OrganizationRegistry(db, clock=lambda: current[0])
        mission = _mission(registry, expires_at=(now + timedelta(hours=1)).isoformat())
        paused = registry.set_mission_state(mission["id"], "paused", expected_version=mission["version"], reason="operator pause")
        assert paused["status"] == "paused"
        assert registry.pending_signals(mission["id"]) == []
        with pytest.raises(ValueError, match="cannot transition"):
            registry.set_mission_state(mission["id"], "paused", expected_version=paused["version"], reason="again")
        current[0] = now + timedelta(hours=2)
        assert registry.mission(mission["id"])["status"] == "expired"
    finally:
        db.close()


def test_signal_freshness_uses_source_timestamp_and_rejects_future_sources(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        now = datetime.now(timezone.utc)
        registry = OrganizationRegistry(db, clock=lambda: now)
        mission = _mission(registry, expires_at=(now + timedelta(days=1)).isoformat())
        stale, inserted = registry.ingest_signal(
            mission["id"], provider="github", source_key="run:stale", signal_type="ci.failure",
            source_updated_at=(now - timedelta(hours=2)).isoformat(), payload={"repo": "acme/opvia"},
            freshness_seconds=3600,
        )
        assert inserted is True
        assert stale["status"] == "ignored"
        assert registry.pending_signals(mission["id"]) == []
        with pytest.raises(ValueError, match="future"):
            registry.ingest_signal(
                mission["id"], provider="github", source_key="run:future", signal_type="ci.failure",
                source_updated_at=(now + timedelta(minutes=3)).isoformat(), payload={"repo": "acme/opvia"},
            )
    finally:
        db.close()


def test_mission_creation_rejects_string_instead_of_collection(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        with pytest.raises(ValueError, match="acceptance_metrics"):
            _mission(OrganizationRegistry(db), acceptance_metrics="CI green")
    finally:
        db.close()


def test_paused_organization_has_no_next_task(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("example-project", "MODÜ-GRID", status="paused")
        TaskStore(db).create("example-project", "Ready task", status="ready", task_id="TASK-PAUSED")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("example-project", mission="paused")
        assert registry.select_next_task("example-project") is None
    finally:
        db.close()


def test_budget_exhausted_organization_has_no_next_task(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        TaskStore(db).create("sample-project", "Ready task", status="ready", task_id="TASK-BUDGET")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", budget={"max_active_jobs": 1})
        from arda.core import JobQueue

        job = JobQueue(db).enqueue(
            "autonomous-coding", "Already running", idempotency_key="budget-active",
            project_id="sample-project",
        )
        JobQueue(db).claim("budget-worker")
        assert registry.select_next_task("sample-project") is None
        assert job.status == "queued"
    finally:
        db.close()


def test_idle_organization_has_no_ready_task(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        TaskStore(db).create("sample-project", "Backlog task", status="backlog", task_id="TASK-IDLE")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project")
        assert registry.select_next_task("sample-project") is None
    finally:
        db.close()


def test_cross_project_objective_is_rejected_for_task_metadata(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        ProjectRegistry(db).create("other", "Other")
        task = TaskStore(db).create("sample-project", "Task", task_id="TASK-CROSS-PROJECT")
        objective = OrganizationRegistry(db).create_objective(
            "other", "Other objective", objective_id="OBJ-OTHER"
        )
        registry = OrganizationRegistry(db)
        try:
            registry.set_task_meta(task["id"], objective_id=objective["id"])
        except ValueError as exc:
            assert "project" in str(exc).lower()
        else:
            raise AssertionError("cross-project objective assignment must be rejected")
    finally:
        db.close()


def test_objective_lifecycle_auto_activation_and_completion(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", accepted_baseline="a" * 40)
        first = registry.create_objective("sample-project", "Source reliability", status="planned", weight=100,
                                          activation_policy="auto", objective_id="OBJ-FIRST")
        task = TaskStore(db).create("sample-project", "Ready work", status="ready", task_id="TASK-FIRST")
        registry.set_task_meta(task["id"], objective_id=first["id"], contract={"task_id": task["id"]})
        transitions = registry.reconcile_objective_lifecycle("sample-project")
        assert transitions[0]["to"] == "active"
        assert registry.profile("sample-project")["autopilot_state"] == "OFF"
        assert db.execute("SELECT status FROM organization_objectives WHERE id='OBJ-FIRST'").fetchone()[0] == "active"
    finally:
        db.close()


def test_objective_lifecycle_skips_tasks_with_disallowed_risk(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", policy={"allowed_risks": ["low"]})
        for suffix, weight, risk in (("HIGH", 100, "high"), ("LOW", 10, "low")):
            objective = registry.create_objective(
                "sample-project", f"{risk} risk objective", status="planned", weight=weight,
                activation_policy="auto", objective_id=f"OBJ-{suffix}",
            )
            task = TaskStore(db).create(
                "sample-project", f"{risk} risk work", status="ready", task_id=f"TASK-{suffix}",
            )
            registry.set_task_meta(
                task["id"], objective_id=objective["id"], risk=risk,
                contract={"task_id": task["id"]},
            )

        transitions = registry.reconcile_objective_lifecycle("sample-project")

        assert transitions == [{
            "objective_id": "OBJ-LOW", "from": "planned", "to": "active",
            "reason": "AUTO_POLICY_ELIGIBLE",
        }]
        statuses = dict(db.execute("SELECT id, status FROM organization_objectives").fetchall())
        assert statuses == {"OBJ-HIGH": "planned", "OBJ-LOW": "active"}
        assert registry.select_next_task("sample-project", require_contract=True)["id"] == "TASK-LOW"
    finally:
        db.close()


def test_objective_lifecycle_advances_dependencies_once_and_respects_activation_policy(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project")
        tasks = TaskStore(db)
        configurations = (
            ("FIRST", "auto", 10, ()),
            ("SECOND", "auto", 20, ("OBJ-FIRST",)),
            ("APPROVAL", "approval_required", 100, ()),
            ("MANUAL", "manual_only", 100, ()),
        )
        for suffix, policy, weight, dependencies in configurations:
            objective = registry.create_objective(
                "sample-project", suffix, status="planned", weight=weight,
                activation_policy=policy, depends_on=dependencies, objective_id=f"OBJ-{suffix}",
            )
            task = tasks.create("sample-project", suffix, status="ready", task_id=f"TASK-{suffix}")
            registry.set_task_meta(
                task["id"], objective_id=objective["id"], contract={"task_id": task["id"]},
            )

        first_activation = {
            "objective_id": "OBJ-FIRST", "from": "planned", "to": "active",
            "reason": "AUTO_POLICY_ELIGIBLE",
        }
        assert registry.reconcile_objective_lifecycle("sample-project") == [first_activation]
        assert registry.reconcile_objective_lifecycle("sample-project") == []
        assert registry.select_next_task("sample-project", require_contract=True)["id"] == "TASK-FIRST"

        first_task = tasks.get("TASK-FIRST")
        tasks.update_status(first_task["id"], "done", expected_version=first_task["version"])
        first_completion = {
            "objective_id": "OBJ-FIRST", "from": "active", "to": "done",
            "reason": "ALL_TASKS_DONE",
        }
        second_activation = {
            "objective_id": "OBJ-SECOND", "from": "planned", "to": "active",
            "reason": "AUTO_POLICY_ELIGIBLE",
        }
        assert registry.reconcile_objective_lifecycle("sample-project") == [first_completion, second_activation]
        assert registry.reconcile_objective_lifecycle("sample-project") == []
        assert registry.select_next_task("sample-project", require_contract=True)["id"] == "TASK-SECOND"

        second_task = tasks.get("TASK-SECOND")
        tasks.update_status(second_task["id"], "done", expected_version=second_task["version"])
        second_completion = {
            "objective_id": "OBJ-SECOND", "from": "active", "to": "done",
            "reason": "ALL_TASKS_DONE",
        }
        assert registry.reconcile_objective_lifecycle("sample-project") == [second_completion]
        assert registry.reconcile_objective_lifecycle("sample-project") == []
        assert dict(db.execute("SELECT id, status FROM organization_objectives").fetchall()) == {
            "OBJ-FIRST": "done", "OBJ-SECOND": "done",
            "OBJ-APPROVAL": "planned", "OBJ-MANUAL": "planned",
        }
        audit_rows = db.execute(
            "SELECT source_id, payload FROM events "
            "WHERE project_id=? AND type='organization.objective_transitioned' ORDER BY rowid",
            ("sample-project",),
        ).fetchall()
        expected_transitions = [first_activation, first_completion, second_activation, second_completion]
        assert [json.loads(row["payload"]) for row in audit_rows] == expected_transitions
        assert [row["source_id"] for row in audit_rows] == [
            transition["objective_id"] for transition in expected_transitions
        ]
    finally:
        db.close()


def test_invalid_profile_policy_and_budget_are_rejected(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        invalid_profiles = (
            {"policy": {"allowed_risks": "medium"}},
            {"budget": {"max_active_jobs": 0}},
            {"budget": {"hard_timeout_seconds": 0}},
        )
        for payload in invalid_profiles:
            try:
                registry.upsert_profile("sample-project", **payload)
            except ValueError:
                continue
            raise AssertionError(f"invalid organization profile accepted: {payload}")
    finally:
        db.close()


def test_enqueue_gate_applies_risk_and_active_job_budget(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", policy={"allowed_risks": ["low"]}, budget={"max_active_jobs": 1})
        try:
            registry.assert_enqueue_allowed("sample-project", risk="high", priority="normal")
        except ValueError as exc:
            assert "risk" in str(exc)
        else:
            raise AssertionError("disallowed risk was accepted")
        from arda.core import JobQueue
        JobQueue(db).enqueue("autonomous-coding", "active", idempotency_key="active", project_id="sample-project")
        try:
            registry.assert_enqueue_allowed("sample-project", risk="low", priority="normal")
        except ValueError as exc:
            assert "budget" in str(exc)
        else:
            raise AssertionError("active-job budget was bypassed")
    finally:
        db.close()
