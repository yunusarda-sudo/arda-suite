from __future__ import annotations

import json
import hashlib
import subprocess
from datetime import datetime, timedelta, timezone

from arda.autonomous_director import (
    AutonomousDirector, CiFailureProposalGenerator, CoreProposalDispatcher,
    GitHubProjectObserver, SignalObservation,
)
from arda.awareness import ProjectRegistry
from arda.core import JobQueue, bootstrap_runtime_database
from arda.execution_bridge import ExecutionBridge
from arda.organization import OrganizationRegistry


def create_mission(db, *, scopes=("github.read", "coding.propose", "coding.execute")):
    ProjectRegistry(db).create("opvia", "OPVIA")
    registry = OrganizationRegistry(db)
    registry.upsert_profile("opvia", budget={"hard_timeout_seconds": 900})
    return registry.create_mission(
        "opvia", "Stabilize OPVIA", "Fix verified delivery blockers",
        acceptance_metrics=("failing CI resolved",),
        allowed_repositories=("acme/opvia",), allowed_domains=("github.com",),
        delegated_scopes=scopes, allowed_risks=("low",),
        budget={"max_active_jobs": 1, "max_tasks": 3, "max_task_cost": 2,
                "hard_timeout_seconds": 900, "max_turns": 20},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        mission_id="MISSION-OPVIA",
    )


def test_github_observer_emits_bounded_normalized_issue_pr_and_failure_signals():
    class Adapter:
        def list_issues(self, repo, *, state):
            return [{"number": 2, "title": "fix CI", "state": state, "updated_at": "2026-10-17T10:00:00Z"}]

        def list_pull_requests(self, repo, *, state):
            return [{"number": 3, "title": "candidate", "state": state, "updated_at": "2026-10-17T10:01:00Z"}]

        def list_workflow_runs(self, repo, *, per_page):
            return [{"id": 4, "status": "completed", "conclusion": "failure", "head_sha": "a" * 40,
                     "run_attempt": 2, "updated_at": "2026-10-17T10:02:00Z"},
                    {"id": 5, "status": "completed", "conclusion": "success", "updated_at": "2026-10-17T10:02:00Z"}]

    db = bootstrap_runtime_database(":memory:")
    try:
        mission = create_mission(db)
        signals = GitHubProjectObserver(Adapter()).observe(mission)
        assert [signal.signal_type for signal in signals] == ["issue.open", "pull_request.open", "ci.failure"]
        assert signals[-1].source_key == "workflow:4:attempt:2"
        assert "body" not in signals[0].payload
    finally:
        db.close()


def test_ci_failure_proposer_is_deterministic_and_repository_scoped():
    mission = {"allowed_repositories": ["acme/opvia"], "allowed_risks": ["low"]}
    generator = CiFailureProposalGenerator()
    failure = {"signal_type": "ci.failure", "payload": {
        "repo": "acme/opvia", "conclusion": "failure", "run_id": 41, "workflow": "Unit tests",
    }}
    proposal = generator.propose(mission, failure)
    assert proposal["repo"] == "acme/opvia"
    assert proposal["risk"] == "low"
    assert proposal["allowed_paths"] == ["**"]
    assert "41" in proposal["objective"]
    failure["payload"]["repo"] = "attacker/other"
    assert generator.propose(mission, failure) is None
    assert generator.propose(mission, {"signal_type": "issue.open", "payload": {}}) is None


def test_director_discovers_and_dispatches_once_through_core_without_seeded_job(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    mission = create_mission(db)
    observation = SignalObservation(
        provider="github", source_key="workflow:123:attempt:1", signal_type="ci.failure",
        source_updated_at=datetime.now(timezone.utc).isoformat(),
        payload={"repo": "acme/opvia", "run_id": 123, "conclusion": "failure", "head_sha": baseline},
    )

    class Observer:
        def observe(self, _mission):
            return [observation, observation]

    class Proposer:
        calls = 0

        def propose(self, _mission, _signal):
            self.calls += 1
            return {"repo": "acme/opvia", "objective": "Repair the failing CI check",
                    "acceptance": ["Configured CI checks pass"], "allowed_paths": ["src/**"],
                    "risk": "low", "estimated_cost": 1, "priority": "high",
                    "rationale": "Fresh failed workflow run on the delegated repository.",
                    "value_score": 90, "feasibility_score": 85}

    proposer = Proposer()
    dispatcher = CoreProposalDispatcher(
        ExecutionBridge(db_path, worker_id="director-test"),
        lambda _repo: {"path": str(repo), "baseline_ref": "refs/heads/main", "baseline_sha": baseline},
        execution_enabled=True,
    )
    dispatch_calls = []

    def crash_after_queue_commit(mission_data, proposal_data):
        result = dispatcher(mission_data, proposal_data)
        dispatch_calls.append(result)
        if len(dispatch_calls) == 1:
            raise RuntimeError("simulated crash after idempotent enqueue")
        return result

    director = AutonomousDirector(db, Observer(), proposer, dispatcher=crash_after_queue_commit)

    first = director.run_once(mission["id"])
    second = director.run_once(mission["id"])

    proposal = db.execute("SELECT * FROM director_proposals").fetchone()
    assert first["inserted"] == 1
    assert first["proposed"] == 1
    assert first["dispatched"] == 0
    assert second["inserted"] == 0
    assert second["proposed"] == 0
    assert second["dispatched"] == 1
    assert proposer.calls == 1
    assert len(dispatch_calls) == 2
    assert dispatch_calls[0] == dispatch_calls[1]
    assert db.scalar("SELECT COUNT(*) FROM tasks") == 1
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 1
    assert proposal["status"] == "queued"
    assert proposal["task_id"].startswith("director-")
    assert proposal["job_id"]
    queue = JobQueue(db)
    leased = queue.claim("verified-test-worker")
    assert leased.id == proposal["job_id"]
    queue.start(leased.id, leased.lease_token)
    content = b"verified worker artifact"
    queue.record_artifact(leased.id, "patch", "candidate.patch", hashlib.sha256(content).hexdigest(),
                          content=content, lease_token=leased.lease_token)
    queue.complete(leased.id, leased.lease_token, result={"verification": "passed"})
    third = director.run_once(mission["id"])
    assert third["reconciled"] == 1
    assert db.execute("SELECT status FROM director_proposals").fetchone()[0] == "verified"
    db.close()


def test_dispatch_respects_mission_active_job_budget_and_resumes_waiting_proposal(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = bootstrap_runtime_database(db_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    mission = create_mission(db)
    now = datetime.now(timezone.utc).isoformat()
    observations = [
        SignalObservation("github", f"workflow:{run_id}:attempt:1", "ci.failure", now,
                          {"repo": "acme/opvia", "run_id": run_id, "conclusion": "failure"})
        for run_id in (101, 102)
    ]

    class Observer:
        def observe(self, _mission):
            return observations

    class Proposer:
        def propose(self, _mission, signal):
            run_id = signal["payload"]["run_id"]
            return {"repo": "acme/opvia", "objective": f"Repair CI run {run_id}",
                    "acceptance": ["Configured checks pass"], "allowed_paths": ["src/**"],
                    "risk": "low", "estimated_cost": 1, "priority": "high",
                    "rationale": f"Fresh failing workflow run {run_id}.",
                    "value_score": 90, "feasibility_score": 85}

    dispatcher = CoreProposalDispatcher(
        ExecutionBridge(db_path, worker_id="director-budget-test"),
        lambda _repo: {"path": str(repo), "baseline_ref": "refs/heads/main", "baseline_sha": baseline},
        execution_enabled=True,
    )
    director = AutonomousDirector(db, Observer(), Proposer(), dispatcher=dispatcher)

    first = director.run_once(mission["id"])
    assert first["dispatched"] == 1
    assert first["awaiting_dispatch"] == 1
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 1
    assert sorted(row[0] for row in db.execute("SELECT status FROM director_proposals")) == ["accepted", "queued"]

    waiting = director.run_once(mission["id"])
    assert waiting["dispatched"] == 0
    assert waiting["awaiting_dispatch"] == 1
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 1

    job = JobQueue(db).claim("budget-test-worker")
    JobQueue(db).start(job.id, job.lease_token)
    evidence = b"verified candidate"
    JobQueue(db).record_artifact(job.id, "candidate", "candidate.txt", hashlib.sha256(evidence).hexdigest(),
                                 content=evidence, lease_token=job.lease_token)
    JobQueue(db).complete(job.id, job.lease_token, result={"verification": "passed"})

    resumed = director.run_once(mission["id"])
    assert resumed["reconciled"] == 1
    assert resumed["dispatched"] == 1
    assert db.scalar("SELECT COUNT(*) FROM jobs") == 2
    assert db.scalar("SELECT COUNT(*) FROM director_proposals WHERE status='queued'") == 1
    assert db.scalar("SELECT COUNT(*) FROM director_proposals WHERE status='verified'") == 1
    db.close()


def test_propose_only_mission_requires_approval_and_low_scores_are_rejected(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        mission = create_mission(db, scopes=("github.read", "coding.propose"))
        observation = SignalObservation(
            provider="github", source_key="issue:5", signal_type="issue.open",
            source_updated_at=datetime.now(timezone.utc).isoformat(),
            payload={"repo": "acme/opvia", "number": 5, "title": "Fix flaky CI"},
        )

        class Observer:
            def observe(self, _mission):
                return [observation]

        class Proposer:
            def __init__(self, score):
                self.score = score

            def propose(self, _mission, _signal):
                return {"repo": "acme/opvia", "objective": "Investigate flaky CI",
                        "acceptance": ["Flake is reproducible and fixed"], "allowed_paths": ["tests/**"],
                        "risk": "low", "estimated_cost": 1, "rationale": "Issue is in mission scope.",
                        "value_score": self.score, "feasibility_score": 80}

        proposed = AutonomousDirector(db, Observer(), Proposer(80)).run_once(mission["id"])
        row = db.execute("SELECT * FROM director_proposals").fetchone()
        assert proposed["proposed"] == 1
        assert row["status"] == "proposed"
        assert json.loads(row["decision_json"])["reason"] == "EXECUTION_APPROVAL_REQUIRED"
        try:
            OrganizationRegistry(db).approve_proposal(row["id"], expected_version=row["version"], actor="operator")
        except ValueError as exc:
            assert "does not delegate" in str(exc)
        else:
            raise AssertionError("human approval must not expand mission execution scope")

        second_signal, _ = OrganizationRegistry(db).ingest_signal(
            mission["id"], provider="github", source_key="issue:6", signal_type="issue.open",
            source_updated_at=datetime.now(timezone.utc).isoformat(),
            payload={"repo": "acme/opvia", "number": 6, "title": "Low value"},
        )
        registry = OrganizationRegistry(db)
        low, created = registry.record_proposal(
            mission["id"], second_signal["id"], draft={"repo": "acme/opvia", "objective": "Low value",
            "acceptance": ["test"], "allowed_paths": ["tests/**"], "risk": "low", "estimated_cost": 1},
            rationale="low score", decision="rejected", reason="PROPOSAL_SCORE_BELOW_THRESHOLD",
        )
        assert created is True
        assert low["status"] == "rejected"
    finally:
        db.close()


def test_director_stops_without_observing_when_mission_is_paused(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        registry = OrganizationRegistry(db)
        mission = create_mission(db)
        registry.set_mission_state(mission["id"], "paused", expected_version=mission["version"], reason="operator stop")

        class Observer:
            def observe(self, _mission):
                raise AssertionError("paused missions must not poll")

        class Proposer:
            def propose(self, _mission, _signal):
                raise AssertionError("paused missions must not propose")

        result = AutonomousDirector(db, Observer(), Proposer()).run_once(mission["id"])
        assert result["state"] == "IDLE"
        assert result["reason"] == "MISSION_PAUSED"
    finally:
        db.close()
