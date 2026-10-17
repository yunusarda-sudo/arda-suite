# WP-2: Autonomous Director and mission lifecycle

Status: **LOCAL DIRECTOR AND CORE QUEUE INTEGRATION IMPLEMENTED; LIVE EXECUTION/Pi ACCEPTANCE OPEN**

This is the durable implementation plan for WP-2 of the user-provided ARDA Autonomous OS work packages. WP-2 is developed on a branch separate from draft PR #7. It may use test doubles while WP-1's physical security and recovery gate is blocked; real unattended coding stays disabled until that gate passes.

## Purpose and context

ARDA already has project profiles, a free-text mission field, objectives, task metadata, deterministic ready-task ranking, a read-only GitHub metrics sync, scheduler ticks, and idempotent queueing. The missing capability is creating justified work proposals from persistent delegated missions and fresh project signals, then having Core validate and dispatch them without duplicate work.

The Director proposes. Core remains the authority for mission scope, risk, budget, task state, lease, and dispatch. Signal observation is read-only. No proposal may expand the mission's repositories/domains or execute a high-risk action. A proposal with insufficient evidence or acceptance criteria is rejected or left for human review; the system records truthful idle/rejected reasons.

## Planned architecture

1. **Mission registry:** versioned project-scoped mission identity, objectives/metrics, permitted repositories/domains, risk and cost limits, expiration, pause/cancel state, and optimistic concurrency.
2. **Signal adapters:** read-only, injectable observers for GitHub issues/PRs/CI and existing project/deadline signals. Each normalized signal carries provider, project, source identifier, observed time, source update time, payload hash, freshness, and stable deduplication key.
3. **Proposal ledger:** persist observation and proposal lifecycle before dispatch. Unique keys bind mission + source + normalized signal version; replay is idempotent. A SQLite transaction reserves mission execution capacity before enqueue; a durable reservation survives a process restart and retries with the Core queue's idempotency key.
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
- [ ] Complete a clean full regression run, document setup and rollback, and create one integrated draft PR.

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
- Pause/cancel/expiry, delegated execution scope, and source-signal freshness are checked atomically when dispatch capacity is reserved. The reservation is the dispatch linearization point; an in-flight reservation completes across a concurrent pause. Leases and existing artifact/acceptance fencing remain unchanged.
- Mission active-job capacity is serialized by SQLite reservations. A crash after reservation retries the same stable Core enqueue; a confirmed no-handoff releases its slot.
- Rollback: disable the Director scheduler/dispatch flag; preserve durable audit rows; migration 17 is additive, so leave its reservation table in place if reverting code. Take a database backup before changing migration state. Never force-reset accepted Git refs.

## Operator setup (local/API)

1. Back up the Core SQLite database before updating; migration 16 adds mission, signal, and proposal tables, and migration 17 adds durable dispatch-slot reservations.
2. Start the Control Plane with `ARDA_DIRECTOR_ENABLED=1`. Set `ARDA_GITHUB_TOKEN` (or `GITHUB_TOKEN`) only for private repositories or higher API limits. The observer exposes GET-only GitHub calls.
3. Create a mission through `POST /api/organizations/{project_id}/missions` with `title`, `objective`, non-empty `acceptance_metrics`, `allowed_repositories`, and timezone-aware `expires_at`. Optional fields are `allowed_domains`, `delegated_scopes`, `allowed_risks`, and bounded `budget`.
4. Use `GET /api/organizations/{project_id}/missions` or `GET /api/missions/{mission_id}` to inspect observations/proposals. Pause with `POST /api/missions/{mission_id}/state` and `{ "status": "paused", "expected_version": N, "reason": "operator pause" }`.
5. The service polls active missions every ten minutes. In this WP-2 delivery, it records and ranks failing-CI proposals, but it does not launch coding workers. Stop the Control Plane or unset `ARDA_DIRECTOR_ENABLED` to disable polling; durable audit records remain.

Local service opt-in is not a Pi deployment or unattended coding authorization. The network observer, scheduler and worker must be validated on the target Pi after WP-1's physical gate.

## Current known gaps

- GitHub issue/PR/CI signal versioning, scoped missions, and durable proposals are implemented. Live observation remains read-only.
- `CoreProposalDispatcher` and the deterministic integration test create one idempotent Core job and reconcile only after a verified artifact. The Control Plane scheduler intentionally does not wire that dispatcher while WP-1's physical security/recovery gate is open; accepted proposals remain pending dispatch.
- The work loop/dispatcher fixture uses real SQLite/Core queues and a fake observer/proposer/worker. It is not evidence of a live Hermes sandbox run.
- `ARDA_DIRECTOR_ENABLED=1` starts read-only observation and proposal generation. No service setting enables execution in this branch, and unattended coding must remain disabled until WP-1 physical acceptance is demonstrated.
- Current public GitHub probe (`pallets/flask`, latest three runs) returned three completed successful runs, so this live sample generated no CI failure proposal. It proves the GET adapter can read a public repository, not proposal quality or worker execution.
- Current full read-only Director probe against `pallets/flask` fetched and persisted 8 normalized issue/PR/workflow signals in a temporary SQLite database. It produced 0 proposals and 0 jobs because the observed sample had no qualifying fresh CI failure; no GitHub writes or worker execution occurred.
- PR #8 remains an open draft with no reported CI checks. Physical Pi, live Hermes, worker restart, and unattended dispatch remain `NOT_RUN`.

## Evidence from this implementation pass

- Focused current integration command: `./.venv/bin/python -m pytest -o addopts='' -q tests/test_core.py tests/test_autonomous_director.py tests/test_control_plane.py tests/test_scheduler.py tests/test_organization.py tests/test_execution_bridge.py tests/test_capabilities.py tests/test_explorer.py tests/test_diagnostics.py`: **77 passed**. `make compile` and `git diff --check` passed.
- `PASS` — regression tests show capacity-1 dispatch is respected; simultaneous SQLite reservations allow one winner; a reserved proposal is recovered after restart and enqueued once; stale source evidence is rejected immediately before a new dispatch.
- Current broad run excluding two test modules that import absent Hermes scripts and local-only `test_brain.py`: **1,269 passed, 1 failed**. The failure is the known unrelated memory identity expectation (`default` vs `example-user`); it is not changed here.
- Current unfiltered `make test` is `BLOCKED` at collection by `ModuleNotFoundError` for `scripts.run_hermes_baseline_batch` and `scripts.capture_hermes_baseline`, absent from this checkout.
- A live unauthenticated read-only scan of public `pallets/flask` successfully fetched issues, PRs, and workflow runs and persisted four normalized signals to a temporary Core database. The latest bounded sample contained no qualifying fresh CI failure, so it correctly generated no proposal and dispatched nothing.
- A separate deterministic integration test supplied a fresh failing-CI observation and verified exactly one proposal/job through the real SQLite Core queue across a simulated crash/replay, then reconciled success only after Core recorded a verified artifact.
- A clean full regression run remains open due to the absent Hermes baseline scripts, local-only brain configuration, and unrelated memory identity assertion. No fix was made to unrelated conditions.
- Physical Pi, real Hermes worker, and unattended dispatch are `NOT_RUN`; the service does not wire a dispatcher and WP-1 physical acceptance remains outstanding.
