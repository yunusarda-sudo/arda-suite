"""Phase 0 (M1) deterministic fake-runner contract harness (P01-P14).

Reusable execution-policy primitives for ARDA-style autonomous work:
versioned model/capability registry, fail-closed policy file loader,
atomic SQLite budget ledger, research gate; version-evidence validation,
runtime tool/path boundary, and a controller that separates policy,
execution, acceptance, and accounting results on every attempt.

No real model/provider calls happen here: adapters, acceptance evaluators
and clocks are caller-injected, so every scenario below is deterministic.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from arda.core import Database, JobQueue, StaleLeaseError, _iso, _utc_now

MICRO = 1_000_000

POLICY_RESULT_ALLOWED = "allowed"
POLICY_RESULT_DENIED = "denied"
POLICY_RESULT_NEEDS_RESEARCH = "needs_research"
POLICY_RESULT_NEEDS_REVIEW = "needs_review"

EXECUTION_NOT_STARTED = "not_started"
EXECUTION_EXIT_0 = "exit_0"
EXECUTION_EXIT_NONZERO = "exit_nonzero"
EXECUTION_TIMEOUT = "timeout"
EXECUTION_LOST = "lost"
EXECUTION_UNKNOWN = "unknown"

ACCEPTANCE_NOT_RUN = "not_run"
ACCEPTANCE_PASSED = "passed"
ACCEPTANCE_FAILED = "failed"
ACCEPTANCE_INCONCLUSIVE = "inconclusive"

ACCOUNTING_NOT_RESERVED = "not_reserved"
ACCOUNTING_RESERVED = "reserved"
ACCOUNTING_RECONCILED = "reconciled"
ACCOUNTING_INCOMPLETE = "incomplete"
ACCOUNTING_OVER_CAP = "over_cap"

TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_BLOCKED = "blocked"
TASK_DENIED = "denied"

VERDICT_OK = "ok"
VERDICT_VERSION_MISMATCH = "version_mismatch"
VERDICT_STALE = "stale"
VERDICT_INSUFFICIENT = "insufficient"

SENSITIVE_PATTERNS = (
    "auth/*",
    "*/auth/*",
    "migrations/*",
    "*/migrations/*",
    ".env*",
    "*/.env*",
    "secrets/*",
    "*/secrets/*",
)

_UNSET = object()


class Phase0Error(RuntimeError):
    pass


class PolicyUnreadable(Phase0Error):
    pass


class ModelUnresolved(Phase0Error):
    pass


class EffortUnsupported(Phase0Error):
    pass


class BudgetExceeded(Phase0Error):
    pass


def to_micros(amount: float) -> int:
    return int(round(float(amount) * MICRO))


def to_usd(micros: int) -> float:
    return micros / MICRO


@dataclass(frozen=True)
class ModelDescriptor:
    provider: str
    model: str
    allowed_efforts: tuple[str, ...]
    paid: bool = True


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    effort: str
    paid: bool
    registry_version: str


class ModelRegistry:
    def __init__(self, descriptors: Iterable[ModelDescriptor], *, version: str):
        self.version = version
        catalogue: dict[tuple[str, str], ModelDescriptor] = {}
        for descriptor in descriptors:
            key = (descriptor.provider, descriptor.model)
            if not descriptor.provider or not descriptor.model:
                raise ValueError("model descriptor requires provider and model")
            if not descriptor.allowed_efforts:
                raise ValueError("model descriptor requires allowed efforts")
            if key in catalogue:
                raise ValueError(f"duplicate model descriptor: {key}")
            catalogue[key] = descriptor
        self._catalogue = dict(sorted(catalogue.items()))

    def resolve(self, provider: str, model: str, effort: str) -> ResolvedModel:
        descriptor = self._catalogue.get((provider, model))
        if descriptor is None:
            raise ModelUnresolved(
                f"model {provider}/{model} is not in registry v{self.version}"
            )
        if effort not in descriptor.allowed_efforts:
            raise EffortUnsupported(
                f"effort {effort!r} is not supported by {provider}/{model}; "
                f"supported: {sorted(descriptor.allowed_efforts)}"
            )
        return ResolvedModel(provider, model, effort, descriptor.paid, self.version)

    def models(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._catalogue)

    def supported_efforts(self, provider: str, model: str) -> tuple[str, ...]:
        descriptor = self._catalogue.get((provider, model))
        return () if descriptor is None else tuple(sorted(descriptor.allowed_efforts))


_POLICY_KEYS = frozenset(
    {
        "policy_version",
        "allowed_models",
        "allowed_efforts",
        "max_attempts_for_task",
        "parallel_attempts",
    }
)


@dataclass(frozen=True)
class ExecutionPolicy:
    policy_version: str
    allowed_models: frozenset[tuple[str, str]]
    allowed_efforts: frozenset[str]
    max_attempts_for_task: int
    parallel_attempts: int


def load_policy_file(path: str | Path) -> ExecutionPolicy:
    """Strict, fail-closed policy loader (P03)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PolicyUnreadable(f"policy not readable: {error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PolicyUnreadable(f"policy is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise PolicyUnreadable("policy must be a JSON object")
    keys = frozenset(data)
    missing = sorted(_POLICY_KEYS - keys)
    unknown = sorted(keys - _POLICY_KEYS)
    if missing:
        raise PolicyUnreadable(f"policy missing required keys: {missing}")
    if unknown:
        raise PolicyUnreadable(f"policy has unknown keys: {unknown}")
    version = data["policy_version"]
    if not isinstance(version, str) or not version.strip():
        raise PolicyUnreadable("policy_version must be a non-empty string")
    raw_models = data["allowed_models"]
    if not isinstance(raw_models, list) or not raw_models:
        raise PolicyUnreadable("allowed_models must be a non-empty list")
    allowed_models: set[tuple[str, str]] = set()
    for entry in raw_models:
        if not isinstance(entry, str) or entry.count("/") != 1:
            raise PolicyUnreadable(
                "allowed_models entries must be 'provider/model' strings"
            )
        provider, model = entry.split("/")
        if not provider.strip() or not model.strip():
            raise PolicyUnreadable("allowed_models entries must be non-empty")
        allowed_models.add((provider.strip(), model.strip()))
    raw_efforts = data["allowed_efforts"]
    if not isinstance(raw_efforts, list) or not raw_efforts:
        raise PolicyUnreadable("allowed_efforts must be a non-empty list")
    if any(not isinstance(value, str) or not value.strip() for value in raw_efforts):
        raise PolicyUnreadable("allowed_efforts must contain non-empty strings")
    max_attempts = data["max_attempts_for_task"]
    parallel = data["parallel_attempts"]
    for name, value in (
        ("max_attempts_for_task", max_attempts),
        ("parallel_attempts", parallel),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise PolicyUnreadable(f"{name} must be a positive integer")
    if parallel > max_attempts:
        raise PolicyUnreadable("parallel_attempts must not exceed max_attempts_for_task")
    return ExecutionPolicy(
        version,
        frozenset(allowed_models),
        frozenset(value.strip() for value in raw_efforts),
        max_attempts,
        parallel,
    )


def ensure_schema(db: Database) -> None:
    with db.transaction():
        db.execute(
            """CREATE TABLE IF NOT EXISTS phase0_budget_accounts (
                task_id TEXT PRIMARY KEY,
                cap_micros INTEGER NOT NULL,
                reserved_micros INTEGER NOT NULL DEFAULT 0,
                spent_micros INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS phase0_budget_reservations (
                reservation_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                amount_micros INTEGER NOT NULL,
                status TEXT NOT NULL,
                reported_usage_micros INTEGER,
                unresolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS phase0_research_decisions (
                decision_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS phase0_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT,
                attempt_id TEXT,
                type TEXT NOT NULL,
                policy_version TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_phase0_events_task ON phase0_events(task_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_phase0_reservations_task "
            "ON phase0_budget_reservations(task_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_phase0_decisions_task "
            "ON phase0_research_decisions(task_id)"
        )


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    task_id: str
    job_id: str
    attempt: int
    amount_micros: int
    status: str
    reported_usage_micros: int | None
    unresolved: bool


def _reservation_record(row: sqlite3.Row) -> Reservation:
    return Reservation(
        row["reservation_id"],
        row["task_id"],
        row["job_id"],
        row["attempt"],
        row["amount_micros"],
        row["status"],
        row["reported_usage_micros"],
        bool(row["unresolved"]),
    )


class BudgetLedger:
    """Per-task atomic spend cap backed by SQLite (P06)."""

    def __init__(self, db: Database, *, clock: Callable[[], datetime] = _utc_now):
        self.db = db
        self.clock = clock

    def ensure_account(self, task_id: str, cap_micros: int) -> None:
        with self.db.transaction():
            row = self.db.execute(
                "SELECT cap_micros FROM phase0_budget_accounts WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO phase0_budget_accounts"
                    "(task_id, cap_micros, reserved_micros, spent_micros, updated_at)"
                    " VALUES (?, ?, 0, 0, ?)",
                    (task_id, int(cap_micros), _iso(self.clock())),
                )
            elif int(row["cap_micros"]) != int(cap_micros):
                raise Phase0Error(
                    f"budget cap conflict for task {task_id}: "
                    f"existing {row['cap_micros']} != requested {cap_micros}"
                )

    def account(self, task_id: str) -> dict[str, int]:
        row = self.db.execute(
            "SELECT cap_micros, reserved_micros, spent_micros "
            "FROM phase0_budget_accounts WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise Phase0Error(f"no budget account for task {task_id}")
        return {
            "cap_micros": row["cap_micros"],
            "reserved_micros": row["reserved_micros"],
            "spent_micros": row["spent_micros"],
        }

    def reserve(
        self, task_id: str, job_id: str, attempt: int, amount_micros: int
    ) -> Reservation:
        if amount_micros < 0:
            raise Phase0Error("reservation amount must be non-negative")
        reservation_id = f"res_{uuid.uuid4().hex}"
        with self.db.transaction():  # BEGIN IMMEDIATE
            row = self.db.execute(
                "SELECT cap_micros, reserved_micros, spent_micros "
                "FROM phase0_budget_accounts WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise Phase0Error(f"no budget account for task {task_id}")
            committed = int(row["reserved_micros"]) + int(row["spent_micros"])
            if committed + int(amount_micros) > int(row["cap_micros"]):
                raise BudgetExceeded(
                    f"budget cap exceeded for task {task_id}: "
                    f"committed={committed} amount={amount_micros} cap={row['cap_micros']}"
                )
            now = _iso(self.clock())
            unchanged = self.db.execute(
                "UPDATE phase0_budget_accounts "
                "SET reserved_micros = reserved_micros + ?, updated_at = ? "
                "WHERE task_id = ? AND reserved_micros + spent_micros + ? <= cap_micros",
                (int(amount_micros), now, task_id, int(amount_micros)),
            ).rowcount
            if unchanged != 1:
                raise BudgetExceeded(
                    f"budget cap exceeded for task {task_id} (concurrent update)"
                )
            self.db.execute(
                "INSERT INTO phase0_budget_reservations"
                "(reservation_id, task_id, job_id, attempt, amount_micros, status,"
                " reported_usage_micros, unresolved, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'active', NULL, 0, ?, ?)",
                (reservation_id, task_id, job_id, int(attempt), int(amount_micros), now, now),
            )
        return self.reservation(job_id, attempt)

    def reservation(self, job_id: str, attempt: int) -> Reservation | None:
        row = self.db.execute(
            "SELECT * FROM phase0_budget_reservations WHERE job_id = ? AND attempt = ?",
            (job_id, int(attempt)),
        ).fetchone()
        return _reservation_record(row) if row is not None else None

    def attempts(self, task_id: str) -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM phase0_budget_reservations WHERE task_id = ?",
                (task_id,),
            )
        )

    def reconcile(
        self, job_id: str, attempt: int, *, usage_micros: int | None
    ) -> Reservation:
        with self.db.transaction():
            row = self.db.execute(
                "SELECT * FROM phase0_budget_reservations WHERE job_id = ? AND attempt = ?",
                (job_id, int(attempt)),
            ).fetchone()
            if row is None:
                raise Phase0Error(f"no reservation for {job_id} attempt {attempt}")
            if row["status"] != "active":
                raise Phase0Error(
                    f"reservation {row['reservation_id']} is already {row['status']}"
                )
            now = _iso(self.clock())
            if usage_micros is None:
                self.db.execute(
                    "UPDATE phase0_budget_reservations SET status = 'unresolved',"
                    " unresolved = 1, updated_at = ? WHERE reservation_id = ?",
                    (now, row["reservation_id"]),
                )
            else:
                usage = int(usage_micros)
                if usage < 0:
                    raise Phase0Error("reported usage must be non-negative")
                self.db.execute(
                    "UPDATE phase0_budget_reservations SET status = 'reconciled',"
                    " reported_usage_micros = ?, unresolved = 0, updated_at = ?"
                    " WHERE reservation_id = ?",
                    (usage, now, row["reservation_id"]),
                )
                self.db.execute(
                    "UPDATE phase0_budget_accounts SET spent_micros = spent_micros + ?,"
                    " reserved_micros = reserved_micros - ?, updated_at = ?"
                    " WHERE task_id = ?",
                    (usage, int(row["amount_micros"]), now, row["task_id"]),
                )
        return self.reservation(job_id, int(attempt))

    def release(self, job_id: str, attempt: int) -> Reservation:
        """Free a reservation for an attempt that never reached a provider."""
        with self.db.transaction():
            row = self.db.execute(
                "SELECT * FROM phase0_budget_reservations WHERE job_id = ? AND attempt = ?",
                (job_id, int(attempt)),
            ).fetchone()
            if row is None:
                raise Phase0Error(f"no reservation for {job_id} attempt {attempt}")
            if row["status"] != "active":
                raise Phase0Error(
                    f"reservation {row['reservation_id']} is already {row['status']}"
                )
            now = _iso(self.clock())
            self.db.execute(
                "UPDATE phase0_budget_reservations SET status = 'released',"
                " unresolved = 0, updated_at = ? WHERE reservation_id = ?",
                (now, row["reservation_id"]),
            )
            self.db.execute(
                "UPDATE phase0_budget_accounts SET reserved_micros = reserved_micros - ?,"
                " updated_at = ? WHERE task_id = ?",
                (int(row["amount_micros"]), now, row["task_id"]),
            )
        return self.reservation(job_id, int(attempt))

    def has_unresolved(self, task_id: str | None = None) -> bool:
        query = (
            "SELECT COUNT(*) FROM phase0_budget_reservations WHERE unresolved = 1"
        )
        params: tuple[Any, ...] = ()
        if task_id is not None:
            query += " AND task_id = ?"
            params = (task_id,)
        return int(self.db.scalar(query, params)) > 0


_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _semver(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _SEMVER.match(value.strip())
    return tuple(int(group) for group in match.groups()) if match else None


def validate_evidence(
    evidence: Mapping[str, Any],
    *,
    required_version: str,
    fresh_after: datetime | None = None,
) -> tuple[str, str]:
    """Version/freshness validation of research evidence (P08 + freshness)."""
    if not isinstance(evidence, Mapping):
        return (VERDICT_INSUFFICIENT, "evidence must be an object")
    required = ("upstream_version", "repo_version", "fresh_at", "source_refs")
    missing = [key for key in required if key not in evidence]
    if missing:
        return (VERDICT_INSUFFICIENT, f"evidence missing fields: {missing}")
    upstream = _semver(evidence["upstream_version"])
    repo = _semver(evidence["repo_version"])
    if upstream is None or repo is None:
        return (VERDICT_INSUFFICIENT, "versions must be semantic (x.y.z)")
    if upstream != repo:
        return (
            VERDICT_VERSION_MISMATCH,
            f"version drift: upstream={evidence['upstream_version']} "
            f"repo={evidence['repo_version']}",
        )
    if required_version and _semver(required_version) and upstream != _semver(required_version):
        return (
            VERDICT_VERSION_MISMATCH,
            f"required {required_version} but evidence says "
            f"upstream={evidence['upstream_version']}",
        )
    try:
        fresh = datetime.fromisoformat(str(evidence["fresh_at"]))
    except (ValueError, TypeError):
        return (VERDICT_INSUFFICIENT, "fresh_at must be an ISO-8601 timestamp")
    if fresh.tzinfo is None:
        fresh = fresh.replace(tzinfo=timezone.utc)
    refs = evidence["source_refs"]
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
        return (VERDICT_INSUFFICIENT, "source_refs must be a non-empty list of strings")
    if fresh_after is not None and fresh < fresh_after:
        return (
            VERDICT_STALE,
            f"stale evidence: fresh_at {fresh.isoformat()} < {fresh_after.isoformat()}",
        )
    return (VERDICT_OK, "")


class ResearchGate:
    """Persistent research decision registry (P07 gating)."""

    def __init__(self, db: Database, *, clock: Callable[[], datetime] = _utc_now):
        self.db = db
        self.clock = clock

    def register(
        self,
        task_id: str,
        decision_id: str,
        evidence: Mapping[str, Any],
        *,
        verdict: str,
        reason: str = "",
    ) -> str:
        if not decision_id:
            raise Phase0Error("research decision_id is required")
        payload = {
            "task_id": task_id,
            "evidence": {key: evidence.get(key) for key in (
                "upstream_version", "repo_version", "fresh_at", "source_refs"
            )},
        }
        structured = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        row = self.db.execute(
            "SELECT task_id, payload FROM phase0_research_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is not None:
            if row["payload"] != structured:
                raise Phase0Error(
                    f"research decision {decision_id} already registered "
                    "with a different payload"
                )
            return self.db.execute(
                "SELECT verdict FROM phase0_research_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()[0]
        self.db.execute(
            "INSERT INTO phase0_research_decisions"
            "(decision_id, task_id, verdict, reason, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (decision_id, task_id, verdict, reason, structured, _iso(self.clock())),
        )
        return verdict

    def satisfied(self, task_id: str, decision_id: str) -> tuple[bool, str]:
        if not decision_id:
            return (False, "no_decision")
        row = self.db.execute(
            "SELECT verdict FROM phase0_research_decisions "
            "WHERE decision_id = ? AND task_id = ?",
            (decision_id, task_id),
        ).fetchone()
        if row is None:
            return (False, "no_decision")
        if row["verdict"] != VERDICT_OK:
            return (False, row["verdict"])
        return (True, "")

    def verdict(self, task_id: str, decision_id: str) -> tuple[str, str]:
        row = self.db.execute(
            "SELECT verdict, reason FROM phase0_research_decisions "
            "WHERE decision_id = ? AND task_id = ?",
            (decision_id, task_id),
        ).fetchone()
        if row is None:
            return (VERDICT_INSUFFICIENT, "no_decision")
        return (row["verdict"], row["reason"])

    def validate(
        self,
        task_id: str,
        decision_id: str,
        evidence: Mapping[str, Any],
        *,
        required_version: str,
        fresh_after: datetime | None = None,
    ) -> str:
        verdict, reason = validate_evidence(
            evidence, required_version=required_version, fresh_after=fresh_after
        )
        self.register(task_id, decision_id, evidence, verdict=verdict, reason=reason)
        return verdict


def scope_error(
    allowed_paths: Sequence[str], forbidden_paths: Sequence[str], changed: Sequence[str]
) -> str | None:
    for path in changed:
        if any(fnmatch.fnmatch(path, pattern) for pattern in forbidden_paths):
            return "forbidden path changed"
    if allowed_paths and any(
        not any(fnmatch.fnmatch(path, pattern) for pattern in allowed_paths)
        for path in changed
    ):
        return "unrelated path changed"
    return None


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    reason: str
    side_effect: str


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    reason: str
    escalated_risk: str | None
    side_effect: str


class RuntimeBoundary:
    """Deny-by-default tool gate and write-path risk boundary (P12/P13)."""

    def __init__(self, *, allowed_tools: Iterable[str] = ("read_file", "write_file", "run_tests")):
        self.allowed_tools = frozenset(allowed_tools)

    def tool_call(self, tool_name: str) -> ToolDecision:
        if tool_name in self.allowed_tools:
            return ToolDecision(True, "", "none")
        return ToolDecision(False, "unknown_tool_denied", "none")

    def write_check(
        self,
        path: str,
        *,
        allowed_paths: Sequence[str],
        forbidden_paths: Sequence[str],
        task_risk: str,
    ) -> WriteDecision:
        posix_path = str(path).replace("\\", "/")
        if any(fnmatch.fnmatch(posix_path, pattern) for pattern in forbidden_paths):
            return WriteDecision(False, "forbidden_path", None, "none")
        if allowed_paths and not any(
            fnmatch.fnmatch(posix_path, pattern) for pattern in allowed_paths
        ):
            return WriteDecision(False, "outside_allowed_scope", None, "none")
        if task_risk == "low" and any(
            fnmatch.fnmatch(posix_path, pattern) for pattern in SENSITIVE_PATTERNS
        ):
            return WriteDecision(False, "risk_escalation_required", "high", "none")
        return WriteDecision(True, "", None, "none")


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    provider: str
    model: str
    effort: str
    cap: float = 0.0
    attempt_budget: float = 0.0
    paid: bool = True
    workspace: Path | str | None = None
    research_required: bool = False
    research_decision_id: str = ""
    required_version: str = ""
    fresh_after: datetime | None = None
    risk: str = "low"
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()

    def with_model(
        self, provider: str, model: str, effort: str
    ) -> "TaskContract":
        return replace(self, provider=provider, model=model, effort=effort)

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["workspace"] = None if self.workspace is None else str(self.workspace)
        payload["fresh_after"] = (
            None if self.fresh_after is None else self.fresh_after.isoformat()
        )
        return payload


@dataclass(frozen=True)
class RunObservation:
    """What a (fake) runner reports back for one attempt."""

    exit_code: int | None = None
    claimed_done: bool = False
    observed_model: str = ""
    observed_effort: str = ""
    usage: float | None = None
    provider_requests: int = 1
    side_effect_state: str = "none"
    changed_paths: tuple[str, ...] = ()
    lost: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    attempt: int
    attempt_id: str | None
    job_id: str | None
    status: str
    policy_result: str
    execution_result: str
    acceptance_result: str
    accounting_result: str
    reason: str = ""
    provider_requests: int = 0
    events: tuple[dict[str, Any], ...] = ()


_RUNNER_TYPES = ("fake",)


class Phase0Controller:
    """Single-attempt controller with separated policy/execution/acceptance/accounting."""

    def __init__(
        self,
        db: Database,
        *,
        registry: ModelRegistry,
        policy_path: str | Path,
        clock: Callable[[], datetime] = _utc_now,
        worker_id: str = "phase0-controller",
        lease_seconds: int = 600,
        allowed_tools: Iterable[str] = ("read_file", "write_file", "run_tests"),
    ):
        ensure_schema(db)
        self.db = db
        self.registry = registry
        self.policy_path = Path(policy_path)
        self.clock = clock
        self.worker_id = worker_id
        self.ledger = BudgetLedger(db, clock=clock)
        self.gate = ResearchGate(db, clock=clock)
        self.boundary = RuntimeBoundary(allowed_tools=allowed_tools)
        self.queue = JobQueue(db, clock=clock, lease_seconds=lease_seconds)
        self._events: list[dict[str, Any]] = []
        self._active_policy_version: str | None = None

    def events(self, task_id: str | None = None) -> tuple[dict[str, Any], ...]:
        if task_id is None:
            return tuple(self._events)
        return tuple(event for event in self._events if event["task_id"] == task_id)

    def register_research(
        self,
        task_id: str,
        decision_id: str,
        evidence: Mapping[str, Any],
        *,
        required_version: str,
        fresh_after: datetime | None = None,
    ) -> str:
        verdict, reason = validate_evidence(
            evidence, required_version=required_version, fresh_after=fresh_after
        )
        self.gate.validate(
            task_id,
            decision_id,
            evidence,
            required_version=required_version,
            fresh_after=fresh_after,
        )
        return verdict

    def tool_call(self, task: TaskContract, tool_name: str, *, attempt_id: str | None = None) -> ToolDecision:
        decision = self.boundary.tool_call(tool_name)
        if not decision.allowed:
            self._emit(
                "tool_denied",
                task.task_id,
                attempt_id,
                {"tool": tool_name, "reason": decision.reason},
            )
        return decision

    def write_check(
        self, task: TaskContract, path: str, *, attempt_id: str | None = None
    ) -> WriteDecision:
        decision = self.boundary.write_check(
            path,
            allowed_paths=task.allowed_paths,
            forbidden_paths=task.forbidden_paths,
            task_risk=task.risk,
        )
        if not decision.allowed:
            self._emit(
                "policy_checked",
                task.task_id,
                attempt_id,
                {
                    "result": "denied",
                    "reason": decision.reason,
                    "path": str(path),
                    "task_risk": task.risk,
                    "escalated_risk": decision.escalated_risk,
                },
            )
        return decision

    def run(
        self,
        task: TaskContract,
        *,
        adapter: Callable[[TaskContract, str, Path | None], RunObservation],
        acceptance: Callable[[TaskContract, Path | None], Any] | None = None,
    ) -> TaskOutcome:
        mark = len(self._events)
        self._active_policy_version = None

        try:
            policy = load_policy_file(self.policy_path)
        except PolicyUnreadable as error:
            self._emit(
                "policy_checked",
                task.task_id,
                None,
                {"result": "denied", "reason": f"policy_unreadable: {error}"},
                policy_version=None,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=f"policy_unreadable: {error}",
                provider_requests=0,
            )

        self._emit(
            "task_started",
            task.task_id,
            None,
            {
                "provider": task.provider,
                "model": task.model,
                "effort": task.effort,
                "paid": task.paid,
                "risk": task.risk,
                "workspace": None if task.workspace is None else str(task.workspace),
            },
            policy.policy_version,
        )
        self._active_policy_version = policy.policy_version
        self._emit(
            "policy_checked",
            task.task_id,
            None,
            {"result": "allowed", "policy_version": policy.policy_version},
            policy.policy_version,
        )

        if task.research_required:
            satisfied, verdict = self.gate.satisfied(task.task_id, task.research_decision_id)
            self._emit(
                "research_validated",
                task.task_id,
                None,
                {
                    "decision_id": task.research_decision_id,
                    "satisfied": satisfied,
                    "verdict": verdict,
                },
                policy.policy_version,
            )
            if not satisfied:
                policy_result = (
                    POLICY_RESULT_NEEDS_RESEARCH
                    if verdict == "no_decision"
                    else POLICY_RESULT_NEEDS_REVIEW
                )
                return self._outcome(
                    task,
                    attempt=self.ledger.attempts(task.task_id),
                    mark=mark,
                    status=TASK_BLOCKED,
                    policy_result=policy_result,
                    execution_result=EXECUTION_NOT_STARTED,
                    acceptance_result=ACCEPTANCE_NOT_RUN,
                    accounting_result=ACCOUNTING_NOT_RESERVED,
                    reason=f"research gate unmet: {verdict}",
                    provider_requests=0,
                )

        try:
            resolved = self.registry.resolve(task.provider, task.model, task.effort)
        except ModelUnresolved as error:
            self._emit(
                "model_resolved",
                task.task_id,
                None,
                {
                    "result": "model_unresolved",
                    "provider": task.provider,
                    "model": task.model,
                    "effort": task.effort,
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=str(error),
                provider_requests=0,
            )
        except EffortUnsupported as error:
            self._emit(
                "model_resolved",
                task.task_id,
                None,
                {
                    "result": "effort_unsupported",
                    "provider": task.provider,
                    "model": task.model,
                    "effort": task.effort,
                    "requested_effort": task.effort,
                    "supported_efforts": list(
                        self.registry.supported_efforts(task.provider, task.model)
                    ),
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=str(error),
                provider_requests=0,
            )

        if (resolved.provider, resolved.model) not in policy.allowed_models:
            self._emit(
                "model_resolved",
                task.task_id,
                None,
                {
                    "result": "not_in_allowlist",
                    "provider": resolved.provider,
                    "model": resolved.model,
                    "effort": resolved.effort,
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=f"model {resolved.provider}/{resolved.model} is not in the policy allowlist",
                provider_requests=0,
            )
        if resolved.effort not in policy.allowed_efforts:
            self._emit(
                "model_resolved",
                task.task_id,
                None,
                {
                    "result": "effort_not_allowed",
                    "provider": resolved.provider,
                    "model": resolved.model,
                    "effort": resolved.effort,
                    "requested_effort": task.effort,
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=f"effort {resolved.effort} is not allowed by policy",
                provider_requests=0,
            )

        if task.paid and self.ledger.has_unresolved():
            self._emit(
                "accounting_unresolved",
                task.task_id,
                None,
                {
                    "result": "blocked_new_paid_attempt",
                    "reason": "unreconciled spend blocks new paid attempts",
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason="unreconciled spend blocks new paid attempts",
                provider_requests=0,
            )

        attempt_number = self.ledger.attempts(task.task_id) + 1 if task.paid else 1
        attempt_id = f"{task.task_id}-attempt-{attempt_number}"

        if task.paid:
            cap_micros = to_micros(task.cap)
            if to_micros(task.attempt_budget) > cap_micros:
                raise Phase0Error("attempt budget exceeds task cap")
            self.ledger.ensure_account(task.task_id, cap_micros)

        job = self.queue.enqueue(
            "phase0_attempt",
            objective=f"phase0 attempt for {task.task_id}",
            idempotency_key=f"phase0:{task.task_id}:{attempt_number}",
            project_id=None,
            acceptance_criteria=[],
            policy_snapshot={
                "policy_version": policy.policy_version,
                "registry_version": resolved.registry_version,
                "resolved_model": {
                    "provider": resolved.provider,
                    "model": resolved.model,
                    "effort": resolved.effort,
                    "paid": resolved.paid,
                },
                "attempt_id": attempt_id,
                "task": task.snapshot(),
            },
            max_attempts=1,
            priority="normal",
            timeout_seconds=3600,
        )
        claimed = self.queue.claim(self.worker_id, job_type="phase0_attempt")
        if claimed.id != job.id:
            raise Phase0Error(f"unexpected job claimed: {claimed.id} != {job.id}")
        self.queue.start(job.id, claimed.lease_token)
        token = claimed.lease_token
        self._emit(
            "attempt_leased",
            task.task_id,
            attempt_id,
            {"job_id": job.id, "attempt": attempt_number},
            policy.policy_version,
        )

        if task.paid:
            try:
                reservation = self.ledger.reserve(
                    task.task_id, job.id, attempt_number, to_micros(task.attempt_budget)
                )
            except BudgetExceeded as error:
                self._fail_lease(job.id, token, f"budget: {error}")
                self._emit(
                    "budget_reserved",
                    task.task_id,
                    attempt_id,
                    {"result": "denied", "reason": str(error)},
                    policy.policy_version,
                )
                return self._outcome(
                    task,
                    attempt=attempt_number,
                    mark=mark,
                    status=TASK_BLOCKED,
                    policy_result=POLICY_RESULT_ALLOWED,
                    execution_result=EXECUTION_NOT_STARTED,
                    acceptance_result=ACCEPTANCE_NOT_RUN,
                    accounting_result=ACCOUNTING_OVER_CAP,
                    reason=f"budget_exceeded: {error}",
                    provider_requests=0,
                    job_id=job.id,
                )
            self._emit(
                "budget_reserved",
                task.task_id,
                attempt_id,
                {
                    "result": "active",
                    "reservation_id": reservation.reservation_id,
                    "amount_micros": reservation.amount_micros,
                },
                policy.policy_version,
            )

        self._emit(
            "model_resolved",
            task.task_id,
            attempt_id,
            {
                "result": "resolved",
                "provider": resolved.provider,
                "model": resolved.model,
                "effort": resolved.effort,
                "paid": resolved.paid,
                "registry_version": resolved.registry_version,
            },
            policy.policy_version,
        )
        self._emit(
            "process_spawned",
            task.task_id,
            attempt_id,
            {"runner": "fake", "attempt_id": attempt_id},
            policy.policy_version,
        )

        try:
            observation = adapter(task, attempt_id, task.workspace)
        except Exception as error:
            self._fail_lease(job.id, token, f"runner exception: {type(error).__name__}")
            self._emit(
                "side_effects_inspected",
                task.task_id,
                attempt_id,
                {"side_effect_state": "unknown"},
                policy.policy_version,
            )
            self._emit(
                "process_exited",
                task.task_id,
                attempt_id,
                {"result": "unknown", "error": str(error)},
                policy.policy_version,
            )
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=RunObservation(),
                mark=mark,
                status=TASK_BLOCKED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_UNKNOWN,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason=f"runner_exception: {type(error).__name__}",
                provider_requests=0,
            )

        self._emit(
            "runtime_model_observed",
            task.task_id,
            attempt_id,
            {
                "model": observation.observed_model,
                "effort": observation.observed_effort,
            },
            policy.policy_version,
        )

        if observation.observed_model and observation.observed_model != f"{resolved.provider}/{resolved.model}":
            self._fail_lease(job.id, token, "runtime model mismatch")
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_UNKNOWN,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason="runtime_model_mismatch",
                provider_requests=observation.provider_requests,
            )
        if observation.observed_effort and observation.observed_effort != resolved.effort:
            self._fail_lease(job.id, token, "runtime effort mismatch")
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_UNKNOWN,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason="runtime_effort_mismatch",
                provider_requests=observation.provider_requests,
            )

        if observation.lost:
            self._emit(
                "reconcile_started",
                task.task_id,
                attempt_id,
                {"reason": "runner lost before completion"},
                policy.policy_version,
            )
            self._fail_lease(job.id, token, "runner lost before completion")
            self._emit(
                "side_effects_inspected",
                task.task_id,
                attempt_id,
                {"side_effect_state": observation.side_effect_state},
                policy.policy_version,
            )
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_LOST,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason="attempt_lost",
                provider_requests=observation.provider_requests,
            )

        if observation.timed_out:
            self._emit(
                "side_effects_inspected",
                task.task_id,
                attempt_id,
                {"side_effect_state": observation.side_effect_state},
                policy.policy_version,
            )
            self._fail_lease(job.id, token, "runner hard timeout")
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_TIMEOUT,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason="hard_timeout",
                provider_requests=observation.provider_requests,
            )

        if observation.exit_code is None:
            self._fail_lease(job.id, token, "runner result unknown")
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=EXECUTION_UNKNOWN,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason="runner produced no exit state",
                provider_requests=observation.provider_requests,
            )

        execution_result = (
            EXECUTION_EXIT_0 if observation.exit_code == 0 else EXECUTION_EXIT_NONZERO
        )
        self._emit(
            "process_exited",
            task.task_id,
            attempt_id,
            {"exit_code": observation.exit_code, "claimed_done": observation.claimed_done},
            policy.policy_version,
        )

        violation = scope_error(
            task.allowed_paths, task.forbidden_paths, observation.changed_paths
        )
        self._emit(
            "diff_checked",
            task.task_id,
            attempt_id,
            {
                "changed_paths": list(observation.changed_paths),
                "result": "violation" if violation else "clean",
            },
            policy.policy_version,
        )
        if violation:
            self._record_evidence(
                job.id,
                token,
                {
                    "schema": "arda-phase0-evidence-v0.1",
                    "attempt_id": attempt_id,
                    "kind": "scope-violation",
                    "reason": violation,
                    "changed_paths": list(observation.changed_paths),
                },
            )
            self._fail_lease(job.id, token, f"scope violation: {violation}")
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=execution_result,
                acceptance_result=ACCEPTANCE_FAILED,
                reason=f"scope_violation: {violation}",
                provider_requests=observation.provider_requests,
            )

        if acceptance is None:
            self._fail_lease(job.id, token, "independent acceptance required")
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_BLOCKED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=execution_result,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                reason="independent acceptance required",
                provider_requests=observation.provider_requests,
            )

        try:
            report = acceptance(task, task.workspace)
            passed, checks = _normalize_acceptance(report)
        except Exception as error:
            self._fail_lease(job.id, token, f"acceptance exception: {type(error).__name__}")
            self._emit(
                "acceptance_checked",
                task.task_id,
                attempt_id,
                {"result": "error", "reason": str(error)},
                policy.policy_version,
            )
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_FAILED,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=execution_result,
                acceptance_result=ACCEPTANCE_INCONCLUSIVE,
                reason=f"acceptance_exception: {type(error).__name__}",
                provider_requests=observation.provider_requests,
            )

        self._record_evidence(
            job.id,
            token,
            {
                "schema": "arda-phase0-evidence-v0.1",
                "attempt_id": attempt_id,
                "kind": "acceptance",
                "passed": passed,
                "changed_paths": list(observation.changed_paths),
                "checks": checks,
            },
        )
        self._emit(
            "acceptance_checked",
            task.task_id,
            attempt_id,
            {
                "result": "passed" if passed else "failed",
                "exit_code": observation.exit_code,
                "checks": checks,
            },
            policy.policy_version,
        )

        if execution_result == EXECUTION_EXIT_0 and passed:
            self.queue.complete(
                job.id, token, result={"outcome": TASK_DONE, "attempt_id": attempt_id}
            )
            return self._finish_accounting(
                task,
                attempt_number,
                job.id,
                observation=observation,
                mark=mark,
                status=TASK_DONE,
                policy_result=POLICY_RESULT_ALLOWED,
                execution_result=execution_result,
                acceptance_result=ACCEPTANCE_PASSED,
                reason="",
                provider_requests=observation.provider_requests,
            )

        reason = (
            "acceptance failed"
            if not passed
            else f"runner exit code {observation.exit_code} is not acceptance-grade"
        )
        self._fail_lease(job.id, token, reason)
        return self._finish_accounting(
            task,
            attempt_number,
            job.id,
            observation=observation,
            mark=mark,
            status=TASK_FAILED,
            policy_result=POLICY_RESULT_ALLOWED,
            execution_result=execution_result,
            acceptance_result=ACCEPTANCE_FAILED if not passed else ACCEPTANCE_NOT_RUN,
            reason=reason,
            provider_requests=observation.provider_requests,
        )

    def attempt_fallback(
        self,
        task: TaskContract,
        *,
        target: tuple[str, str, str],
        adapter: Callable[[TaskContract, str, Path | None], RunObservation],
        acceptance: Callable[[TaskContract, Path | None], Any] | None = None,
    ) -> TaskOutcome:
        """P04/P05: fallback only for policy-approved, budget-reservable targets."""
        mark = len(self._events)
        provider, model, effort = target
        try:
            policy = load_policy_file(self.policy_path)
        except PolicyUnreadable as error:
            self._emit(
                "policy_checked",
                task.task_id,
                None,
                {"result": "denied", "reason": f"policy_unreadable: {error}"},
                policy_version=None,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=f"policy_unreadable: {error}",
                provider_requests=0,
            )
        if (
            (provider, model) not in policy.allowed_models
            or effort not in policy.allowed_efforts
        ):
            self._emit(
                "fallback_decision",
                task.task_id,
                None,
                {
                    "result": "denied",
                    "reason": "fallback_not_in_policy_allowlist",
                    "target": {"provider": provider, "model": model, "effort": effort},
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=self.ledger.attempts(task.task_id),
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=(
                    f"fallback {provider}/{model}/{effort} is not approved by policy"
                ),
                provider_requests=0,
            )
        used = self.ledger.attempts(task.task_id)
        if used >= policy.max_attempts_for_task:
            self._emit(
                "fallback_decision",
                task.task_id,
                None,
                {
                    "result": "denied",
                    "reason": "attempt_limit_reached",
                    "target": {"provider": provider, "model": model, "effort": effort},
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=used,
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason="attempt limit reached",
                provider_requests=0,
            )
        try:
            self.registry.resolve(provider, model, effort)
        except (ModelUnresolved, EffortUnsupported) as error:
            self._emit(
                "fallback_decision",
                task.task_id,
                None,
                {
                    "result": "denied",
                    "reason": "fallback_model_unresolved" if isinstance(error, ModelUnresolved)
                    else "fallback_effort_unsupported",
                    "target": {"provider": provider, "model": model, "effort": effort},
                },
                policy.policy_version,
            )
            return self._outcome(
                task,
                attempt=used,
                mark=mark,
                status=TASK_DENIED,
                policy_result=POLICY_RESULT_DENIED,
                execution_result=EXECUTION_NOT_STARTED,
                acceptance_result=ACCEPTANCE_NOT_RUN,
                accounting_result=ACCOUNTING_NOT_RESERVED,
                reason=str(error),
                provider_requests=0,
            )
        self._emit(
            "fallback_decision",
            task.task_id,
            None,
            {
                "result": "allowed",
                "target": {"provider": provider, "model": model, "effort": effort},
            },
            policy.policy_version,
        )
        return self.run(
            task.with_model(provider, model, effort), adapter=adapter, acceptance=acceptance
        )

    def _fail_lease(self, job_id: str, token: str, error: str) -> None:
        try:
            self.queue.fail(job_id, token, error=error, retryable=False)
        except StaleLeaseError:
            self.queue.reconcile_expired()

    def _record_evidence(self, job_id: str, token: str, payload: Mapping[str, Any]) -> str:
        content = _json_dump(payload).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        attempt_id = payload.get("attempt_id", "unknown")
        return self.queue.record_artifact(
            job_id,
            kind="phase0-evidence",
            path_or_ref=f"phase0/{payload.get('kind', 'event')}/{attempt_id}.json",
            expected_sha256=digest,
            content=content,
            lease_token=token,
        )

    def _finish_accounting(
        self,
        task: TaskContract,
        attempt: int,
        job_id: str,
        *,
        observation: RunObservation,
        mark: int,
        status: str,
        policy_result: str,
        execution_result: str,
        acceptance_result: str,
        reason: str,
        provider_requests: int,
    ) -> TaskOutcome:
        if task.paid:
            if observation.usage is None:
                reservation = self.ledger.reconcile(job_id, attempt, usage_micros=None)
                self._emit(
                    "accounting_unresolved",
                    task.task_id,
                    f"{task.task_id}-attempt-{attempt}",
                    {
                        "status": reservation.status,
                        "reservation_id": reservation.reservation_id,
                        "amount_micros": reservation.amount_micros,
                    },
                )
                accounting_result = ACCOUNTING_INCOMPLETE
            else:
                reservation = self.ledger.reconcile(
                    job_id, attempt, usage_micros=to_micros(observation.usage)
                )
                self._emit(
                    "usage_reported",
                    task.task_id,
                    f"{task.task_id}-attempt-{attempt}",
                    {
                        "usage_micros": reservation.reported_usage_micros,
                        "reservation_id": reservation.reservation_id,
                    },
                )
                self._emit(
                    "accounting_reconciled",
                    task.task_id,
                    f"{task.task_id}-attempt-{attempt}",
                    {"account": self.ledger.account(task.task_id)},
                )
                accounting_result = ACCOUNTING_RECONCILED
        else:
            accounting_result = ACCOUNTING_NOT_RESERVED
        self._emit("task_finalized", task.task_id, f"{task.task_id}-attempt-{attempt}", {
            "status": status,
            "reason": reason,
            "accounting_result": accounting_result,
        })
        return self._outcome(
            task,
            attempt=attempt,
            mark=mark,
            status=status,
            policy_result=policy_result,
            execution_result=execution_result,
            acceptance_result=acceptance_result,
            accounting_result=accounting_result,
            reason=reason,
            provider_requests=provider_requests,
            job_id=job_id,
        )

    def _outcome(
        self,
        task: TaskContract,
        *,
        attempt: int,
        mark: int,
        status: str,
        policy_result: str,
        execution_result: str,
        acceptance_result: str,
        accounting_result: str,
        reason: str = "",
        provider_requests: int = 0,
        job_id: str | None = None,
    ) -> TaskOutcome:
        attempt_id = None if attempt < 1 else f"{task.task_id}-attempt-{attempt}"
        return TaskOutcome(
            task_id=task.task_id,
            attempt=attempt,
            attempt_id=attempt_id,
            job_id=job_id,
            status=status,
            policy_result=policy_result,
            execution_result=execution_result,
            acceptance_result=acceptance_result,
            accounting_result=accounting_result,
            reason=reason,
            provider_requests=provider_requests,
            events=tuple(self._events[mark:]),
        )

    def _emit(
        self,
        event_type: str,
        task_id: str,
        attempt_id: str | None,
        payload: Mapping[str, Any],
        policy_version: Any = _UNSET,
    ) -> dict[str, Any]:
        if policy_version is _UNSET:
            policy_version = self._active_policy_version
        record = {
            "event_id": f"event_{uuid.uuid4().hex}",
            "task_id": task_id,
            "attempt_id": attempt_id,
            "type": event_type,
            "policy_version": policy_version,
            "payload": dict(payload),
        }
        self.db.execute(
            "INSERT INTO phase0_events"
            "(event_id, task_id, attempt_id, type, policy_version, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record["event_id"],
                task_id,
                attempt_id,
                event_type,
                policy_version,
                _json_dump(payload),
                _iso(self.clock()),
            ),
        )
        self._events.append(record)
        return record


def _normalize_acceptance(report: Any) -> tuple[bool, tuple[dict[str, Any], ...]]:
    if isinstance(report, tuple) and len(report) == 2:
        passed, checks = report
    else:
        passed = getattr(report, "passed", None)
        if passed is None:
            raise Phase0Error("acceptance report must expose 'passed'")
        checks = getattr(report, "checks", ())
    if not isinstance(passed, bool):
        raise Phase0Error("acceptance report 'passed' must be a boolean")
    normalized = tuple(
        dict(check) for check in checks
    )
    return passed, normalized


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
