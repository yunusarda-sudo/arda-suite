# WP-7: 72-Hour Instrumented Autonomy Pilot

**Status:** Planned  
**Base:** `main` (Requires WP-6 Physical Raspberry Pi Gate Passed)  
**Goal:** Execute a continuous 72-hour autonomous execution pilot with real work items, comprehensive telemetry, fault injection, and a transparent go/no-go scorecard.

---

## Context and boundaries

WP-5 Milestone 5 defined the requirement for a 72-hour autonomy pilot to establish empirical evidence for multi-day, unattended operations. The pilot tests the interplay between:
- Autonomous Director (task breakdown & prioritization),
- Podman Sandbox Execution (isolated execution & test verification),
- Core Lease Fencing (tamper-proof Git CAS updates),
- Model Routes Gateway (dynamic provider routing, failover, and budget tracking),
- Persistent Shared Memory (lesson extraction and semantic recall).

The pilot operates under strict human-in-the-loop permission policies: any action classified as `CONFIRM` or `DENY` by policy must pause and request approval; only allowlisted, sandboxed, low-risk development items proceed autonomously.

---

## Milestones

1. **Curated Workload Specification:**
   - Define at least 12 checkable work items across real or synthetic sandbox repositories:
     - Minimum 3 self-discovered maintenance or test improvement items (Autonomous Director).
     - Minimum 2 multi-stage dependent tasks (Step Verifier & Execution Bridge).
     - Standard test/lint/refactor items with verifiable deterministic outputs.

2. **Fault Injection & Chaos Scenarios:**
   - **Provider Failure:** Inject artificial rate-limiting (HTTP 429) and upstream timeouts (HTTP 504) to test exponential backoff and alternate provider failover.
   - **Daemon Crash/Reboot:** Trigger abrupt Pi daemon termination mid-task to verify lease recovery, CAS conflict handling, and task resumption.
   - **Network Partition:** Simulate transient network drops during MCP HTTP transport calls.

3. **Continuous Telemetry & Cost Accounting:**
   - Real-time logging of token consumption, latency, and financial budget expenditure per task.
   - Track Core event log entries, verifier pass/fail rates, candidate rejection rates, and lease renewals.
   - Capture memory recall lift and reflexion engine lessons generated across the 72-hour window.

4. **Scorecard & Post-Mortem Analysis:**
   - Aggregate reliability metrics: Mean Time to Failure (MTTF), Mean Time to Recovery (MTTR), and Unassisted Completion Rate.
   - Publish a comprehensive Go/No-Go Scorecard for 24/7 background operation.

---

## Acceptance and verification

| Metric | Target / Threshold | Target Status |
| :--- | :--- | :--- |
| **Autonomous Task Completion** | >= 85% of executable tasks pass verification without manual repair | PENDING |
| **Lease & Fencing Integrity** | 100% — Zero unverified or unauthorized Git ref updates | PENDING |
| **Crash Recovery** | 100% of interrupted tasks recover to clean state or fail safely | PENDING |
| **Budget Enforcement** | 100% compliance — No provider call exceeds hard per-job budget | PENDING |
| **Zero Memory Leak** | RSS memory on daemon remains stable (+/- 10%) across 72 hours | PENDING |

---

## Risk and Abort Conditions

- **Immediate Stop Criteria:**
  - Any attempt to execute outside the designated Podman container.
  - Any unauthorized mutation to the parent Git repository or production database.
  - Cumulative cost exceeding pre-set emergency ceiling.
- **Rollback Procedure:** Instantly kill all active worker processes via Control Center versioned cancel action or `make rpi-stop`, and execute `arda.recovery` rollback to pre-pilot baseline.
