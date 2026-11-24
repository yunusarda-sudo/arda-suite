# WP-6: Physical Raspberry Pi & Recovery Rehearsal Gate

**Status:** Planned  
**Base:** `main` (`48a1aba`)  
**Goal:** Verify physical device execution, systemd daemon lifecycles, and rehearse database/artifact backup and recovery on the Raspberry Pi hardware before commencing long-running autonomy pilots.

---

## Context and boundaries

WP-1 through WP-5 implemented software-level lease fencing, sandbox execution, autonomous planning, worker fleet coordination, and recovery tooling (`arda.recovery`). However, all verifications were conducted within local dev environments or GitHub Actions Linux virtual runners. 

Hardware-specific constraints (ARM64 memory pressure, Pi systemd user-session persistence, ALSA/PulseAudio audio devices, and real network latency over SSH tunnels) require physical gate verification. This work package focuses strictly on host-level integrity, failure recovery, and daemon reliability. No new agent features or code generation logic will be introduced here.

---

## Milestones

1. **Storage & Recovery Rehearsal (`arda.recovery`):**
   - Execute clean backup creation on the Pi host with stopped-writers validation.
   - Calculate and verify SHA-256 hashes of all materialized artifacts.
   - Perform an out-of-place restore to an isolated test directory and execute SQLite integrity checks (`PRAGMA integrity_check`).
   - Validate rollback safety: ensure active database and artifacts remain uncorrupted during interrupted backup/restore cycles.

2. **Daemon Lifecycle & Process Isolation:**
   - Verify `apps/jarvis` Linux headless daemon and `services/memory` systemd user services under `systemctl --user`.
   - Test daemon survival across SSH session disconnects and reboots.
   - Verify signal handling (`SIGTERM`, `SIGINT`) ensuring active worker leases are gracefully terminated or released without leaving orphaned lock files.

3. **Audio Hardware & Headless Intake:**
   - Exercise headless audio capture via ALSA/PulseAudio with Vosk wake-word recognition.
   - Verify memory footprint and CPU utilization during continuous background listening on Raspberry Pi 4/5 hardware.
   - Confirm fallback behavior when no audio hardware is connected (graceful degradation to CLI/MCP intake).

4. **Secure Transport & Loopback Invariants:**
   - Confirm Shared Memory HTTP MCP and Jarvis Control Center bind strictly to loopback (`127.0.0.1`).
   - Exercise remote MCP client connection via authenticated SSH tunnel (`ssh -L`).
   - Verify rejection of cross-origin or non-bearer requests over the tunnel boundary.

---

## Acceptance and verification

| Check | Acceptance Criteria | Target Status |
| :--- | :--- | :--- |
| `rehearsal-runner` | `scripts/rpi_recovery_rehearsal.sh` automates backup, hash verification, and integrity check | VERIFIED (PASS in sandbox) |
| `pi-recovery-backup` | `python3 -m arda.recovery backup` produces verified manifest and artifact hashes on Pi | PENDING (Host execution) |
| `pi-recovery-restore` | Restore to isolated target matches source SQLite state and passes integrity check | PENDING (Host execution) |
| `pi-systemd-lifecycle` | Services start on boot, cleanly stop on `SIGTERM`, release all DB locks | PENDING |
| `pi-headless-audio` | Vosk engine detects wake-word with CPU usage < 25% on Pi 4/5 idle | PENDING |
| `pi-loopback-boundary` | External ports remain closed; loopback accessible only via authenticated tunnel | PENDING |

---

## Risk, Rollback, and Security

- **Data Safety:** Never execute recovery rehearsals directly against production memory databases or vault stores. All restore operations must target isolated sandbox directories.
- **Service Availability:** Rehearsals must be scheduled during maintenance windows; failover instructions must be documented in `docs/RECOVERY.md`.
- **Secrets & Keys:** No private keys or bearer tokens shall be committed or logged during Pi verification runs.
