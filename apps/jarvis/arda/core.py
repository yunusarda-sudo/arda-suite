"""Arda Core M0: durable state, events, jobs, approvals and policy.

This module deliberately contains no LLM, worker execution, GitHub write,
network guardian, or shell integration.  It is the small reliable kernel those
later layers must use.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


class StaleLeaseError(RuntimeError):
    """A worker tried to use an expired or fenced lease token."""


class ApprovalError(RuntimeError):
    """An approval is missing, expired, consumed, or does not match."""


class PolicyLevel(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    HUMAN_ONLY = "human-only"
    DENY = "deny"


class Database:
    """Small SQLite wrapper with WAL, foreign keys and versioned migrations."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 30000")

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return self.conn.execute(sql, params).fetchone()[0]

    def table_exists(self, name: str) -> bool:
        return bool(self.scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        ))

    def transaction(self, immediate: bool = True):
        return _Transaction(self, immediate)

    def migrate(self) -> list[int]:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        migrations = []
        if 1 not in applied:
            with self.transaction():
                statements = [
                    """CREATE TABLE projects (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        brain_path TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1
                    )""",
                    """CREATE TABLE tasks (
                        id TEXT PRIMARY KEY,
                        project_id TEXT,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'backlog',
                        priority TEXT NOT NULL DEFAULT 'normal',
                        source TEXT,
                        deadline TEXT,
                        blocked_by TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    """CREATE TABLE decisions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT,
                        adr_path TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'proposed',
                        decision_date TEXT,
                        supersedes TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    """CREATE TABLE events (
                        event_id TEXT PRIMARY KEY,
                        event_schema_version INTEGER NOT NULL DEFAULT 1,
                        type TEXT NOT NULL,
                        project_id TEXT,
                        source TEXT NOT NULL,
                        source_id TEXT,
                        observed_at TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        correlation_id TEXT,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE project_metrics (
                        project_id TEXT PRIMARY KEY,
                        last_github_activity_at TEXT,
                        days_inactive INTEGER,
                        open_pr_count INTEGER NOT NULL DEFAULT 0,
                        failed_ci_count INTEGER NOT NULL DEFAULT 0,
                        next_deadline TEXT,
                        last_worker_run_at TEXT,
                        last_sync_at TEXT,
                        calculated_at TEXT NOT NULL,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    """CREATE TABLE jobs (
                        id TEXT PRIMARY KEY,
                        project_id TEXT,
                        type TEXT NOT NULL,
                        objective TEXT NOT NULL,
                        acceptance_criteria TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        priority TEXT NOT NULL DEFAULT 'normal',
                        available_at TEXT NOT NULL,
                        leased_by TEXT,
                        lease_token TEXT,
                        lease_expires_at TEXT,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        timeout_seconds INTEGER NOT NULL DEFAULT 1800,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        context_manifest TEXT NOT NULL DEFAULT '[]',
                        policy_snapshot TEXT NOT NULL DEFAULT '{}',
                        input_hash TEXT,
                        approval_id TEXT,
                        result TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    """CREATE TABLE workers (
                        id TEXT PRIMARY KEY,
                        public_key TEXT NOT NULL,
                        capabilities TEXT NOT NULL DEFAULT '[]',
                        trust_level TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'registered',
                        last_seen_at TEXT,
                        registered_at TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )""",
                    """CREATE TABLE approvals (
                        id TEXT PRIMARY KEY,
                        action_type TEXT NOT NULL,
                        subject_id TEXT NOT NULL,
                        requested_by TEXT NOT NULL,
                        requested_at TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        payload_snapshot TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        expires_at TEXT NOT NULL,
                        approved_by TEXT,
                        approved_at TEXT,
                        executed_at TEXT,
                        execution_id TEXT
                    )""",
                    """CREATE TABLE artifacts (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        path_or_ref TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        verified INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES jobs(id)
                    )""",
                    """CREATE TABLE idempotency_keys (
                        key TEXT PRIMARY KEY,
                        operation TEXT NOT NULL,
                        result TEXT,
                        created_at TEXT NOT NULL
                    )""",
                    "CREATE INDEX jobs_available_idx ON jobs(status, available_at)",
                    "CREATE INDEX events_project_idx ON events(project_id, created_at)",
                    "CREATE INDEX events_correlation_idx ON events(correlation_id)",
                    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN
                        SELECT RAISE(ABORT, 'events are append-only');
                    END""",
                    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN
                        SELECT RAISE(ABORT, 'events are append-only');
                    END""",
                ]
                for statement in statements:
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, _iso(_utc_now())),
                )
            migrations.append(1)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 2 not in applied:
            with self.transaction():
                for statement in (
                    "ALTER TABLE project_metrics ADD COLUMN last_activity_at TEXT",
                    "ALTER TABLE project_metrics ADD COLUMN open_task_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE project_metrics ADD COLUMN blocked_task_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE project_metrics ADD COLUMN awaiting_decision_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE project_metrics ADD COLUMN upcoming_deadline_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE project_metrics ADD COLUMN next_deadline_at TEXT",
                    "ALTER TABLE project_metrics ADD COLUMN source_event_watermark TEXT",
                    "ALTER TABLE project_metrics ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                    """CREATE TABLE schedules (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        interval_seconds INTEGER,
                        local_hour INTEGER,
                        local_minute INTEGER,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        catch_up_policy TEXT NOT NULL DEFAULT 'current_window',
                        last_run_at TEXT,
                        next_run_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1
                    )""",
                    """CREATE TABLE scheduler_ticks (
                        tick_id TEXT PRIMARY KEY,
                        schedule_id TEXT NOT NULL,
                        scheduled_at TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        window_key TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        lease_expires_at TEXT,
                        created_at TEXT NOT NULL,
                        finished_at TEXT,
                        error TEXT,
                        FOREIGN KEY(schedule_id) REFERENCES schedules(id)
                    )""",
                    """CREATE TABLE scheduler_emissions (
                        idempotency_key TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""",
                    "CREATE INDEX schedules_due_idx ON schedules(enabled, next_run_at)",
                    "CREATE INDEX scheduler_ticks_schedule_idx ON scheduler_ticks(schedule_id, created_at)",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, _iso(_utc_now())),
                )
            migrations.append(2)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 3 not in applied:
            with self.transaction():
                self.conn.execute("ALTER TABLE workers ADD COLUMN auth_token_hash TEXT")
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (3, _iso(_utc_now())),
                )
            migrations.append(3)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 4 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE explorer_runs (
                        run_id TEXT PRIMARY KEY,
                        explorer_version TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        strategy_config_hash TEXT NOT NULL,
                        repository_root TEXT NOT NULL,
                        repository_commit TEXT,
                        repository_tree_hash TEXT NOT NULL,
                        task_hash TEXT NOT NULL,
                        selected_files TEXT NOT NULL,
                        selected_regions TEXT NOT NULL,
                        ignored_regions TEXT NOT NULL,
                        context_budget TEXT NOT NULL,
                        context_pack_hash TEXT NOT NULL,
                        provenance TEXT NOT NULL,
                        canonical_result TEXT NOT NULL,
                        canonical_hash TEXT NOT NULL,
                        latency_ms INTEGER NOT NULL,
                        resource_usage TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE explorer_evaluations (
                        evaluation_id TEXT PRIMARY KEY,
                        explorer_run_id TEXT NOT NULL,
                        evaluator_version TEXT NOT NULL,
                        oracle_source TEXT NOT NULL,
                        file_recall_at_k REAL,
                        line_recall REAL,
                        mrr REAL,
                        context_efficiency REAL,
                        irrelevant_context_ratio REAL,
                        downstream_patch_success INTEGER,
                        targeted_tests TEXT NOT NULL,
                        regression_tests TEXT NOT NULL,
                        verification_status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(explorer_run_id) REFERENCES explorer_runs(run_id)
                    )""",
                    "CREATE INDEX explorer_evaluations_run_idx ON explorer_evaluations(explorer_run_id)",
                    """CREATE TRIGGER explorer_runs_no_update BEFORE UPDATE ON explorer_runs BEGIN
                        SELECT RAISE(ABORT, 'explorer runs are immutable');
                    END""",
                    """CREATE TRIGGER explorer_runs_no_delete BEFORE DELETE ON explorer_runs BEGIN
                        SELECT RAISE(ABORT, 'explorer runs are immutable');
                    END""",
                    """CREATE TRIGGER explorer_evaluations_no_update BEFORE UPDATE ON explorer_evaluations BEGIN
                        SELECT RAISE(ABORT, 'explorer evaluations are immutable');
                    END""",
                    """CREATE TRIGGER explorer_evaluations_no_delete BEFORE DELETE ON explorer_evaluations BEGIN
                        SELECT RAISE(ABORT, 'explorer evaluations are immutable');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (4, _iso(_utc_now())),
                )
            migrations.append(4)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 5 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE requirement_contract_revisions (
                        revision_id TEXT PRIMARY KEY,
                        parent_revision_id TEXT,
                        revision_number INTEGER NOT NULL,
                        task_id TEXT NOT NULL,
                        episode_id TEXT NOT NULL,
                        contract_json TEXT NOT NULL,
                        contract_hash TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        provenance_json TEXT NOT NULL,
                        added_requirement_ids TEXT NOT NULL,
                        changed_requirement_ids TEXT NOT NULL,
                        removed_requirement_ids TEXT NOT NULL,
                        canonical_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(task_id, episode_id, revision_number),
                        UNIQUE(revision_id, task_id, episode_id),
                        FOREIGN KEY(parent_revision_id, task_id, episode_id)
                            REFERENCES requirement_contract_revisions(revision_id, task_id, episode_id),
                        CHECK((parent_revision_id IS NULL AND revision_number = 1)
                              OR (parent_revision_id IS NOT NULL AND revision_number > 1))
                    )""",
                    "CREATE INDEX requirement_revisions_task_idx ON requirement_contract_revisions(task_id, episode_id, revision_number)",
                    """CREATE TRIGGER requirement_revisions_no_update
                        BEFORE UPDATE ON requirement_contract_revisions BEGIN
                        SELECT RAISE(ABORT, 'requirement revisions are append-only');
                    END""",
                    """CREATE TRIGGER requirement_revisions_no_delete
                        BEFORE DELETE ON requirement_contract_revisions BEGIN
                        SELECT RAISE(ABORT, 'requirement revisions are append-only');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (5, _iso(_utc_now())),
                )
            migrations.append(5)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 6 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE alignment_evaluations (
                        evaluation_id TEXT PRIMARY KEY,
                        episode_id TEXT NOT NULL,
                        patch_result_id TEXT NOT NULL,
                        task_contract_revision_id TEXT NOT NULL,
                        forward_intent_json TEXT NOT NULL,
                        evaluation_json TEXT NOT NULL,
                        canonical_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""",
                    "CREATE INDEX alignment_evaluations_episode_idx ON alignment_evaluations(episode_id, patch_result_id)",
                    """CREATE TRIGGER alignment_evaluations_no_update
                        BEFORE UPDATE ON alignment_evaluations BEGIN
                        SELECT RAISE(ABORT, 'alignment evaluations are append-only');
                    END""",
                    """CREATE TRIGGER alignment_evaluations_no_delete
                        BEFORE DELETE ON alignment_evaluations BEGIN
                        SELECT RAISE(ABORT, 'alignment evaluations are append-only');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (6, _iso(_utc_now())),
                )
            migrations.append(6)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 7 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE episodes (
                        episode_id TEXT PRIMARY KEY,
                        intake_id TEXT NOT NULL UNIQUE,
                        task_id TEXT NOT NULL,
                        task_contract_revision_id TEXT NOT NULL,
                        task_contract_hash TEXT NOT NULL,
                        intake_payload_json TEXT NOT NULL,
                        intake_payload_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE episode_events (
                        event_id TEXT PRIMARY KEY,
                        episode_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
                    )""",
                    "CREATE INDEX episode_events_episode_idx ON episode_events(episode_id, created_at)",
                    """CREATE TRIGGER episode_events_no_update
                        BEFORE UPDATE ON episode_events BEGIN
                        SELECT RAISE(ABORT, 'episode events are append-only');
                    END""",
                    """CREATE TRIGGER episode_events_no_delete
                        BEFORE DELETE ON episode_events BEGIN
                        SELECT RAISE(ABORT, 'episode events are append-only');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (7, _iso(_utc_now())),
                )
            migrations.append(7)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 8 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE capability_descriptors (
                        capability_id TEXT PRIMARY KEY,
                        version TEXT NOT NULL,
                        canonical_hash TEXT NOT NULL UNIQUE,
                        lifecycle TEXT NOT NULL,
                        descriptor_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE provider_descriptors (
                        provider_id TEXT PRIMARY KEY,
                        canonical_hash TEXT NOT NULL UNIQUE,
                        descriptor_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE provider_evaluations (
                        evaluation_id TEXT PRIMARY KEY,
                        provider_id TEXT NOT NULL,
                        capability_id TEXT NOT NULL,
                        capability_version TEXT NOT NULL,
                        canonical_hash TEXT NOT NULL UNIQUE,
                        evaluation_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(provider_id) REFERENCES provider_descriptors(provider_id),
                        FOREIGN KEY(capability_id) REFERENCES capability_descriptors(capability_id)
                    )""",
                    """CREATE TABLE provider_capability_events (
                        transition_id TEXT PRIMARY KEY,
                        provider_id TEXT NOT NULL,
                        capability_id TEXT NOT NULL,
                        capability_version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        evidence_snapshot_hash TEXT,
                        reason TEXT NOT NULL,
                        transition_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(provider_id) REFERENCES provider_descriptors(provider_id),
                        FOREIGN KEY(capability_id) REFERENCES capability_descriptors(capability_id)
                    )""",
                    "CREATE INDEX provider_evaluations_capability_idx ON provider_evaluations(provider_id, capability_id, created_at)",
                    "CREATE INDEX provider_capability_events_current_idx ON provider_capability_events(provider_id, capability_id, created_at)",
                    """CREATE TRIGGER capability_descriptors_no_update
                        BEFORE UPDATE ON capability_descriptors BEGIN
                        SELECT RAISE(ABORT, 'capability descriptors are append-only');
                    END""",
                    """CREATE TRIGGER capability_descriptors_no_delete
                        BEFORE DELETE ON capability_descriptors BEGIN
                        SELECT RAISE(ABORT, 'capability descriptors are append-only');
                    END""",
                    """CREATE TRIGGER provider_descriptors_no_update
                        BEFORE UPDATE ON provider_descriptors BEGIN
                        SELECT RAISE(ABORT, 'provider descriptors are append-only');
                    END""",
                    """CREATE TRIGGER provider_descriptors_no_delete
                        BEFORE DELETE ON provider_descriptors BEGIN
                        SELECT RAISE(ABORT, 'provider descriptors are append-only');
                    END""",
                    """CREATE TRIGGER provider_evaluations_no_update
                        BEFORE UPDATE ON provider_evaluations BEGIN
                        SELECT RAISE(ABORT, 'provider evaluations are append-only');
                    END""",
                    """CREATE TRIGGER provider_evaluations_no_delete
                        BEFORE DELETE ON provider_evaluations BEGIN
                        SELECT RAISE(ABORT, 'provider evaluations are append-only');
                    END""",
                    """CREATE TRIGGER provider_capability_events_no_update
                        BEFORE UPDATE ON provider_capability_events BEGIN
                        SELECT RAISE(ABORT, 'provider capability events are append-only');
                    END""",
                    """CREATE TRIGGER provider_capability_events_no_delete
                        BEFORE DELETE ON provider_capability_events BEGIN
                        SELECT RAISE(ABORT, 'provider capability events are append-only');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (8, _iso(_utc_now())),
                )
            migrations.append(8)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 9 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE provider_output_artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        episode_id TEXT NOT NULL,
                        request_id TEXT NOT NULL UNIQUE,
                        provider_id TEXT NOT NULL,
                        model_snapshot TEXT NOT NULL,
                        request_hash TEXT NOT NULL,
                        output_hash TEXT NOT NULL,
                        content BLOB NOT NULL,
                        content_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TRIGGER provider_output_artifacts_no_update
                        BEFORE UPDATE ON provider_output_artifacts BEGIN
                        SELECT RAISE(ABORT, 'provider output artifacts are append-only');
                    END""",
                    """CREATE TRIGGER provider_output_artifacts_no_delete
                        BEFORE DELETE ON provider_output_artifacts BEGIN
                        SELECT RAISE(ABORT, 'provider output artifacts are append-only');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (9, _iso(_utc_now())),
                )
            migrations.append(9)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 10 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE evaluation_records (
                        evaluation_id TEXT PRIMARY KEY,
                        episode_id TEXT NOT NULL,
                        provider_id TEXT NOT NULL,
                        capability_id TEXT NOT NULL,
                        model_snapshot TEXT NOT NULL,
                        route_decision_hash TEXT NOT NULL,
                        provider_result_id TEXT NOT NULL,
                        output_artifact_id TEXT NOT NULL,
                        verification_hash TEXT NOT NULL,
                        outcome_status TEXT NOT NULL,
                        completion_status TEXT NOT NULL,
                        latency_ms INTEGER,
                        token_usage_json TEXT NOT NULL,
                        human_intervention INTEGER NOT NULL,
                        failure_class TEXT,
                        canonical_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TRIGGER evaluation_records_no_update
                        BEFORE UPDATE ON evaluation_records BEGIN
                        SELECT RAISE(ABORT, 'evaluation records are append-only');
                    END""",
                    """CREATE TRIGGER evaluation_records_no_delete
                        BEFORE DELETE ON evaluation_records BEGIN
                        SELECT RAISE(ABORT, 'evaluation records are append-only');
                    END""",
                    "CREATE INDEX evaluation_records_capability_idx ON evaluation_records(provider_id, capability_id, created_at)",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (10, _iso(_utc_now())),
                )
            migrations.append(10)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 11 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE hermes_run_events (
                        event_id TEXT PRIMARY KEY,
                        hermes_run_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    )""",
                    "CREATE INDEX hermes_run_events_run_idx ON hermes_run_events(hermes_run_id, created_at, event_id)",
                    """CREATE TRIGGER hermes_run_events_no_update
                        BEFORE UPDATE ON hermes_run_events BEGIN
                        SELECT RAISE(ABORT, 'Hermes run events are append-only');
                    END""",
                    """CREATE TRIGGER hermes_run_events_no_delete
                        BEFORE DELETE ON hermes_run_events BEGIN
                        SELECT RAISE(ABORT, 'Hermes run events are append-only');
                    END""",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (11, _iso(_utc_now())),
                )
            migrations.append(11)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 12 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE organization_profiles (
                        project_id TEXT PRIMARY KEY,
                        mission TEXT NOT NULL DEFAULT '',
                        accepted_baseline TEXT,
                        policy_json TEXT NOT NULL DEFAULT '{}',
                        budget_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    """CREATE TABLE organization_objectives (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'planned',
                        weight INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    """CREATE TABLE organization_task_meta (
                        task_id TEXT PRIMARY KEY,
                        objective_id TEXT,
                        user_impact INTEGER NOT NULL DEFAULT 0,
                        dependency_status TEXT NOT NULL DEFAULT 'ready',
                        risk TEXT NOT NULL DEFAULT 'low',
                        FOREIGN KEY(task_id) REFERENCES tasks(id),
                        FOREIGN KEY(objective_id) REFERENCES organization_objectives(id)
                    )""",
                    "CREATE INDEX organization_objectives_project_idx ON organization_objectives(project_id, status)",
                    "CREATE INDEX organization_task_meta_objective_idx ON organization_task_meta(objective_id)",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (12, _iso(_utc_now())),
                )
            migrations.append(12)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 13 not in applied:
            with self.transaction():
                for statement in (
                    "ALTER TABLE organization_profiles ADD COLUMN autopilot_enabled INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE organization_profiles ADD COLUMN autopilot_state TEXT NOT NULL DEFAULT 'OFF'",
                    "ALTER TABLE organization_profiles ADD COLUMN last_dispatch_at TEXT",
                    "ALTER TABLE organization_profiles ADD COLUMN last_dispatch_reason TEXT",
                    "ALTER TABLE organization_task_meta ADD COLUMN contract_json TEXT",
                    "CREATE INDEX organization_task_meta_contract_idx ON organization_task_meta(task_id)",
                ):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (13, _iso(_utc_now())),
                )
            migrations.append(13)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 14 not in applied:
            with self.transaction():
                self.conn.execute("ALTER TABLE organization_task_meta ADD COLUMN estimated_cost INTEGER NOT NULL DEFAULT 1")
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                                   (14, _iso(_utc_now())))
            migrations.append(14)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 15 not in applied:
            with self.transaction():
                for statement in (
                    "ALTER TABLE organization_objectives ADD COLUMN activation_policy TEXT NOT NULL DEFAULT 'manual_only'",
                    "ALTER TABLE organization_objectives ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'",
                ):
                    self.conn.execute(statement)
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                                  (15, _iso(_utc_now())))
            migrations.append(15)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 16 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE organization_missions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        objective TEXT NOT NULL,
                        acceptance_metrics_json TEXT NOT NULL,
                        allowed_repositories_json TEXT NOT NULL,
                        allowed_domains_json TEXT NOT NULL,
                        delegated_scopes_json TEXT NOT NULL,
                        allowed_risks_json TEXT NOT NULL,
                        budget_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','cancelled','completed','expired')),
                        expires_at TEXT NOT NULL,
                        pause_reason TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    "CREATE INDEX organization_missions_project_state_idx ON organization_missions(project_id, status, expires_at)",
                    """CREATE TABLE director_signals (
                        id TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        source_key TEXT NOT NULL,
                        signal_type TEXT NOT NULL,
                        source_updated_at TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        fresh_until TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processed','ignored')),
                        created_at TEXT NOT NULL,
                        UNIQUE(mission_id, provider, source_key, payload_sha256),
                        FOREIGN KEY(mission_id) REFERENCES organization_missions(id),
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    "CREATE INDEX director_signals_pending_idx ON director_signals(mission_id, status, observed_at)",
                    """CREATE TABLE director_proposals (
                        id TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        signal_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK(status IN ('proposed','rejected','accepted','queued','verified','failed','deferred')),
                        rationale TEXT NOT NULL,
                        contract_json TEXT,
                        decision_json TEXT NOT NULL DEFAULT '{}',
                        task_id TEXT,
                        job_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        FOREIGN KEY(mission_id) REFERENCES organization_missions(id),
                        FOREIGN KEY(signal_id) REFERENCES director_signals(id),
                        FOREIGN KEY(project_id) REFERENCES projects(id)
                    )""",
                    "CREATE INDEX director_proposals_mission_status_idx ON director_proposals(mission_id, status, created_at)",
                ):
                    self.conn.execute(statement)
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                                  (16, _iso(_utc_now())))
            migrations.append(16)
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        if 17 not in applied:
            with self.transaction():
                for statement in (
                    """CREATE TABLE director_dispatch_reservations (
                        proposal_id TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(proposal_id) REFERENCES director_proposals(id) ON DELETE CASCADE,
                        FOREIGN KEY(mission_id) REFERENCES organization_missions(id)
                    )""",
                    "CREATE INDEX director_dispatch_reservations_mission_idx ON director_dispatch_reservations(mission_id, created_at)",
                ):
                    self.conn.execute(statement)
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                                  (17, _iso(_utc_now())))
            migrations.append(17)
        return migrations


def runtime_database_path() -> Path:
    """Return the canonical durable Arda Core database path."""
    return Path(os.environ.get("ARDA_CORE_DB", "~/.arda/core.sqlite3")).expanduser()


def bootstrap_runtime_database(path: str | Path | None = None) -> Database:
    """Create and migrate the canonical runtime database before app startup.

    This uses the same versioned migrations as tests and worker processes; an
    empty or hand-created SQLite file is never considered runtime-ready.
    """
    database = Database(path or runtime_database_path())
    database.migrate()
    return database


class _Transaction:
    def __init__(self, db: Database, immediate: bool):
        self.db = db
        self.immediate = immediate

    def __enter__(self):
        self.db.conn.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


class EventLog:
    def __init__(self, db: Database, clock: Callable[[], datetime] = _utc_now):
        self.db = db
        self.clock = clock

    def append(
        self,
        event_type: str,
        *,
        project_id: str | None = None,
        source: str = "system",
        source_id: str | None = None,
        payload: Any = None,
        correlation_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> str:
        event_id = f"evt_{uuid.uuid4().hex}"
        now = self.clock()
        self.db.execute(
            """INSERT INTO events(
                event_id, event_schema_version, type, project_id, source, source_id,
                observed_at, payload, correlation_id, created_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, event_type, project_id, source, source_id,
             _iso(observed_at or now), _json(payload if payload is not None else {}),
             correlation_id, _iso(now)),
        )
        return event_id

    def get(self, event_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return row


@dataclass(frozen=True)
class Job:
    id: str
    type: str
    objective: str
    status: str
    lease_token: str | None
    leased_by: str | None
    attempt_count: int


class JobQueue:
    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], datetime] = _utc_now,
        lease_seconds: int = 600,
    ):
        self.db = db
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.events = EventLog(db, clock)

    def enqueue(
        self,
        job_type: str,
        objective: str,
        *,
        idempotency_key: str,
        project_id: str | None = None,
        acceptance_criteria: list[str] | None = None,
        context_manifest: Any = None,
        policy_snapshot: Any = None,
        priority: str = "normal",
        max_attempts: int = 3,
        timeout_seconds: int = 1800,
    ) -> Job:
        now = self.clock()
        job_id = f"job_{uuid.uuid4().hex}"
        criteria = acceptance_criteria or []
        input_hash = _hash_payload({
            "type": job_type,
            "objective": objective,
            "acceptance_criteria": criteria,
            "context_manifest": context_manifest if context_manifest is not None else [],
            "policy_snapshot": policy_snapshot if policy_snapshot is not None else {},
        })
        try:
            self.db.execute(
                """INSERT INTO jobs(
                    id, project_id, type, objective, acceptance_criteria, priority,
                    available_at, max_attempts, timeout_seconds, idempotency_key,
                    context_manifest, policy_snapshot, input_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, project_id, job_type, objective,
                 _json(criteria), priority, _iso(now), max_attempts, timeout_seconds,
                 idempotency_key, _json(context_manifest if context_manifest is not None else []),
                 _json(policy_snapshot if policy_snapshot is not None else {}), input_hash,
                 _iso(now), _iso(now)),
            )
        except sqlite3.IntegrityError:
            existing = self.db.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is None:
                raise
            if existing["input_hash"] != input_hash:
                raise ValueError("idempotency key conflict")
            return self._record(existing)
        return self.get(job_id)

    def reconcile_expired(self) -> int:
        now = self.clock()
        now_text = _iso(now)
        with self.db.transaction():
            leased = self.db.execute(
                """UPDATE jobs SET status='queued', leased_by=NULL,
                    lease_token=NULL, lease_expires_at=NULL, updated_at=?, version=version+1
                    WHERE status='leased' AND lease_expires_at < ?""",
                (now_text, now_text),
            ).rowcount
            running = self.db.execute(
                """UPDATE jobs SET status=CASE WHEN attempt_count < max_attempts
                    THEN 'retry_wait' ELSE 'failed' END,
                    available_at=?, leased_by=NULL, lease_token=NULL,
                    lease_expires_at=NULL, updated_at=?, version=version+1,
                    error=CASE WHEN attempt_count < max_attempts THEN error
                        ELSE 'maximum attempts exceeded after lease expiry' END
                    WHERE status='running' AND lease_expires_at < ?""",
                (now_text, now_text, now_text),
            ).rowcount
            promoted = self.db.execute(
                """UPDATE jobs SET status='queued', updated_at=?, version=version+1
                    WHERE status='retry_wait' AND available_at <= ?""",
                (now_text, now_text),
            ).rowcount
        # Return affected jobs, not SQL statement row-counts: a running job can
        # be transitioned through retry_wait and queued in the same pass.
        return leased + running

    def claim(self, worker_id: str, *, job_type: str | None = None) -> Job:
        self.reconcile_expired()
        now = self.clock()
        with self.db.transaction():
            query = """SELECT * FROM jobs WHERE status='queued' AND available_at <= ?"""
            params: list[Any] = [_iso(now)]
            if job_type is not None:
                query += " AND type = ?"
                params.append(job_type)
            query += """ ORDER BY CASE priority WHEN 'critical' THEN 0
                WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                created_at LIMIT 1"""
            row = self.db.execute(query, tuple(params)).fetchone()
            if row is None:
                raise LookupError("no queued job")
            token = secrets.token_urlsafe(24)
            expires = now + timedelta(seconds=self.lease_seconds)
            self.db.execute(
                """UPDATE jobs SET status='leased', leased_by=?, lease_token=?,
                    lease_expires_at=?, attempt_count=attempt_count+1,
                    updated_at=?, version=version+1 WHERE id=? AND status='queued'""",
                (worker_id, token, _iso(expires), _iso(now), row["id"]),
            )
            self.events.append(
                "job.leased", source="core", source_id=row["id"],
                payload={"worker_id": worker_id},
            )
        return self.get(row["id"])

    def start(self, job_id: str, lease_token: str) -> Job:
        now = self.clock()
        with self.db.transaction():
            row = self._verify_lease(job_id, lease_token, ("leased",))
            self.db.execute(
                "UPDATE jobs SET status='running', started_at=COALESCE(started_at, ?), "
                "updated_at=?, version=version+1 WHERE id=? AND lease_token=?",
                (_iso(now), _iso(now), job_id, lease_token),
            )
            self.events.append("job.started", source="core", source_id=job_id)
        return self.get(job_id)

    def heartbeat(self, job_id: str, lease_token: str) -> Job:
        now = self.clock()
        expires = now + timedelta(seconds=self.lease_seconds)
        with self.db.transaction():
            row = self._verify_lease(job_id, lease_token, ("leased", "running"))
            self.db.execute(
                "UPDATE jobs SET lease_expires_at=?, updated_at=?, version=version+1 "
                "WHERE id=? AND lease_token=?",
                (_iso(expires), _iso(now), job_id, lease_token),
            )
        return self.get(row["id"])

    def record_artifact(
        self,
        job_id: str,
        kind: str,
        path_or_ref: str,
        expected_sha256: str,
        *,
        content: bytes,
        lease_token: str | None = None,
    ) -> str:
        if lease_token is not None:
            self._verify_lease(job_id, lease_token, ("leased", "running"))
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("artifact sha256 mismatch")
        existing = self.db.execute(
            """SELECT id, sha256 FROM artifacts
                WHERE job_id=? AND kind=? AND path_or_ref=?""",
            (job_id, kind, path_or_ref),
        ).fetchone()
        if existing is not None:
            if existing["sha256"] != actual_sha256:
                raise ValueError("artifact identity conflict")
            return existing["id"]
        artifact_id = f"art_{uuid.uuid4().hex}"
        self.db.execute(
            """INSERT INTO artifacts(id, job_id, kind, path_or_ref, sha256, verified, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (artifact_id, job_id, kind, path_or_ref, actual_sha256, _iso(self.clock())),
        )
        return artifact_id

    def describe(self, job_id: str, lease_token: str) -> dict[str, Any]:
        """Return a leased job contract without bypassing lease validation."""
        row = self._verify_lease(job_id, lease_token, ("leased", "running"))
        return {
            "job_id": row["id"],
            "type": row["type"],
            "objective": row["objective"],
            "acceptance_criteria": json.loads(row["acceptance_criteria"]),
            "context_manifest": json.loads(row["context_manifest"]),
            "policy_snapshot": json.loads(row["policy_snapshot"]),
            "input_hash": row["input_hash"],
            "attempt_count": row["attempt_count"],
        }

    def complete(self, job_id: str, lease_token: str, *, result: Any) -> Job:
        now = self.clock()
        with self.db.transaction():
            row = self._verify_lease(job_id, lease_token, ("running",))
            artifacts = self.db.scalar(
                "SELECT COUNT(*) FROM artifacts WHERE job_id = ? AND verified = 1", (job_id,)
            )
            if artifacts == 0:
                raise ValueError("at least one verified artifact is required before completion")
            self.db.execute(
                """UPDATE jobs SET status='succeeded', result=?, finished_at=?, updated_at=?,
                    leased_by=NULL, lease_token=NULL, lease_expires_at=NULL, version=version+1
                    WHERE id=? AND lease_token=?""",
                (_json(result), _iso(now), _iso(now), row["id"], lease_token),
            )
            self.events.append("job.completed", source="core", source_id=job_id,
                              payload={"artifact_count": artifacts})
        return self.get(job_id)

    def fail(self, job_id: str, lease_token: str, *, error: str, retryable: bool = True) -> Job:
        now = self.clock()
        with self.db.transaction():
            row = self._verify_lease(job_id, lease_token, ("leased", "running"))
            retry = retryable and row["attempt_count"] < row["max_attempts"]
            status = "retry_wait" if retry else "failed"
            self.db.execute(
                """UPDATE jobs SET status=?, error=?, available_at=?, finished_at=?, updated_at=?,
                    leased_by=NULL, lease_token=NULL, lease_expires_at=NULL, version=version+1
                    WHERE id=? AND lease_token=?""",
                (status, error, _iso(now), None if retry else _iso(now), _iso(now), row["id"], lease_token),
            )
            self.events.append(
                "job.retry_scheduled" if retry else "job.failed",
                source="core", source_id=job_id,
                payload={"error": error, "retryable": retry},
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def _record(self, row: sqlite3.Row) -> Job:
        return Job(row["id"], row["type"], row["objective"], row["status"],
                   row["lease_token"], row["leased_by"], row["attempt_count"])

    def _verify_lease(self, job_id: str, token: str, statuses: tuple[str, ...]) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or row["status"] not in statuses or row["lease_token"] != token:
            raise StaleLeaseError(f"stale or invalid lease for {job_id}")
        expires = datetime.fromisoformat(row["lease_expires_at"])
        if expires <= self.clock():
            raise StaleLeaseError(f"expired lease for {job_id}")
        return row


class ApprovalService:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = _utc_now):
        self.db = db
        self.clock = clock

    def request(self, action_type: str, subject_id: str, payload: Any, *, requested_by: str,
                expires_in: int = 900):
        now = self.clock()
        approval_id = f"approval_{uuid.uuid4().hex}"
        self.db.execute(
            """INSERT INTO approvals(
                id, action_type, subject_id, requested_by, requested_at,
                payload_hash, payload_snapshot, status, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (approval_id, action_type, subject_id, requested_by, _iso(now),
             _hash_payload(payload), _json(payload), _iso(now + timedelta(seconds=expires_in))),
        )
        return self.get(approval_id)

    def approve(self, approval_id: str, payload: Any, *, approved_by: str = "user"):
        row = self._check_payload(approval_id, payload)
        if row["status"] != "pending":
            raise ApprovalError(f"approval is already {row['status']}")
        now = self.clock()
        if datetime.fromisoformat(row["expires_at"]) <= now:
            self.db.execute("UPDATE approvals SET status='expired' WHERE id=? AND status='pending'", (approval_id,))
            raise ApprovalError("approval expired")
        with self.db.transaction():
            changed = self.db.execute(
                """UPDATE approvals SET status='approved', approved_by=?, approved_at=?
                    WHERE id=? AND status='pending' AND payload_hash=? AND expires_at > ?""",
                (approved_by, _iso(now), approval_id, row["payload_hash"], _iso(now)),
            ).rowcount
            if changed != 1:
                raise ApprovalError("approval was changed concurrently")
        return self.get(approval_id)

    def reject(self, approval_id: str):
        row = self.get(approval_id)
        if row["status"] != "pending":
            raise ApprovalError(f"approval is already {row['status']}")
        self.db.execute("UPDATE approvals SET status='rejected' WHERE id=?", (approval_id,))

    def consume(self, approval_id: str, payload: Any) -> Any:
        row = self._check_payload(approval_id, payload)
        if row["status"] != "approved":
            raise ApprovalError(f"approval is already {row['status']}")
        if datetime.fromisoformat(row["expires_at"]) <= self.clock():
            self.db.execute("UPDATE approvals SET status='expired' WHERE id=? AND status='approved'", (approval_id,))
            raise ApprovalError("approval expired")
        now = self.clock()
        with self.db.transaction():
            changed = self.db.execute(
                """UPDATE approvals SET status='executed', executed_at=?, execution_id=?
                    WHERE id=? AND status='approved' AND payload_hash=? AND expires_at > ?""",
                (_iso(now), f"exec_{uuid.uuid4().hex}", approval_id,
                 row["payload_hash"], _iso(now)),
            ).rowcount
            if changed != 1:
                raise ApprovalError("approval was already consumed or expired")
        return payload

    def get(self, approval_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return row

    def _check_payload(self, approval_id: str, payload: Any) -> sqlite3.Row:
        row = self.get(approval_id)
        if row["payload_hash"] != _hash_payload(payload):
            raise ApprovalError("approval payload hash mismatch")
        return row


class PolicyEngine:
    DEFAULTS = {
        "github.list_prs": PolicyLevel.AUTO,
        "github.list_issues": PolicyLevel.AUTO,
        "github.read_issue": PolicyLevel.AUTO,
        "github.read_ci": PolicyLevel.AUTO,
        "memory.search": PolicyLevel.AUTO,
        "project.read": PolicyLevel.AUTO,
        "worker.prepare_job": PolicyLevel.AUTO,
        "worker.execute_isolated_tests": PolicyLevel.AUTO,
        "memory.write": PolicyLevel.CONFIRM,
        "skill.write": PolicyLevel.CONFIRM,
        "package.install": PolicyLevel.CONFIRM,
        "github.create_issue": PolicyLevel.CONFIRM,
        "github.create_pr": PolicyLevel.CONFIRM,
        "git.push": PolicyLevel.CONFIRM,
        "network.request": PolicyLevel.CONFIRM,
        "secret.request": PolicyLevel.HUMAN_ONLY,
        "github.merge_pr": PolicyLevel.HUMAN_ONLY,
        "production.deploy": PolicyLevel.HUMAN_ONLY,
        "production.db_write": PolicyLevel.HUMAN_ONLY,
        "sudo": PolicyLevel.DENY,
        "policy.modify": PolicyLevel.DENY,
        "private_key.read": PolicyLevel.DENY,
        "secret.read": PolicyLevel.DENY,
        "shell": PolicyLevel.DENY,
    }

    def __init__(self, overrides: dict[str, PolicyLevel] | None = None):
        self._policies = dict(self.DEFAULTS)
        self._policies.update(overrides or {})

    def decide(self, action: str) -> PolicyLevel:
        return self._policies.get(action, PolicyLevel.CONFIRM)
