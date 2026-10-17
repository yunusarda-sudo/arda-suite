# WP-3 Persistent Computer and Worker Fleet

**Status:** In progress
**Base:** WP-1 draft branch `codex/p0a-lease-fenced-acceptance`
**Goal:** Deliver a scoped, recoverable worker platform for browser, coding, research, and operations work while keeping Core as the authority for leases and accepted results.

## Context and boundaries

The current worker HTTP protocol already provides authenticated HMAC requests, durable request replay checks, lease-fenced state changes, and bounded artifact transport. `WorkerRuntime` is a deterministic local command harness and is not a production coding sandbox. The existing Camofox integration creates run-owned tabs and closes them after a run, but it has no project domain policy or durable browser session authorization.

WP-3 will build on these components. It must not claim that application URL validation controls browser redirects or browser-process egress. Untrusted pages remain data; they do not grant tool, domain, filesystem, Git, or secret permissions. External browser egress isolation and a real authenticated session require deployment-level evidence.

## Milestones

1. **Authorization and identity:** bind worker job claims to registered capabilities; add durable, project-scoped browser session grants with explicit domains, expiry, and revocation.
2. **Browser safety:** enforce delegated domain scope at create, navigate, and observed destination; keep sessions isolated by project and runs; mark page content untrusted; retain bounded artifacts/cleanup behavior.
3. **Worker recovery:** heartbeat active leases, propagate controller cancellation/lease loss, and preserve replay/idempotency behavior for uncertain transport outcomes.
4. **Vertical validation:** test Core-to-worker handoff, unauthorized capability/domain rejection, expiry/revocation, restart recovery, and malicious page content handling; document reproducible setup and deployment gates.

## Acceptance and verification

- A worker cannot claim a job outside its registered capabilities.
- Browser sessions require an explicit project id, non-empty delegated domain list, expiry, and active grant; revocation prevents later use.
- Host/local destinations and destinations outside the exact delegated domain scope are rejected. A final observed redirect outside scope is treated as untrusted and blocked from further tool use.
- Page text is conveyed as untrusted evidence, never as policy or authorization.
- Controller cancellation is persisted by Core, returned on worker heartbeat, and finalized only by the lease-owning worker's acknowledgement. Queued work cancels immediately. Lease loss/cancellation is checked between research acquisition phases and before publication; bounded in-flight network/model calls can finish before the next check. Protocol retries reuse request ids and do not repeat Core mutations.
- Targeted and full runnable tests pass. Live authenticated browser, external egress enforcement, physical Pi installation, and crash/restart with a real worker are reported separately.

## Decisions and risks

- Project identity is part of browser profile/session identity; a global `jarvis` session is not an acceptable default for persistent work.
- Application allowlists are defense in depth only. Production browser deployment needs an independently enforced outbound network policy, including redirect and DNS-rebinding behavior.
- Unknown external side-effect outcomes are not retried blindly. The recovery record must remain inspectable and require reconciliation where no idempotency receipt exists.
- **Browser engine decision (initial slice):** retain the already integrated Camofox REST adapter and use its per-user persistent profile with a project-derived user identity. Its upstream documents browser profile persistence and per-user storage isolation. We did not add a second browser stack before a worker lifecycle and egress boundary exist. The requested Playwright worker and UI controls remain an outstanding WP-3 acceptance item.
- **Security evidence:** Microsoft's Playwright MCP documents that its allowed-origins filter does not cover redirects and is not a security boundary. Playwright request routing also has a documented Service Worker interception limitation. Neither feature alone satisfies delegated egress policy; the browser worker needs OS/container network enforcement. Sources: [Playwright MCP security/options](https://github.com/microsoft/playwright-mcp#security), [Playwright BrowserContext routing](https://playwright.dev/docs/api/class-browsercontext#browser-context-route), [Camofox session persistence](https://github.com/jo-inc/camofox-browser#session-persistence).
- **Worker runtime boundary:** job claim is filtered by declared worker capabilities and long-running research renews its lease with a fail-closed guard. `WorkerRuntime` remains a deterministic host subprocess test harness; it is not approved as the coding worker and must not receive untrusted source execution.
- Current code does not yet provide a Playwright coding/browser worker, revocation-to-browser-process kill path, or recoverable coding candidate handoff. Those remain blockers to calling WP-3 complete.
- Worker protocol receipts and Core mutations now commit atomically in SQLite. A crash before commit rolls back both; a post-commit lost response is served from the durable receipt. This is covered by a simulated failure immediately after claim dispatch, not by killing/restarting a live worker service.
- The durable `JobQueue.request_cancel` API is currently a Core/controller call, not exposed as a remote operator endpoint. The higher-level Control Center/API remains to wire this action with user authentication and authorization.

## Rollback

WP-3 introduces additive schema and opt-in scoped browser sessions. Rollback consists of disabling worker dispatch/browser configuration and restoring the previous application revision; do not delete durable worker or browser audit rows during rollback.
