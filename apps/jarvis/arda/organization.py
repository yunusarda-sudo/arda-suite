"""Durable organization metadata and deterministic backlog selection."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from arda.core import Database, EventLog, _iso, _utc_now


OBJECTIVE_STATUSES = frozenset({"planned", "active", "paused", "done", "blocked"})
ACTIVATION_POLICIES = frozenset({"auto", "approval_required", "manual_only"})
RISK_PENALTY = {"low": 0, "medium": 5, "high": 15}
PRIORITY_SCORE = {"critical": 40, "high": 30, "normal": 20, "low": 10}
DEPENDENCY_STATUSES = frozenset({"ready", "blocked", "pending"})
MISSION_STATUSES = frozenset({"active", "paused", "cancelled", "completed", "expired"})
MISSION_SCOPES = frozenset({"signals.read", "github.read", "coding.propose", "coding.execute", "browser.read"})
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _validate_objective_status(status: str) -> None:
    if status not in OBJECTIVE_STATUSES:
        raise ValueError(f"invalid objective status: {status}")


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    if policy is None:
        return {}
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    normalized = dict(policy)
    risks = normalized.get("allowed_risks")
    if risks is not None:
        if not isinstance(risks, list) or any(value not in RISK_PENALTY for value in risks):
            raise ValueError("allowed_risks must be a list of low, medium or high")
        normalized["allowed_risks"] = list(dict.fromkeys(risks))
    return normalized


def _normalize_budget(budget: dict[str, Any] | None) -> dict[str, Any]:
    if budget is None:
        return {}
    if not isinstance(budget, dict):
        raise ValueError("budget must be an object")
    normalized = dict(budget)
    for key, minimum in (("max_active_jobs", 1), ("hard_timeout_seconds", 1), ("max_repair", 0)):
        if key in normalized and (not isinstance(normalized[key], int) or isinstance(normalized[key], bool)
                                  or normalized[key] < minimum):
            raise ValueError(f"{key} must be an integer >= {minimum}")
    return normalized


class OrganizationRegistry:
    def __init__(self, db: Database, *, clock=_utc_now):
        self.db = db
        self.clock = clock
        self.events = EventLog(db, clock)

    def upsert_profile(
        self,
        project_id: str,
        *,
        mission: str = "",
        accepted_baseline: str | None = None,
        policy: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        self._require_project(project_id)
        now = _iso(self.clock())
        policy_json = json.dumps(_normalize_policy(policy), sort_keys=True)
        budget_json = json.dumps(_normalize_budget(budget), sort_keys=True)
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO organization_profiles(project_id, mission, accepted_baseline, policy_json, budget_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET mission=excluded.mission,
                     accepted_baseline=excluded.accepted_baseline, policy_json=excluded.policy_json,
                     budget_json=excluded.budget_json, updated_at=excluded.updated_at,
                     version=organization_profiles.version+1""",
                (project_id, mission, accepted_baseline, policy_json, budget_json, now, now),
            )
            self.events.append("organization.profile_updated", project_id=project_id, source="organization",
                               source_id=project_id, payload={"accepted_baseline": accepted_baseline})
        return self.profile(project_id)

    def profile(self, project_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM organization_profiles WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return row

    def create_mission(
        self, project_id: str, title: str, objective: str, *,
        acceptance_metrics: tuple[str, ...], allowed_repositories: tuple[str, ...],
        allowed_domains: tuple[str, ...] = (), delegated_scopes: tuple[str, ...] = (),
        allowed_risks: tuple[str, ...] = ("low",), budget: dict[str, Any] | None = None,
        expires_at: str, mission_id: str | None = None,
    ) -> sqlite3.Row:
        """Create an explicit, expiring authority envelope for Director work."""
        self._require_project(project_id)
        if (not isinstance(title, str) or not isinstance(objective, str)
                or not title.strip() or not objective.strip()
                or len(title) > 240 or len(objective) > 4000):
            raise ValueError("mission title and objective are required")
        if (not isinstance(acceptance_metrics, (tuple, list)) or not acceptance_metrics
                or len(acceptance_metrics) > 20
                or any(not isinstance(value, str) or not value.strip() or len(value) > 500
                       for value in acceptance_metrics)):
            raise ValueError("mission acceptance_metrics must be non-empty")
        if (not isinstance(allowed_repositories, (tuple, list)) or not allowed_repositories
                or len(allowed_repositories) > 20
                or any(not isinstance(value, str) or not _REPOSITORY.fullmatch(value)
                       for value in allowed_repositories)):
            raise ValueError("allowed_repositories must use owner/name form")
        if (not isinstance(allowed_domains, (tuple, list)) or len(allowed_domains) > 50
                or any(not isinstance(value, str) for value in allowed_domains)):
            raise ValueError("allowed_domains must be a list of exact hostnames")
        domains = tuple(value.lower().rstrip(".") for value in allowed_domains)
        if any(not _DOMAIN.fullmatch(value) for value in domains):
            raise ValueError("allowed_domains must contain exact hostnames")
        if (not isinstance(delegated_scopes, (tuple, list)) or len(delegated_scopes) > len(MISSION_SCOPES)
                or any(not isinstance(scope, str) or scope not in MISSION_SCOPES
                       for scope in delegated_scopes)):
            raise ValueError("delegated scope is not recognized")
        if (not isinstance(allowed_risks, (tuple, list)) or not allowed_risks
                or any(not isinstance(value, str) or value not in RISK_PENALTY for value in allowed_risks)):
            raise ValueError("allowed_risks must contain low, medium, or high")
        if budget is not None and not isinstance(budget, dict):
            raise ValueError("budget must be an object")
        budget_value = dict(budget or {})
        budget_limits = {"max_active_jobs": 1, "max_tasks": 1, "max_total_cost": 1,
                         "max_task_cost": 1, "hard_timeout_seconds": 1, "max_turns": 1}
        for key, minimum in budget_limits.items():
            if key in budget_value and (not isinstance(budget_value[key], int)
                                        or isinstance(budget_value[key], bool)
                                        or budget_value[key] < minimum):
                raise ValueError(f"{key} must be an integer >= {minimum}")
        if not isinstance(expires_at, str):
            raise ValueError("expires_at must be an ISO-8601 timestamp")
        if mission_id is not None and (not isinstance(mission_id, str) or not mission_id.strip()
                                       or len(mission_id) > 160):
            raise ValueError("mission id must be a non-empty string up to 160 characters")
        try:
            expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
        if expiration.tzinfo is None or expiration <= self.clock():
            raise ValueError("expires_at must be a future timezone-aware timestamp")
        now = _iso(self.clock())
        mission_id = mission_id or f"mission_{uuid.uuid4().hex}"
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO organization_missions(
                    id, project_id, title, objective, acceptance_metrics_json,
                    allowed_repositories_json, allowed_domains_json, delegated_scopes_json,
                    allowed_risks_json, budget_json, status, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (mission_id, project_id, title.strip(), objective.strip(),
                 json.dumps(list(acceptance_metrics), sort_keys=True),
                 json.dumps(sorted(set(allowed_repositories)), sort_keys=True),
                 json.dumps(sorted(set(domains)), sort_keys=True),
                 json.dumps(sorted(set(delegated_scopes)), sort_keys=True),
                 json.dumps(sorted(set(allowed_risks)), sort_keys=True),
                 json.dumps(budget_value, sort_keys=True), _iso(expiration), now, now),
            )
            self.events.append("organization.mission_created", project_id=project_id,
                               source="organization", source_id=mission_id,
                               payload={"title": title.strip(), "expires_at": _iso(expiration)})
        return self.mission(mission_id)

    def mission(self, mission_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM organization_missions WHERE id=?", (mission_id,)).fetchone()
        if row is None:
            raise KeyError(mission_id)
        if row["status"] in {"active", "paused"} and datetime.fromisoformat(row["expires_at"]) <= self.clock():
            with self.db.transaction():
                changed = self.db.execute(
                    "UPDATE organization_missions SET status='expired', updated_at=?, version=version+1 "
                    "WHERE id=? AND status IN ('active','paused') AND version=?",
                    (_iso(self.clock()), mission_id, row["version"]),
                ).rowcount
                if changed:
                    self.events.append("organization.mission_expired", project_id=row["project_id"],
                                       source="organization", source_id=mission_id)
            row = self.db.execute("SELECT * FROM organization_missions WHERE id=?", (mission_id,)).fetchone()
        return row

    def set_mission_state(self, mission_id: str, status: str, *, expected_version: int,
                          reason: str = "") -> sqlite3.Row:
        if (not isinstance(status, str) or status not in MISSION_STATUSES
                or not isinstance(expected_version, int) or isinstance(expected_version, bool)
                or not isinstance(reason, str)):
            raise ValueError("invalid mission state")
        current = self.mission(mission_id)
        allowed = {"active": {"paused", "cancelled", "completed", "expired"},
                   "paused": {"active", "cancelled", "completed", "expired"}}
        if status not in allowed.get(current["status"], set()):
            raise ValueError(f"mission cannot transition from {current['status']} to {status}")
        if status == "paused" and not reason.strip():
            raise ValueError("pause reason is required")
        with self.db.transaction():
            changed = self.db.execute(
                "UPDATE organization_missions SET status=?, pause_reason=?, updated_at=?, version=version+1 "
                "WHERE id=? AND version=? AND status=?",
                (status, reason.strip() if status == "paused" else "", _iso(self.clock()),
                 mission_id, expected_version, current["status"]),
            ).rowcount
            if changed != 1:
                raise ValueError("mission version changed; reload before retrying")
            self.events.append("organization.mission_state_changed", project_id=current["project_id"],
                               source="organization", source_id=mission_id,
                               payload={"from": current["status"], "to": status, "reason": reason.strip()})
        return self.mission(mission_id)

    def ingest_signal(self, mission_id: str, *, provider: str, source_key: str, signal_type: str,
                      source_updated_at: str, payload: dict[str, Any], freshness_seconds: int = 3600) -> tuple[sqlite3.Row, bool]:
        """Persist a normalized observation once; repeated polling is harmless."""
        mission = self.mission(mission_id)
        if mission["status"] != "active":
            raise ValueError("mission is not active")
        if (not isinstance(freshness_seconds, int) or isinstance(freshness_seconds, bool)
                or not 1 <= freshness_seconds <= 7 * 24 * 3600
                or not isinstance(provider, str) or not provider.strip()
                or not isinstance(source_key, str) or not source_key.strip() or len(source_key) > 300
                or not isinstance(signal_type, str) or not signal_type.strip() or len(signal_type) > 120):
            raise ValueError("signal identity and freshness are required")
        if not isinstance(source_updated_at, str):
            raise ValueError("source_updated_at must be ISO-8601")
        try:
            source_time = datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_updated_at must be ISO-8601") from exc
        if source_time.tzinfo is None or source_time.utcoffset() is None or not isinstance(payload, dict):
            raise ValueError("signal timestamp must include timezone and payload must be an object")
        if provider not in {"github", "project"}:
            raise ValueError("signal provider is not supported")
        delegated = set(json.loads(mission["delegated_scopes_json"]))
        required_scope = "github.read" if provider == "github" else "signals.read"
        if required_scope not in delegated:
            raise ValueError("mission does not delegate this signal source")
        repo = payload.get("repo")
        allowed_repositories = set(json.loads(mission["allowed_repositories_json"]))
        if repo is not None and repo not in allowed_repositories:
            raise ValueError("signal repository is outside mission scope")
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("signal payload must contain JSON values") from exc
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("normalized signal payload exceeds 16 KiB")
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        signal_id = "signal_" + hashlib.sha256(
            f"{mission_id}\0{provider}\0{source_key}\0{payload_hash}".encode()).hexdigest()
        observed = self.clock()
        if source_time > observed + timedelta(minutes=2):
            raise ValueError("source_updated_at is too far in the future")
        now, fresh_until = _iso(observed), _iso(source_time + timedelta(seconds=freshness_seconds))
        initial_status = "pending" if source_time + timedelta(seconds=freshness_seconds) > observed else "ignored"
        with self.db.transaction():
            inserted = self.db.execute(
                """INSERT OR IGNORE INTO director_signals(
                    id, mission_id, project_id, provider, source_key, signal_type,
                    source_updated_at, observed_at, fresh_until, payload_sha256,
                    payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (signal_id, mission_id, mission["project_id"], provider, source_key, signal_type,
                 _iso(source_time), now, fresh_until, payload_hash, encoded, initial_status, now),
            ).rowcount == 1
            if inserted:
                self.events.append("organization.signal_observed", project_id=mission["project_id"],
                                   source=provider, source_id=signal_id,
                                   payload={"mission_id": mission_id, "signal_type": signal_type,
                                            "source_key": source_key, "payload_sha256": payload_hash})
        row = self.db.execute("SELECT * FROM director_signals WHERE id=?", (signal_id,)).fetchone()
        return row, inserted

    def pending_signals(self, mission_id: str, *, limit: int = 100) -> list[sqlite3.Row]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("signal limit must be between 1 and 1000")
        mission = self.mission(mission_id)
        if mission["status"] != "active":
            return []
        now = _iso(self.clock())
        with self.db.transaction():
            stale = self.db.execute(
                "UPDATE director_signals SET status='ignored' WHERE mission_id=? AND status='pending' AND fresh_until<=?",
                (mission_id, now),
            ).rowcount
            if stale:
                self.events.append("organization.stale_signals_ignored", project_id=mission["project_id"],
                                   source="director", source_id=mission_id, payload={"count": stale})
        return self.db.execute(
            """SELECT * FROM director_signals
               WHERE mission_id=? AND status='pending' AND fresh_until>?
               ORDER BY observed_at, id LIMIT ?""",
            (mission_id, now, limit),
        ).fetchall()

    def record_proposal(self, mission_id: str, signal_id: str, *, draft: dict[str, Any] | None,
                        rationale: str, decision: str, reason: str = "") -> tuple[sqlite3.Row, bool]:
        """Persist a proposal decision once after Core-owned policy checks."""
        if decision not in {"proposed", "accepted", "rejected", "deferred"}:
            raise ValueError("invalid proposal decision")
        mission = self.mission(mission_id)
        signal = self.db.execute("SELECT * FROM director_signals WHERE id=?", (signal_id,)).fetchone()
        if signal is None or signal["mission_id"] != mission_id:
            raise ValueError("signal does not belong to mission")
        if mission["status"] != "active":
            decision, reason, draft = "rejected", "MISSION_NOT_ACTIVE", None
        elif datetime.fromisoformat(signal["fresh_until"]) <= self.clock():
            decision, reason, draft = "rejected", "SIGNAL_STALE", None
        if decision in {"proposed", "accepted"}:
            delegated = set(json.loads(mission["delegated_scopes_json"]))
            if "coding.propose" not in delegated:
                decision, reason, draft = "rejected", "PROPOSAL_SCOPE_NOT_DELEGATED", None
        if decision in {"proposed", "accepted"}:
            if not isinstance(draft, dict):
                raise ValueError("accepted proposal requires a contract draft")
            required = {"repo", "objective", "acceptance", "risk", "estimated_cost", "allowed_paths"}
            if required - draft.keys():
                decision, reason = "rejected", "CONTRACT_FIELDS_MISSING"
            elif draft["repo"] not in set(json.loads(mission["allowed_repositories_json"])):
                decision, reason = "rejected", "REPOSITORY_OUT_OF_SCOPE"
            elif (not isinstance(draft["objective"], str) or not draft["objective"].strip()
                  or len(draft["objective"]) > 1000):
                decision, reason = "rejected", "OBJECTIVE_INVALID"
            elif draft["risk"] not in set(json.loads(mission["allowed_risks_json"])):
                decision, reason = "rejected", "RISK_NOT_DELEGATED"
            elif (not isinstance(draft["acceptance"], list) or not draft["acceptance"]
                  or len(draft["acceptance"]) > 20
                  or any(not isinstance(item, str) or not item.strip() or len(item) > 500
                         for item in draft["acceptance"])):
                decision, reason = "rejected", "ACCEPTANCE_CRITERIA_INVALID"
            elif (not isinstance(draft["allowed_paths"], list) or not draft["allowed_paths"]
                  or any(not isinstance(path, str) or not path.strip() or len(path) > 256 or "\x00" in path
                         or path.startswith("/")
                         or ".." in path.replace("\\", "/").split("/") for path in draft["allowed_paths"])):
                decision, reason = "rejected", "PATH_SCOPE_INVALID"
            elif ("forbidden_paths" in draft and
                  (not isinstance(draft["forbidden_paths"], list) or len(draft["forbidden_paths"]) > 100
                   or any(not isinstance(path, str) or not path.strip() or len(path) > 256 or "\x00" in path
                          or path.startswith("/")
                          or ".." in path.replace("\\", "/").split("/")
                          for path in draft["forbidden_paths"]))):
                decision, reason = "rejected", "FORBIDDEN_PATHS_INVALID"
            elif (not isinstance(draft["estimated_cost"], int) or isinstance(draft["estimated_cost"], bool)
                  or draft["estimated_cost"] < 1):
                decision, reason = "rejected", "COST_ESTIMATE_INVALID"
            else:
                budget = json.loads(mission["budget_json"])
                if draft["estimated_cost"] > budget.get("max_task_cost", 100):
                    decision, reason = "rejected", "TASK_BUDGET_EXCEEDED"
                active = self.db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE project_id=? AND status IN ('queued','leased','running')",
                    (mission["project_id"],),
                ).fetchone()[0]
                if decision == "accepted" and active >= budget.get("max_active_jobs", 1):
                    decision, reason = "deferred", "ACTIVE_JOB_BUDGET_EXHAUSTED"
                total = self.db.execute(
                    "SELECT COALESCE(SUM(CAST(json_extract(contract_json, '$.estimated_cost') AS INTEGER)),0) "
                    "FROM director_proposals WHERE mission_id=? AND status IN ('accepted','queued','verified','failed')",
                    (mission_id,),
                ).fetchone()[0]
                if (decision == "accepted" and "max_total_cost" in budget
                        and total + draft["estimated_cost"] > budget["max_total_cost"]):
                    decision, reason = "deferred", "MISSION_BUDGET_EXHAUSTED"
                max_tasks = budget.get("max_tasks")
                created = self.db.execute(
                    "SELECT COUNT(*) FROM director_proposals WHERE mission_id=? AND status IN ('accepted','queued','verified','failed')",
                    (mission_id,),
                ).fetchone()[0]
                if decision == "accepted" and max_tasks is not None and created >= max_tasks:
                    decision, reason = "deferred", "MISSION_TASK_LIMIT_REACHED"
            if decision == "accepted" and "coding.execute" not in delegated:
                decision, reason = "proposed", "EXECUTION_APPROVAL_REQUIRED"
        now = _iso(self.clock())
        key = f"{mission_id}:{signal_id}"
        proposal_id = "proposal_" + hashlib.sha256(key.encode()).hexdigest()
        status = {"proposed": "proposed", "accepted": "accepted",
                  "rejected": "rejected", "deferred": "deferred"}[decision]
        contract_json = json.dumps(draft, sort_keys=True, separators=(",", ":")) if draft else None
        decision_json = json.dumps({"decision": decision, "reason": reason}, sort_keys=True)
        with self.db.transaction():
            inserted = self.db.execute(
                """INSERT OR IGNORE INTO director_proposals(
                    id, mission_id, signal_id, project_id, idempotency_key, status,
                    rationale, contract_json, decision_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (proposal_id, mission_id, signal_id, mission["project_id"], key, status,
                 rationale[:4000], contract_json if decision in {"proposed", "accepted"} else None,
                 decision_json, now, now),
            ).rowcount == 1
            if inserted:
                self.db.execute("UPDATE director_signals SET status='processed' WHERE id=? AND status='pending'",
                                (signal_id,))
                self.events.append("organization.proposal_decided", project_id=mission["project_id"],
                                   source="director", source_id=proposal_id,
                                   payload={"mission_id": mission_id, "signal_id": signal_id,
                                            "decision": decision, "reason": reason})
        row = self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()
        return row, inserted

    def ready_proposals(self, mission_id: str, *, limit: int = 100) -> list[sqlite3.Row]:
        mission = self.mission(mission_id)
        if mission["status"] != "active":
            return []
        return self.db.execute(
            "SELECT * FROM director_proposals WHERE mission_id=? AND status='accepted' "
            "ORDER BY created_at, id LIMIT ?", (mission_id, limit),
        ).fetchall()

    def reconcile_proposal_outcomes(self, mission_id: str) -> int:
        """Mirror only terminal Core outcomes; success requires a verified Core artifact."""
        rows = self.db.execute(
            """SELECT p.id, p.project_id, p.job_id, j.status AS job_status
               FROM director_proposals p LEFT JOIN jobs j ON j.id=p.job_id
               WHERE p.mission_id=? AND p.status='queued' ORDER BY p.created_at, p.id""",
            (mission_id,),
        ).fetchall()
        changed_count = 0
        for row in rows:
            if row["job_status"] == "succeeded":
                verified = self.db.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE job_id=? AND verified=1", (row["job_id"],)
                ).fetchone()[0] > 0
                terminal, reason = ("verified", "CORE_VERIFIED_ARTIFACT") if verified else (
                    "failed", "CORE_SUCCESS_WITHOUT_VERIFIED_ARTIFACT")
            elif row["job_status"] in {"failed", "cancelled"}:
                terminal, reason = "failed", "CORE_JOB_" + row["job_status"].upper()
            else:
                continue
            with self.db.transaction():
                changed = self.db.execute(
                    "UPDATE director_proposals SET status=?, decision_json=?, updated_at=?, version=version+1 "
                    "WHERE id=? AND status='queued'",
                    (terminal, json.dumps({"decision": terminal, "reason": reason}, sort_keys=True),
                     _iso(self.clock()), row["id"]),
                ).rowcount
                if changed:
                    self.events.append("organization.proposal_outcome_reconciled", project_id=row["project_id"],
                                       source="director", source_id=row["id"],
                                       payload={"job_id": row["job_id"], "status": terminal, "reason": reason})
                    changed_count += 1
        return changed_count

    def mark_proposal_queued(self, proposal_id: str, *, task_id: str, job_id: str) -> sqlite3.Row:
        if not task_id or not job_id:
            raise ValueError("task_id and job_id are required")
        proposal = self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal["status"] == "queued":
            if proposal["task_id"] != task_id or proposal["job_id"] != job_id:
                raise ValueError("proposal already queued with a different task or job")
            return proposal
        if proposal["status"] != "accepted":
            raise ValueError("only accepted proposals can be queued")
        mission = self.mission(proposal["mission_id"])
        if mission["status"] != "active" or datetime.fromisoformat(mission["expires_at"]) <= self.clock():
            raise ValueError("mission is not active at dispatch time")
        if "coding.propose" not in json.loads(mission["delegated_scopes_json"]):
            raise ValueError("mission does not delegate proposal approval")
        with self.db.transaction():
            changed = self.db.execute(
                "UPDATE director_proposals SET status='queued', task_id=?, job_id=?, updated_at=?, version=version+1 "
                "WHERE id=? AND status='accepted'",
                (task_id, job_id, _iso(self.clock()), proposal_id),
            ).rowcount
            if changed != 1:
                raise ValueError("proposal state changed before dispatch receipt")
            self.events.append("organization.proposal_queued", project_id=proposal["project_id"],
                               source="director", source_id=proposal_id,
                               payload={"task_id": task_id, "job_id": job_id})
        return self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()

    def approve_proposal(self, proposal_id: str, *, expected_version: int, actor: str) -> sqlite3.Row:
        """Approve a proposed contract only within the mission's already-delegated scope."""
        if (not isinstance(actor, str) or not actor.strip()
                or not isinstance(expected_version, int) or isinstance(expected_version, bool)):
            raise ValueError("approval actor is required")
        proposal = self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()
        if proposal is None:
            raise KeyError(proposal_id)
        mission = self.mission(proposal["mission_id"])
        signal = self.db.execute("SELECT * FROM director_signals WHERE id=?", (proposal["signal_id"],)).fetchone()
        if proposal["status"] != "proposed" or proposal["version"] != expected_version:
            raise ValueError("proposal state/version changed")
        if mission["status"] != "active" or datetime.fromisoformat(signal["fresh_until"]) <= self.clock():
            raise ValueError("mission or source signal is no longer active/fresh")
        if "coding.execute" not in json.loads(mission["delegated_scopes_json"]):
            raise ValueError("mission does not delegate coding execution")
        draft = json.loads(proposal["contract_json"] or "{}")
        if (draft.get("repo") not in json.loads(mission["allowed_repositories_json"])
                or draft.get("risk") not in json.loads(mission["allowed_risks_json"])):
            raise ValueError("proposal no longer satisfies mission scope")
        budget = json.loads(mission["budget_json"])
        active_jobs = self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE project_id=? AND status IN ('queued','leased','running')",
            (mission["project_id"],),
        ).fetchone()[0]
        if active_jobs >= budget.get("max_active_jobs", 1):
            raise ValueError("mission active-job budget exhausted")
        total = self.db.execute(
            "SELECT COALESCE(SUM(CAST(json_extract(contract_json, '$.estimated_cost') AS INTEGER)),0) "
            "FROM director_proposals WHERE mission_id=? AND status IN ('accepted','queued','verified','failed')",
            (mission["id"],),
        ).fetchone()[0]
        if (draft.get("estimated_cost", 0) > budget.get("max_task_cost", 100)
                or ("max_total_cost" in budget and total + draft.get("estimated_cost", 0) > budget["max_total_cost"])):
            raise ValueError("mission task/cumulative budget exhausted")
        created = self.db.execute(
            "SELECT COUNT(*) FROM director_proposals WHERE mission_id=? AND status IN ('accepted','queued','verified','failed')",
            (mission["id"],),
        ).fetchone()[0]
        if "max_tasks" in budget and created >= budget["max_tasks"]:
            raise ValueError("mission task limit reached")
        allowed_paths = draft.get("allowed_paths")
        if (not isinstance(allowed_paths, list) or not allowed_paths
                or any(not isinstance(path, str) or not path.strip() or len(path) > 256 or "\x00" in path
                       or path.startswith("/")
                       or ".." in path.replace("\\", "/").split("/") for path in allowed_paths)):
            raise ValueError("proposal path scope is invalid")
        with self.db.transaction():
            changed = self.db.execute(
                "UPDATE director_proposals SET status='accepted', decision_json=?, updated_at=?, version=version+1 "
                "WHERE id=? AND status='proposed' AND version=?",
                (json.dumps({"decision": "accepted", "reason": "HUMAN_APPROVED",
                             "actor": actor[:120]}, sort_keys=True),
                 _iso(self.clock()), proposal_id, expected_version),
            ).rowcount
            if changed != 1:
                raise ValueError("proposal changed before approval")
            self.events.append("organization.proposal_approved", project_id=proposal["project_id"],
                               source="control-plane", source_id=proposal_id,
                               payload={"actor": actor[:120]})
        return self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()

    def reject_proposal(self, proposal_id: str, *, expected_version: int, reason: str) -> sqlite3.Row:
        if (not isinstance(reason, str) or not reason.strip()
                or not isinstance(expected_version, int) or isinstance(expected_version, bool)):
            raise ValueError("rejection reason is required")
        proposal = self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal["status"] != "proposed" or proposal["version"] != expected_version:
            raise ValueError("proposal state/version changed")
        with self.db.transaction():
            changed = self.db.execute(
                "UPDATE director_proposals SET status='rejected', decision_json=?, updated_at=?, version=version+1 "
                "WHERE id=? AND status='proposed' AND version=?",
                (json.dumps({"decision": "rejected", "reason": reason[:500]}, sort_keys=True),
                 _iso(self.clock()), proposal_id, expected_version),
            ).rowcount
            if changed != 1:
                raise ValueError("proposal changed before rejection")
            self.events.append("organization.proposal_rejected", project_id=proposal["project_id"],
                               source="control-plane", source_id=proposal_id,
                               payload={"reason": reason[:500]})
        return self.db.execute("SELECT * FROM director_proposals WHERE id=?", (proposal_id,)).fetchone()

    def create_objective(
        self,
        project_id: str,
        title: str,
        *,
        description: str = "",
        status: str = "planned",
        weight: int = 0,
        objective_id: str | None = None,
        activation_policy: str = "manual_only",
        depends_on: tuple[str, ...] = (),
    ) -> sqlite3.Row:
        _validate_objective_status(status)
        if not title.strip() or not 0 <= weight <= 100 or activation_policy not in ACTIVATION_POLICIES:
            raise ValueError("objective title and weight 0..100 are required")
        if any(not isinstance(value, str) or not value.strip() for value in depends_on):
            raise ValueError("depends_on must contain objective ids")
        for dependency in depends_on:
            row = self.db.execute("SELECT project_id FROM organization_objectives WHERE id=?", (dependency,)).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ValueError("objective dependency does not belong to project")
        self._require_project(project_id)
        now = _iso(self.clock())
        objective_id = objective_id or f"obj_{uuid.uuid4().hex}"
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO organization_objectives(id, project_id, title, description, status, weight,
                   activation_policy, depends_on_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (objective_id, project_id, title, description, status, weight,
                 activation_policy, json.dumps(list(depends_on), sort_keys=True), now, now),
            )
            self.events.append("organization.objective_created", project_id=project_id, source="organization",
                               source_id=objective_id, payload={"title": title, "status": status, "weight": weight})
        return self.db.execute("SELECT * FROM organization_objectives WHERE id=?", (objective_id,)).fetchone()

    def set_task_meta(
        self,
        task_id: str,
        *,
        objective_id: str | None = None,
        user_impact: int = 0,
        dependency_status: str = "ready",
        risk: str = "low",
        contract: dict[str, Any] | None = None,
        estimated_cost: int = 1,
    ) -> sqlite3.Row:
        if (not 0 <= user_impact <= 100 or risk not in RISK_PENALTY
                or dependency_status not in DEPENDENCY_STATUSES or not isinstance(estimated_cost, int)
                or isinstance(estimated_cost, bool) or not 1 <= estimated_cost <= 100):
            raise ValueError("invalid task metadata")
        task = self.db.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise KeyError(task_id)
        if objective_id is not None:
            objective = self.db.execute("SELECT project_id FROM organization_objectives WHERE id=?", (objective_id,)).fetchone()
            if objective is None or objective["project_id"] != task["project_id"]:
                raise ValueError("objective does not belong to task project")
        contract_json = json.dumps(contract, sort_keys=True) if contract is not None else None
        self.db.execute(
            """INSERT INTO organization_task_meta(task_id, objective_id, user_impact, dependency_status, risk, contract_json, estimated_cost)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET objective_id=excluded.objective_id,
                 user_impact=excluded.user_impact, dependency_status=excluded.dependency_status, risk=excluded.risk,
                 contract_json=COALESCE(excluded.contract_json, organization_task_meta.contract_json),
                 estimated_cost=excluded.estimated_cost""",
            (task_id, objective_id, user_impact, dependency_status, risk, contract_json, estimated_cost),
        )
        return self.db.execute("SELECT * FROM organization_task_meta WHERE task_id=?", (task_id,)).fetchone()

    def select_next_task(self, project_id: str, *, require_contract: bool = False) -> dict[str, Any] | None:
        profile = self.profile(project_id)
        project = self.db.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None or project["status"] != "active":
            return None
        policy = _normalize_policy(json.loads(profile["policy_json"]))
        budget = _normalize_budget(json.loads(profile["budget_json"]))
        max_active = budget.get("max_active_jobs")
        if isinstance(max_active, int) and self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE project_id=? AND status IN ('leased', 'running')", (project_id,)
        ).fetchone()[0] >= max_active:
            return None
        rows = self.db.execute(
            """SELECT t.*, m.objective_id, m.user_impact, m.dependency_status, m.risk, m.contract_json,
                      COALESCE(o.weight, 0) AS objective_weight, COALESCE(o.title, '') AS objective_title
                 FROM tasks t
                 LEFT JOIN organization_task_meta m ON m.task_id=t.id
                 LEFT JOIN organization_objectives o ON o.id=m.objective_id
                WHERE t.project_id=? AND t.status='ready'
                  AND COALESCE(m.dependency_status, 'ready')='ready'
                  AND (m.objective_id IS NULL OR o.status='active')
                ORDER BY t.created_at, t.id""",
            (project_id,),
        ).fetchall()
        if not rows:
            return None
        if require_contract:
            rows = [row for row in rows if row["contract_json"] is not None]
            if not rows:
                return None
        allowed_risks = policy.get("allowed_risks")
        if isinstance(allowed_risks, list):
            rows = [row for row in rows if (row["risk"] or "low") in allowed_risks]
        if not rows:
            return None
        def score(row: sqlite3.Row) -> int:
            return (PRIORITY_SCORE.get(row["priority"], 0) + int(row["objective_weight"] or 0)
                    + int(row["user_impact"] or 0) - RISK_PENALTY.get(row["risk"] or "low", 0))
        selected = sorted(rows, key=lambda row: (-score(row), row["created_at"], row["id"]))[0]
        result = dict(selected)
        result["selection_score"] = score(selected)
        result["policy"] = policy
        result["budget"] = budget
        result["selection_reason"] = {
            "priority": PRIORITY_SCORE.get(selected["priority"], 0),
            "objective_weight": int(selected["objective_weight"] or 0),
            "user_impact": int(selected["user_impact"] or 0),
            "risk_penalty": RISK_PENALTY.get(selected["risk"] or "low", 0),
            "dependencies": selected["dependency_status"] or "ready",
        }
        return result

    def set_autopilot(self, project_id: str, enabled: bool) -> sqlite3.Row:
        self._require_project(project_id)
        state = "ON" if enabled else "OFF"
        with self.db.transaction():
            self.db.execute("UPDATE organization_profiles SET autopilot_enabled=?, autopilot_state=?, updated_at=? WHERE project_id=?",
                            (int(enabled), state, _iso(self.clock()), project_id))
        return self.profile(project_id)

    def reconcile_objective_lifecycle(self, project_id: str) -> list[dict[str, Any]]:
        """Apply deterministic objective transitions before task selection."""
        profile = self.profile(project_id)
        project = self.db.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project or project["status"] != "active":
            return []
        policy = _normalize_policy(json.loads(profile["policy_json"]))
        allowed_risks = policy.get("allowed_risks")
        transitions: list[dict[str, Any]] = []
        now = _iso(self.clock())
        rows = self.db.execute("SELECT * FROM organization_objectives WHERE project_id=? ORDER BY weight DESC, created_at, id", (project_id,)).fetchall()
        with self.db.transaction():
            for objective in rows:
                if objective["status"] != "active":
                    continue
                task_rows = self.db.execute(
                    "SELECT t.status FROM tasks t JOIN organization_task_meta m ON m.task_id=t.id WHERE m.objective_id=?",
                    (objective["id"],),
                ).fetchall()
                if task_rows and all(row["status"] == "done" for row in task_rows):
                    changed = self.db.execute(
                        "UPDATE organization_objectives SET status='done', updated_at=?, version=version+1 WHERE id=? AND status='active'",
                        (now, objective["id"],),
                    ).rowcount
                    if not changed:
                        continue
                    transition = {"objective_id": objective["id"], "from": "active", "to": "done", "reason": "ALL_TASKS_DONE"}
                    self.events.append("organization.objective_transitioned", project_id=project_id, source="organization", source_id=objective["id"], payload=transition)
                    transitions.append(transition)
            active_exists = self.db.execute("SELECT 1 FROM organization_objectives WHERE project_id=? AND status='active'", (project_id,)).fetchone()
            if active_exists:
                return transitions
            candidates = self.db.execute(
                "SELECT * FROM organization_objectives WHERE project_id=? AND status='planned' AND activation_policy='auto' ORDER BY weight DESC, created_at, id",
                (project_id,),
            ).fetchall()
            for objective in candidates:
                try:
                    dependencies = json.loads(objective["depends_on_json"] or "[]")
                except (TypeError, ValueError):
                    continue
                dependency_rows = [self.db.execute("SELECT status FROM organization_objectives WHERE id=?", (dep,)).fetchone() for dep in dependencies]
                if any(row is None or row["status"] != "done" for row in dependency_rows):
                    continue
                ready = self.db.execute(
                    "SELECT m.risk FROM tasks t JOIN organization_task_meta m ON m.task_id=t.id WHERE t.project_id=? AND m.objective_id=? AND t.status='ready' AND m.dependency_status='ready' AND m.contract_json IS NOT NULL",
                    (project_id, objective["id"]),
                ).fetchall()
                if not any(allowed_risks is None or (row["risk"] or "low") in allowed_risks for row in ready):
                    continue
                changed = self.db.execute(
                    "UPDATE organization_objectives SET status='active', updated_at=?, version=version+1 WHERE id=? AND status='planned'",
                    (now, objective["id"],),
                ).rowcount
                if not changed:
                    continue
                transition = {"objective_id": objective["id"], "from": "planned", "to": "active", "reason": "AUTO_POLICY_ELIGIBLE"}
                self.events.append("organization.objective_transitioned", project_id=project_id, source="organization", source_id=objective["id"], payload=transition)
                transitions.append(transition)
                break
        return transitions

    def record_dispatch(self, project_id: str, reason: str) -> None:
        self.db.execute("UPDATE organization_profiles SET last_dispatch_at=?, last_dispatch_reason=?, updated_at=? WHERE project_id=?",
                        (_iso(self.clock()), reason, _iso(self.clock()), project_id))

    def advance_baseline(self, project_id: str, accepted_sha: str) -> sqlite3.Row:
        """Move the organization's accepted baseline after an accepted task."""
        if not accepted_sha.strip():
            raise ValueError("accepted_sha must not be empty")
        self._require_project(project_id)
        with self.db.transaction():
            self.db.execute(
                "UPDATE organization_profiles SET accepted_baseline=?, updated_at=? WHERE project_id=?",
                (accepted_sha, _iso(self.clock()), project_id),
            )
            self.events.append("organization.baseline.advanced", project_id=project_id,
                               source="organization", source_id=project_id,
                               payload={"accepted_baseline": accepted_sha})
        return self.profile(project_id)

    def task_contract(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT contract_json FROM organization_task_meta WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row["contract_json"]) if row and row["contract_json"] else None

    def autopilot_projects(self) -> list[str]:
        return [row["project_id"] for row in self.db.execute(
            "SELECT project_id FROM organization_profiles WHERE autopilot_enabled=1 ORDER BY project_id"
        ).fetchall()]

    def assert_enqueue_allowed(self, project_id: str, *, risk: str, priority: str,
                               human_approved: bool = False) -> sqlite3.Row | None:
        project = self.db.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise KeyError(project_id)
        profile = self.db.execute("SELECT * FROM organization_profiles WHERE project_id=?", (project_id,)).fetchone()
        if profile is None:
            return None
        if project["status"] != "active":
            raise ValueError("organization is not active")
        policy = _normalize_policy(json.loads(profile["policy_json"]))
        allowed = policy.get("allowed_risks")
        if allowed is not None and risk not in allowed:
            raise ValueError("organization policy denies task risk")
        if priority == "critical" and not human_approved:
            raise ValueError("critical priority requires explicit human approval")
        budget = _normalize_budget(json.loads(profile["budget_json"]))
        maximum = budget.get("max_active_jobs")
        if maximum is not None:
            active = self.db.execute("SELECT COUNT(*) FROM jobs WHERE project_id=? AND status IN ('queued','leased','running')",
                                     (project_id,)).fetchone()[0]
            if active >= maximum:
                raise ValueError("organization budget exhausted")
        return profile

    def snapshot(self, project_id: str) -> dict[str, Any]:
        profile = dict(self.profile(project_id))
        profile["policy"] = json.loads(profile.pop("policy_json"))
        profile["budget"] = json.loads(profile.pop("budget_json"))
        objectives = [dict(row) for row in self.db.execute(
            "SELECT * FROM organization_objectives WHERE project_id=? ORDER BY weight DESC, created_at, id", (project_id,)
        ).fetchall()]
        backlog = [dict(row) for row in self.db.execute(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY updated_at DESC, id DESC", (project_id,)
        ).fetchall()]
        return {"profile": profile, "objectives": objectives, "backlog": backlog,
                "next_task": self.select_next_task(project_id)}

    def _require_project(self, project_id: str) -> None:
        if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise KeyError(project_id)
