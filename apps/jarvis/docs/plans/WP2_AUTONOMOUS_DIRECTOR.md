# WP-2: Autonomous Director and mission lifecycle

Status: **IN PROGRESS — implementation and local integration tests underway**

This is the durable implementation plan for WP-2 of the user-provided ARDA Autonomous OS work packages. WP-2 is developed on a branch separate from draft PR #7. It may use test doubles while WP-1's physical security and recovery gate is blocked; real unattended coding stays disabled until that gate passes.

## Purpose and context

ARDA already has project profiles, a free-text mission field, objectives, task metadata, deterministic ready-task ranking, a read-only GitHub metrics sync, scheduler ticks, and idempotent queueing. The missing capability is creating justified work proposals from persistent delegated missions and fresh project signals, then having Core validate and dispatch them without duplicate work.

The Director proposes. Core remains the authority for mission scope, risk, budget, task state, lease, and dispatch. Signal observation is read-only. No proposal may expand the mission's repositories/domains or execute a high-risk action. A proposal with insufficient evidence or acceptance criteria is rejected or left for human review; the system records truthful idle/rejected reasons.

## Planned architecture

1. **Mission registry:** versioned project-scoped mission identity, objectives/metrics, permitted repositories/domains, risk and cost limits, expiration, pause/cancel state, and optimistic concurrency.
2. **Signal adapters:** read-only, injectable observers for GitHub issues/PRs/CI and existing project/deadline signals. Each normalized signal carries provider, project, source identifier, observed time, source update time, payload hash, freshness, and stable deduplication key.
3. **Proposal ledger:** persist observation and proposal lifecycle before dispatch. Unique keys bind mission + source + normalized signal version; replay is idempotent. Unknown external outcome is never blindly replayed.
4. **Director:** bounded proposal generation via an injected model/planner interface. Test doubles exercise policy now; model/provider routing is completed in WP-4. The Director returns a bounded DAG or one task proposal with explicit dependencies, risk, estimates, repository scope, and independently checkable acceptance criteria.
5. **Core validation/dispatch:** validate active mission, fresh signal, repository/domain allowlist, risk, budget, duplicate state, max active jobs, and task contract before creating a Core task/job. Persist decision/reason and link mission, proposal, task, job, and run identifiers.
6. **Recovery and control:** restart resumes undelivered observations/proposals safely; pause/cancel/expiry prevents new dispatch. Minimal read-only status and pause controls precede UI polish.

## Milestones

- [x] Confirm existing schemas and APIs; add migration-compatible mission and proposal records.
- [x] Add mission creation, pause/cancel/complete/expire states, and optimistic version checks while preserving `organization_profiles`.
- [x] Add bounded read-only GitHub CI/issue/PR observation, provenance, source-based freshness, and stable deduplication.
- [x] Add injectable proposal generation and Core-owned scope, risk, cost, active-job, and task-budget validation.
- [x] Connect accepted proposals to the existing idempotent Core queue and reconcile only Core-terminal outcomes; success requires a verified artifact.
- [x] Add local restart/replay, duplicate, stale/future signal, low-score, scope, paused-mission, and scheduled-mission tests.
- [x] Add authenticated Control Center API routes for mission create/list/read/pause, proposal review, and one-shot Director execution.
- [x] Wire a deterministic failing-CI proposer and an opt-in long-running, durable scheduler service; keep unattended execution closed.
- [ ] Evaluate a real read-only GitHub source and real-worker end-to-end flow without enabling unattended execution.
- [ ] Complete full regression review, document setup and rollback, and create one integrated draft PR.

## Acceptance and evidence

- With a saved OPVIA mission and no pre-seeded task/job, a configured failing CI signal is observed, deduplicated, transformed into a verifiable proposal, accepted/rejected by Core policy, and dispatched once through the durable queue.
- A restart after observation, proposal persistence, dispatch, and completion does not lose or duplicate work.
- Expired, paused, out-of-scope, stale, duplicate, over-budget, high-risk, or unverifiable proposals cannot dispatch; every rejection and idle outcome has a durable reason.
- Worker/executor result is independently verified; model-authored claims alone cannot mark success.
- WP-1 implementation/integration/physical Pi gates remain separately reported. Real unattended implementation remains blocked until physical WP-1 acceptance passes.
- Provide exact test commands/results, proposal choice-quality sample, known risks, and migration/rollback steps. This is not production-ready until real source, worker, and Pi evidence pass.

## Security and recovery

- No GitHub write scope is needed for observation. Credentials remain in the trusted host/provider layer and are never copied to worker or sandbox environments.
- Model output is untrusted data. Core validates all IDs, scope, risk, acceptance criteria, resource estimates, and transition preconditions.
- Unique constraints and transactional writes prevent duplicate proposal/task/job creation on signal replay. Dispatch records the stable idempotency key before/with queue creation.
- Pause/cancel/expiry are checked at proposal acceptance and immediately before dispatch. Leases and existing artifact/acceptance fencing remain unchanged.
- Rollback: disable the Director scheduler/dispatch flag; preserve durable audit rows; revert only WP-2 migrations/code after taking a database backup. Never force-reset accepted Git refs.

## Operator setup (local/API)

1. Back up the Core SQLite database before updating; migration 16 adds mission, signal, and proposal tables.
2. Start the Control Plane with `ARDA_DIRECTOR_ENABLED=1`. Set `ARDA_GITHUB_TOKEN` (or `GITHUB_TOKEN`) only for private repositories or higher API limits. The observer exposes GET-only GitHub calls.
3. Create a mission through `POST /api/organizations/{project_id}/missions` with `title`, `objective`, non-empty `acceptance_metrics`, `allowed_repositories`, and timezone-aware `expires_at`. Optional fields are `allowed_domains`, `delegated_scopes`, `allowed_risks`, and bounded `budget`.
4. Use `GET /api/organizations/{project_id}/missions` or `GET /api/missions/{mission_id}` to inspect observations/proposals. Pause with `POST /api/missions/{mission_id}/state` and `{ "status": "paused", "expected_version": N, "reason": "operator pause" }`.
5. The service polls active missions every ten minutes. In this WP-2 delivery, it records and ranks failing-CI proposals, but it does not launch coding workers. Stop the Control Plane or unset `ARDA_DIRECTOR_ENABLED` to disable polling; durable audit records remain.

Local service opt-in is not a Pi deployment or unattended coding authorization. The network observer, scheduler and worker must be validated on the target Pi after WP-1's physical gate.

## Current known gaps

- Existing `GitHubSyncService` stores aggregate open-PR and failed-CI metrics; it does not retain actionable issue/CI signal versions or create proposals.
- `ControlPlane.dispatch_next_task()` selects pre-created ready tasks only.
- Mission state is a free-text project profile with broad policy/budget JSON; there is no dedicated expiring, scoped mission record or proposal ledger.
- The current executor may be configured but must not run new self-discovered work against real repositories until WP-1's physical security/recovery gate passes.
- The current branch has local implementation changes, but no PR is created and no physical Pi or live GitHub credential test has run.
- The Control Plane only starts Director polling when `ARDA_DIRECTOR_ENABLED=1`; it uses optional `ARDA_GITHUB_TOKEN`/`GITHUB_TOKEN` for read-only GitHub requests. Execution dispatch is intentionally not wired into the service.
- The work loop test uses the real SQLite `ExecutionBridge`/Core queue and a fake observer/proposer; it is not evidence of Hermes sandbox or physical Pi execution.

## Evidence from this implementation pass

- Focused integration command covers Director, organization registry, GitHub adapter, Control Center, scheduler, Core migrations, diagnostics, explorer, and capability migration expectations: **77 passed**. `compileall` and `git diff --check` passed.
- Broad regression, excluding the four known-blocked files listed below: **1,251 passed**.
- A live unauthenticated read-only scan of public `pallets/flask` successfully fetched issues, PRs, and workflow runs and persisted four normalized signals to a temporary Core database. The latest bounded sample contained no qualifying fresh CI failure, so it correctly generated no proposal and dispatched nothing.
- A separate deterministic integration test supplied a fresh failing-CI observation and verified exactly one proposal/job through the real SQLite Core queue across a simulated crash/replay, then reconciled success only after Core recorded a verified artifact.
- The full repository suite remains blocked by checkout/environment issues: two Hermes baseline test modules import script files absent from this checkout; five `test_brain.py` cases require local `config.local.yaml`/vault configuration; and an unrelated memory identity assertion fails (`default` vs `example-user`). A broad run excluding those four test files passed. No fix was made to those unrelated conditions.
- Physical Pi, real Hermes worker, and unattended dispatch are `NOT_RUN`; the service does not wire a dispatcher and WP-1 physical acceptance remains outstanding.
