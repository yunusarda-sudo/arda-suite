# WP-5 Control Center, Full CI, and Autonomy Pilot

**Status:** In progress
**Base:** WP-3 branch `codex/wp3-worker-fleet`
**Goal:** Make ARDA operations observable and controllable, require full automated checks, and run a measured 72-hour pilot only after upstream runtime gates pass.

## Context and boundaries

ARDA already has a loopback-first Control Center, Core diagnostics, a durable job queue, and Pi smoke scripts. Existing GitHub Actions cover installer safety only. This package must integrate with Core rather than duplicate state. A green CI run is not Pi or long-running autonomy evidence.

## Milestones

1. **Full CI:** run Jarvis and Shared Memory tests in separate, explicit Python environments; compile both packages, check dependency consistency and shell installers, and retain least-privilege pinned Actions.
2. **Operator controls:** expose per-job cancellation with an expected-version check; surface cancellation state; confirm destructive controls in the browser; reject cross-origin mutations and untrusted loopback Host headers.
3. **Operational evidence:** show mission/task/job/worker/evidence state with source provenance, approvals, budget, resource and heartbeat signals; connect WP-2 mission and WP-4 gateway data when their branches land.
4. **Recovery operations:** add documented backup/restore and migration/rollback rehearsal with artifact integrity checks and a stop/incident runbook.
5. **72-hour pilot:** only after WP-1 through WP-4 physical acceptance, record at least 12 independently checkable work items, including 3 self-discovered items and 2 dependent steps; inject provider failure, Pi/worker restart, and network loss; publish a go/no-go scorecard.

## Acceptance and verification

- `make test` runs the full two-project suites using their own venvs and gives a clear error if an environment is missing.
- GitHub Actions runs the same full suites on pull requests and main pushes, plus compile, `pip check`, shell syntax, and installer safeguards.
- Job cancellation is durable, version-checked, visible, and delivered to the lease owner; cross-origin browser mutations are rejected.
- Backup can be restored to an isolated database and artifacts can be hash-verified; documented rollback leaves the active data recoverable.
- The 72-hour pilot starts only after security and device gates pass; each task, proposal, execution, verifier result, accepted hash, cost, recovery, and human intervention has an auditable record.

## Current progress and decisions

- Root `make test` no longer selects system Python implicitly; `make setup-test-env` creates app-specific venvs.
- Full CI workflow added with read-only repository permission, pinned Actions, Python 3.11, Linux audio libraries, full tests, compilation, `pip check`, shell syntax, and installer safeguards.
- Control Center now has a versioned cancel action for queued and active jobs. The UI confirms cancellation, displays pending cancellation, and creates DOM nodes with `textContent` for organization/job labels.
- POST requests with an `Origin` must match the request `Host`; loopback deployments require a loopback client and loopback Host. Non-loopback bind still uses a shared bearer token and must sit behind an authenticated TLS transport for remote use.
- WP-2 and WP-4 are separate open draft PRs and are not included in this branch yet. WP-1 physical sandbox/recovery gates and WP-3 live worker/browser gates remain incomplete. Therefore the pilot has not started and this WP is not complete.
- No external Pi deployment, main merge, or 72-hour run was performed.

## Risk, rollback, and recovery

The control action boundary is job cancellation: queued jobs stop immediately; active jobs retain their lease until the worker receives and acknowledges cancellation. Stale UI versions are rejected. If CI or the Control Center change fails, revert this branch/PR; additive Core schema stays migratable and the existing operator can stop future autopilot dispatch. Do not restore or delete production data as part of code rollback. Pilot rollback requires an independent, tested service-stop and database/artifact restore procedure before the pilot starts.

## Open gates

- Mission-first joined traceability, approval mutations, per-operator identity, worker health alerts, resource/cost scorecard, and authenticated TLS deployment.
- Locked/reproducible dependency inputs and a CI run on GitHub.
- Backup/restore rehearsal and migration rollback check.
- WP-1 to WP-4 actual acceptance, physical Raspberry Pi checks, and the instrumented 72-hour pilot.
