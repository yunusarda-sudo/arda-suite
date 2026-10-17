"""Mission-scoped signal-to-proposal Director; Core owns authority and state."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from arda.core import Database, EventLog, _iso, _utc_now
from arda.organization import OrganizationRegistry


@dataclass(frozen=True)
class SignalObservation:
    provider: str
    source_key: str
    signal_type: str
    source_updated_at: str
    payload: dict[str, Any]


class SignalObserver(Protocol):
    def observe(self, mission: sqlite3.Row) -> list[SignalObservation]: ...


class ProposalGenerator(Protocol):
    def propose(self, mission: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any] | None: ...


class CiFailureProposalGenerator:
    """Deterministic, bounded proposer for fresh failing CI; no model or network writes."""

    def propose(self, mission: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any] | None:
        if signal.get("signal_type") != "ci.failure":
            return None
        payload = signal.get("payload")
        if not isinstance(payload, dict) or payload.get("conclusion") not in {
                "failure", "timed_out", "action_required"}:
            return None
        repo = payload.get("repo")
        if repo not in mission.get("allowed_repositories", ()):
            return None
        workflow = str(payload.get("workflow") or "CI")[:120]
        run_id = payload.get("run_id")
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            return None
        risk = "low" if "low" in mission.get("allowed_risks", ()) else next(
            iter(mission.get("allowed_risks", ())), None)
        if risk is None:
            return None
        objective = f"Investigate and resolve {workflow} failure from run {run_id} in {repo}"
        return {
            "repo": repo,
            "objective": objective[:500],
            "acceptance": [
                f"A fresh {workflow} run for the resulting candidate completes successfully",
                "The candidate remains within the explicitly delegated repository",
            ],
            "allowed_paths": ["**"],
            "forbidden_paths": [],
            "risk": risk,
            "estimated_cost": 1,
            "priority": "high",
            "task_type": "maintenance",
            "affected_subsystem": workflow,
            "rationale": f"Fresh failing workflow run {run_id} is inside the mission repository scope.",
            "value_score": 75,
            "feasibility_score": 70,
        }


Dispatcher = Callable[[dict[str, Any], dict[str, Any]], dict[str, str] | None]


class CoreProposalDispatcher:
    """Convert a validated proposal into Core work; disabled by default until WP-1 gate passes."""

    def __init__(self, execution_bridge: Any, repo_resolver: Callable[[str], dict[str, str]],
                 *, execution_enabled: bool = False):
        self.execution_bridge = execution_bridge
        self.repo_resolver = repo_resolver
        self.execution_enabled = execution_enabled

    def __call__(self, mission: dict[str, Any], proposal: dict[str, Any]) -> dict[str, str] | None:
        if not self.execution_enabled or "coding.execute" not in mission["delegated_scopes"]:
            return None
        from arda.autonomous_work_loop import WorkTask

        draft = json.loads(proposal["contract_json"])
        resolved = self.repo_resolver(draft["repo"])
        hard_timeout = int(mission["budget"].get("hard_timeout_seconds", 900))
        if hard_timeout < 2:
            raise ValueError("mission hard_timeout_seconds must be at least 2")
        task_id = "director-" + proposal["id"].removeprefix("proposal_")[:24]
        task = WorkTask(
            task_id=task_id,
            repo=resolved["path"], baseline_ref=resolved["baseline_ref"],
            baseline_sha=resolved["baseline_sha"], objective=draft["objective"],
            acceptance=tuple(draft["acceptance"]), allowed_paths=tuple(draft["allowed_paths"]),
            forbidden_paths=tuple(draft.get("forbidden_paths", ())),
            provider=resolved.get("provider", "openai-codex"),
            model=resolved.get("model", "gpt-5.6-luna"),
            reasoning=resolved.get("reasoning", "medium"),
            max_turns=int(mission["budget"].get("max_turns", 20)),
            hard_timeout_seconds=hard_timeout,
            soft_budget_seconds=max(1, int(hard_timeout * 0.8)),
            risk=draft["risk"], task_type=draft.get("task_type", "maintenance"),
            affected_subsystem=draft.get("affected_subsystem", ""),
        )
        result = self.execution_bridge.enqueue(
            task, project_id=mission["project_id"], title=draft["objective"],
            priority=draft.get("priority", "normal"), timeout_seconds=hard_timeout,
            human_approved=False,
        )
        return {"task_id": task_id, "job_id": result["job"]["id"]}


class GitHubProjectObserver:
    """Read-only bounded observer for open issues, PRs, and completed CI failures."""

    def __init__(self, adapter: Any, *, per_endpoint_limit: int = 25):
        if not 1 <= per_endpoint_limit <= 100:
            raise ValueError("per_endpoint_limit must be between 1 and 100")
        self.adapter = adapter
        self.per_endpoint_limit = per_endpoint_limit

    def observe(self, mission: sqlite3.Row) -> list[SignalObservation]:
        if "github.read" not in json.loads(mission["delegated_scopes_json"]):
            return []
        results: list[SignalObservation] = []
        repositories = json.loads(mission["allowed_repositories_json"])
        for repo in repositories:
            for issue in self.adapter.list_issues(repo, state="open")[:self.per_endpoint_limit]:
                if issue.get("updated_at") and issue.get("number") is not None:
                    results.append(SignalObservation(
                        "github", f"issue:{issue['number']}", "issue.open", issue["updated_at"],
                        {"repo": repo, "number": issue["number"], "title": str(issue.get("title", ""))[:500],
                         "state": issue.get("state", "open")},
                    ))
            for pull in self.adapter.list_pull_requests(repo, state="open")[:self.per_endpoint_limit]:
                if pull.get("updated_at") and pull.get("number") is not None:
                    results.append(SignalObservation(
                        "github", f"pull:{pull['number']}", "pull_request.open", pull["updated_at"],
                        {"repo": repo, "number": pull["number"], "title": str(pull.get("title", ""))[:500],
                         "state": pull.get("state", "open")},
                    ))
            list_runs = getattr(self.adapter, "list_workflow_runs", None)
            if callable(list_runs):
                runs = list_runs(repo, per_page=self.per_endpoint_limit)
                for run in runs:
                    if (run.get("status") == "completed" and run.get("conclusion") in
                            {"failure", "timed_out", "action_required"} and run.get("id") is not None
                            and run.get("updated_at")):
                        results.append(SignalObservation(
                            "github", f"workflow:{run['id']}:attempt:{run.get('run_attempt', 1)}",
                            "ci.failure", run["updated_at"],
                            {"repo": repo, "run_id": run["id"], "run_attempt": run.get("run_attempt", 1),
                             "workflow": str(run.get("name", ""))[:200],
                             "head_branch": str(run.get("head_branch", ""))[:200],
                             "head_sha": str(run.get("head_sha", ""))[:64],
                             "conclusion": run["conclusion"], "url": run.get("html_url")},
                        ))
        return results


class AutonomousDirector:
    """Poll signals, persist bounded decisions, and idempotently hand off accepted work."""

    def __init__(self, db: Database, observer: SignalObserver, proposer: ProposalGenerator,
                 *, dispatcher: Dispatcher | None = None, min_value_score: int = 60,
                 min_feasibility_score: int = 60, freshness_seconds: int = 3600,
                 clock=_utc_now):
        if not 0 <= min_value_score <= 100 or not 0 <= min_feasibility_score <= 100:
            raise ValueError("proposal thresholds must be between 0 and 100")
        if freshness_seconds <= 0:
            raise ValueError("freshness_seconds must be positive")
        self.db = db
        self.registry = OrganizationRegistry(db, clock=clock)
        self.observer = observer
        self.proposer = proposer
        self.dispatcher = dispatcher
        self.min_value_score = min_value_score
        self.min_feasibility_score = min_feasibility_score
        self.freshness_seconds = freshness_seconds
        self.clock = clock
        self.events = EventLog(db, clock)

    def run_once(self, mission_id: str) -> dict[str, Any]:
        mission = self.registry.mission(mission_id)
        reconciled = self.registry.reconcile_proposal_outcomes(mission_id)
        if mission["status"] != "active":
            return {"state": "IDLE", "reason": f"MISSION_{mission['status'].upper()}",
                    "observed": 0, "proposed": 0, "dispatched": 0, "reconciled": reconciled}
        observed = inserted = proposed = dispatched = 0
        try:
            signals = self.observer.observe(mission)
        except Exception as exc:
            self.events.append("director.observation_failed", project_id=mission["project_id"],
                               source="director", source_id=mission_id,
                               payload={"error_class": type(exc).__name__})
            return {"state": "BLOCKED", "reason": "SIGNAL_OBSERVER_FAILED",
                    "observed": 0, "proposed": 0, "dispatched": 0}
        for signal in signals:
            observed += 1
            try:
                _, created = self.registry.ingest_signal(
                    mission_id, provider=signal.provider, source_key=signal.source_key,
                    signal_type=signal.signal_type, source_updated_at=signal.source_updated_at,
                    payload=signal.payload, freshness_seconds=self.freshness_seconds,
                )
                inserted += int(created)
            except ValueError as exc:
                self.events.append("director.signal_rejected", project_id=mission["project_id"],
                                   source=signal.provider, source_id=signal.source_key,
                                   payload={"reason": str(exc)[:200]})
        mission_data = self._mission_payload(self.registry.mission(mission_id))
        for signal_row in self.registry.pending_signals(mission_id):
            signal_data = dict(signal_row)
            signal_data["payload"] = json.loads(signal_data.pop("payload_json"))
            try:
                draft = self.proposer.propose(mission_data, signal_data)
                if draft is None:
                    self.registry.record_proposal(
                        mission_id, signal_row["id"], draft=None, rationale="No justified task found.",
                        decision="rejected", reason="NO_ACTION_JUSTIFIED",
                    )
                    continue
                rationale = str(draft.get("rationale", ""))
                try:
                    value = int(draft.get("value_score", 0))
                    feasibility = int(draft.get("feasibility_score", 0))
                except (TypeError, ValueError):
                    value = feasibility = 0
                contract = {key: value for key, value in draft.items()
                            if key not in {"rationale", "value_score", "feasibility_score"}}
                if value < self.min_value_score or feasibility < self.min_feasibility_score:
                    decision, reason = "rejected", "PROPOSAL_SCORE_BELOW_THRESHOLD"
                else:
                    decision, reason = "accepted", "CORE_POLICY_ACCEPTED"
                row, created = self.registry.record_proposal(
                    mission_id, signal_row["id"], draft=contract, rationale=rationale,
                    decision=decision, reason=reason,
                )
                proposed += int(created)
            except Exception as exc:
                self.events.append("director.proposal_generation_failed", project_id=mission["project_id"],
                                   source="director", source_id=signal_row["id"],
                                   payload={"error_class": type(exc).__name__})
                # Persist a terminal rejection so restarts do not repeatedly pay for a failing proposal call.
                self.registry.record_proposal(
                    mission_id, signal_row["id"], draft=None, rationale="Proposal generation failed.",
                    decision="rejected", reason="PROPOSAL_GENERATION_FAILED",
                )
        ready_proposals = self.registry.ready_proposals(mission_id)
        blocked_dispatches = 0
        dispatch_errors = 0
        capacity_exhausted = False
        mission_blocked = False
        execution_scope_blocked = False
        if self.dispatcher is not None:
            for proposal in ready_proposals:
                reservation = self.registry.reserve_proposal_dispatch(proposal["id"])
                if reservation == "capacity_exhausted":
                    capacity_exhausted = True
                    break
                if reservation == "mission_inactive":
                    mission_blocked = True
                    break
                if reservation == "scope_not_delegated":
                    execution_scope_blocked = True
                    break
                if reservation == "signal_stale":
                    continue
                if reservation != "reserved":
                    continue
                try:
                    handoff = self.dispatcher(mission_data, dict(proposal))
                    if handoff is None:
                        self.registry.release_proposal_dispatch(proposal["id"])
                        blocked_dispatches += 1
                        continue
                    self.registry.mark_proposal_queued(
                        proposal["id"], task_id=handoff["task_id"], job_id=handoff["job_id"])
                    dispatched += 1
                except Exception as exc:
                    dispatch_errors += 1
                    self.events.append("director.dispatch_failed", project_id=mission["project_id"],
                                       source="director", source_id=proposal["id"],
                                       payload={"error_class": type(exc).__name__})
        awaiting_dispatch = self.db.execute(
            "SELECT COUNT(*) FROM director_proposals WHERE mission_id=? AND status='accepted'",
            (mission_id,),
        ).fetchone()[0]
        gate_closed = awaiting_dispatch > 0 and (
            self.dispatcher is None or blocked_dispatches > 0 or dispatch_errors > 0
            or mission_blocked or execution_scope_blocked
        )
        reason = ("DIRECTOR_DISPATCH_FAILED" if dispatch_errors
                  else "MISSION_NOT_ACTIVE" if mission_blocked
                  else "MISSION_EXECUTION_SCOPE_NOT_DELEGATED" if execution_scope_blocked
                  else "WP1_EXECUTION_GATE_CLOSED" if gate_closed and "coding.execute" in mission_data["delegated_scopes"]
                  else "MISSION_EXECUTION_SCOPE_NOT_DELEGATED" if gate_closed
                  else "MISSION_ACTIVE_JOB_BUDGET_EXHAUSTED" if awaiting_dispatch and capacity_exhausted
                  else "SIGNALS_PROCESSED" if observed else "IDLE_NO_SIGNALS")
        return {"state": "BLOCKED" if gate_closed else "ACTIVE" if observed or inserted or proposed or dispatched else "IDLE",
                "reason": reason,
                "observed": observed, "inserted": inserted, "proposed": proposed,
                "dispatched": dispatched, "awaiting_dispatch": awaiting_dispatch,
                "reconciled": reconciled}

    @staticmethod
    def _mission_payload(mission: sqlite3.Row) -> dict[str, Any]:
        result = dict(mission)
        for field, source in (("acceptance_metrics", "acceptance_metrics_json"),
                              ("allowed_repositories", "allowed_repositories_json"),
                              ("allowed_domains", "allowed_domains_json"),
                              ("delegated_scopes", "delegated_scopes_json"),
                              ("allowed_risks", "allowed_risks_json"),
                              ("budget", "budget_json")):
            result[field] = json.loads(result.pop(source))
        return result
