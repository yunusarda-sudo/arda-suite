# WP-3 Persistent Computer and Worker Fleet

**Status:** In progress
**Base:** WP-1 draft branch `codex/p0a-lease-fenced-acceptance`
**Goal:** Deliver a scoped, recoverable worker platform for browser, coding, research, and operations work while keeping Core as the authority for leases and accepted results.

## Context and boundaries

The current worker HTTP protocol already provides authenticated HMAC requests, durable request replay checks, lease-fenced state changes, and bounded artifact transport. `WorkerRuntime` is a deterministic local command harness and is not a production coding sandbox. Camofox browser grants are project-scoped and domain-limited; browser tabs are also recorded durably so revoke/expiry cleanup can be retried after a process or service restart.

WP-3 will build on these components. It must not claim that application URL validation controls browser redirects or browser-process egress. Untrusted pages remain data; they do not grant tool, domain, filesystem, Git, or secret permissions. External browser egress isolation and a real authenticated session require deployment-level evidence.

## Milestones

1. **Authorization and identity:** bind worker job claims to registered capabilities; add durable, project-scoped browser session grants with explicit domains, expiry, and revocation.
2. **Browser safety:** enforce delegated domain scope at create, navigate, and observed destination; keep sessions isolated by project and runs; mark page content untrusted; retain bounded artifacts/cleanup behavior.
3. **Worker recovery:** heartbeat active leases, propagate controller cancellation/lease loss, stop the persistent runner cleanly between jobs, and preserve replay/idempotency behavior for uncertain transport outcomes.
4. **Vertical validation:** test Core-to-worker handoff, unauthorized capability/domain rejection, expiry/revocation, restart recovery, and malicious page content handling; document reproducible setup and deployment gates.

## Acceptance and verification

- A worker cannot claim a job outside its registered capabilities.
- Browser sessions require an explicit project id, non-empty delegated domain list, expiry, and active grant; revocation prevents later use.
- Open Camofox tabs are durably associated with their grant. A revoke or expiry blocks browser actions until pending tab closures are retried successfully; close failures remain recorded for recovery.
- Host/local destinations and destinations outside the exact delegated domain scope are rejected. A final observed redirect outside scope is treated as untrusted and blocked from further tool use.
- Page text is conveyed as untrusted evidence, never as policy or authorization.
- Controller cancellation is persisted by Core, returned on worker heartbeat, and forwarded to cancellable executors. The bridge acknowledges cancellation only under the active lease and atomically marks the task cancelled; a candidate returned after cancellation is not recorded or promoted. Acceptance intent creation and Git CAS both reject a pending cancellation. If the worker dies before acknowledgement, lease expiry closes the job as cancelled and the next bridge pass repairs the task projection instead of redispatching it. Queued work cancels immediately. Lease loss/cancellation is checked between research acquisition phases and before publication; bounded in-flight network/model calls can finish before the next check. Protocol retries reuse request ids and do not repeat Core mutations.
- Targeted and full runnable tests pass. Live authenticated browser, external egress enforcement, physical Pi installation, and crash/restart with a real worker are reported separately.

## Decisions and risks

- Project identity is part of browser profile/session identity; a global `jarvis` session is not an acceptable default for persistent work.
- Application allowlists are defense in depth only. Production browser deployment needs an independently enforced outbound network policy, including redirect and DNS-rebinding behavior.
- Unknown external side-effect outcomes are not retried blindly. The recovery record must remain inspectable and require reconciliation where no idempotency receipt exists.
- **Browser engine decision (initial slice):** retain the already integrated Camofox REST adapter and use its per-user persistent profile with a project-derived user identity. Its upstream documents browser profile persistence and per-user storage isolation. We did not add a second browser stack before a worker lifecycle and egress boundary exist. The requested Playwright worker and UI controls remain an outstanding WP-3 acceptance item.
- **Security evidence:** Microsoft's Playwright MCP documents that its allowed-origins filter does not cover redirects and is not a security boundary. Playwright request routing also has a documented Service Worker interception limitation. Neither feature alone satisfies delegated egress policy; the browser worker needs OS/container network enforcement. Sources: [Playwright MCP security/options](https://github.com/microsoft/playwright-mcp#security), [Playwright BrowserContext routing](https://playwright.dev/docs/api/class-browsercontext#browser-context-route), [Camofox session persistence](https://github.com/jo-inc/camofox-browser#session-persistence).
- **Worker runtime boundary:** job claim is filtered by declared worker capabilities and long-running research renews its lease with a fail-closed guard. `WorkerRuntime` remains a deterministic host subprocess test harness; it is not approved as the coding worker and must not receive untrusted source execution.
- **Browser tab lifecycle:** migration 23 stores open/closed Camofox tab receipts. The agent registers a returned tab before using it, records close success/failure at run cleanup, and tries up to eight revoked/expired tabs before each browser action. Any failed or still-pending closure blocks that browser action; the next action retries. Only 2xx or already-closed 404 responses count as a close receipt. This closes managed tabs through the Camofox API, not the browser process itself.
- Current code does not yet provide a Playwright coding/browser worker, browser-process kill path, or recoverable coding candidate handoff. Those remain blockers to calling WP-3 complete.
- Worker protocol receipts and Core mutations now commit atomically in SQLite. A crash before commit rolls back both; a post-commit lost response is served from the durable receipt. This is covered by a simulated failure immediately after claim dispatch, not by killing/restarting a live worker service.
- `bridge_worker` maps SIGTERM/SIGINT to a stop event, uses interruptible polling, and does not start another dispatch/claim cycle after shutdown is requested. An active `run_once` drains through normal lease and acceptance fencing before the runner exits. This does not prove systemd stop behavior or a hard process crash/restart; if a service manager force-kills a still-running task, lease expiry remains the recovery path.
- The Control Center now exposes an authorized `POST /api/jobs/{id}/cancel` action and a per-job cancel button. Queued jobs cancel immediately; leased/running jobs retain their lease and receive cancellation at heartbeat until the owner acknowledges. The caller is recorded as the shared `control-center-operator` role; the current shared-token auth model does not provide per-operator identity.
- Current local cancellation test evidence: Control Center plus worker protocol tests **19 passed**; `make compile` and `git diff --check` passed. This does not prove cancellation over a deployed remote/Tailscale endpoint.
- Current WP-3 branch cancellation regression evidence: `pytest -o addopts='' -q tests/test_execution_bridge.py tests/test_acceptance.py tests/test_core.py` **46 passed**; full Jarvis suite **1372 passed, 5 skipped in 46.81s**; targeted Python compilation and `git diff --check` passed. Coverage includes Core heartbeat propagation, candidate/ref suppression, cancellation checks before acceptance intent and Git CAS, stale lease owner rejection, and restart-style expiry recovery. This remains local SQLite/temp-Git evidence, not a deployed remote worker or physical Pi run.
- Current WP-3 persistent-runner verification: `pytest -o addopts='' -q tests/test_bridge_worker.py tests/test_execution_bridge.py` **12 passed**; full Jarvis suite **1375 passed, 5 skipped in 48.04s**; project `make compile` with the available shared venv and `git diff --check` passed. Signal tests confirm dispatch stops between projects, no next job claim begins after shutdown, and SIGTERM/SIGINT set the worker stop event. No actual systemd service or Pi process was exercised.
- Browser tab lifecycle regression tests cover durable registration, run cleanup receipts, expiry/revocation selection, fail-closed use while cleanup is pending, HTTP error handling, and a close failure followed by successful recovery after reopening Core's SQLite database. Final full Jarvis suite: **1369 passed, 5 skipped**; Python compilation and `git diff --check` passed. This is local Camofox-client contract evidence, not a live browser or process-kill test.
- Current broad Jarvis regression subset: **1314 passed, 1 deselected**. It excludes the two Hermes baseline tests importing absent scripts, `test_brain.py` requiring ignored local config, and the known unrelated memory identity expectation. The unfiltered suite's checkout-level collection failure was observed on WP-2's sibling branch; it must be rerun against the integrated release branch.

## Rollback

WP-3 introduces additive schema and opt-in scoped browser sessions. Rollback consists of disabling worker dispatch/browser configuration and restoring the previous application revision; do not delete durable worker or browser audit rows during rollback.
