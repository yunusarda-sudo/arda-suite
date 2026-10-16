# WP-1: Hermes sandbox executor and trusted acceptance

Status: **IMPLEMENTED LOCALLY; LIVE RUNTIME GATE BLOCKED**

This plan follows `ARDA_AUTONOMOUS_OS_WORKPACKAGES.md` supplied by the user. WP-1 is the P0 secure-execution package on the existing draft PR #7 branch. The attachment supplies the master work-package instructions; the current execution is authorized by the user's earlier direct instruction to continue WP-1 from PR #7. No main merge, Pi access, or deployment is authorized here.

## Purpose and boundary

Connect the real Hermes CLI coding turn to Core's existing `ExecutionBridge`, so Hermes sees one ARDA sandbox MCP tool and all code commands/evaluators use a pinned rootless Podman image. Core remains the authority for evidence, lease fencing, candidate acceptance, and CAS. The worker receives an exact Git-tree snapshot and a separate disposable writable clone; it does not receive accepted repository metadata, Core DB, provider environment, or host HOME.

Model orchestration runs in the trusted Hermes process. Provider credentials are forwarded only by an explicit environment-key allowlist. The MCP child receives Podman runtime context plus per-task workspace paths, never provider keys. The container gets no network and no inherited host environment.

## Implemented locally

- `arda.execution.PodmanRuntime` now supports a separately mounted work directory, requires a confirmed rootless capability when requested, checks memory capability, applies per-file `RLIMIT_FSIZE`, monitors aggregate workspace bytes, passes a minimal Podman client environment, and ignores configurable `containers.conf` defaults that could add mounts.
- `arda.hermes_sandbox.SandboxMCPServer` implements a newline-delimited JSON-RPC MCP server with a single `sandbox_exec` tool, pinned source/work paths, command/time/call/output/resource bounds, and hash-only command audit records.
- `HermesPodmanTaskExecutor` implements the bridge executor: it validates the frozen baseline and path/evaluator contract, materializes exact Git blobs without `.git`, creates a disposable local Git repository, launches Hermes with a temporary profile/tool allowlist, requires successful sandbox tool evidence, checks path scope, runs independent commands through the same isolated runtime, rejects evaluator mutation, and creates a trusted candidate commit with the exact baseline parent using an isolated Git index. It never advances an accepted ref.
- `build_executor()` is compatible with `ARDA_BRIDGE_EXECUTOR_FACTORY=arda.hermes_sandbox:build_executor` and requires digest-pinned `ARDA_SANDBOX_IMAGE`, JSON `ARDA_SANDBOX_EVALUATOR_COMMANDS`, and explicit JSON `ARDA_HERMES_PROVIDER_ENV_KEYS`.
- Focused tests cover Podman argv and workspace limits, MCP tool allowlisting and bounds, candidate parent/ref invariants, symlink rejection/validation, and mocked Hermes-to-candidate orchestration.

## Trust flow

```text
ExecutionBridge lease + immutable WorkTask
  -> trusted Hermes CLI (temporary HOME/config; explicit provider env only)
  -> only mcp-arda-sandbox / sandbox_exec
  -> rootless Podman: --network=none, read-only source, writable disposable work
  -> independent evaluator in the same sandbox policy
  -> trusted Git object/index import, candidate parent = frozen baseline
  -> existing Core artifact + AcceptanceCoordinator lease/CAS/reconciliation
```

The Hermes MCP tool configuration uses a single explicit server with an include filter for `sandbox_exec`, and the CLI's `stream-json` events are used to record tool activity. These settings follow the current official [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md) and [CLI reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md). Podman documents `fsize` in bytes; the aggregate workspace cap is separately monitored.

## Fail-closed behavior and limits

- Missing `allowed_paths`, evaluator commands, configured provider credentials, confirmed rootless mode, or memory enforcement returns INVALID; Hermes cannot fall back to host tools.
- Hermes tool failure, absent successful sandbox audit, timeout/output limit, scope violation, evaluator failure or mutation, unsupported baseline tree entry, and cleanup failure cannot produce PASS.
- Worker symlinks are accepted only when relative and contained within the workspace; special files and path escapes are rejected. Git submodules and non-UTF-8 paths are currently unsupported and fail closed.
- `RLIMIT_FSIZE` is a hard per-file cap. Aggregate directory usage is polled and the worker is killed when it exceeds the configured limit; this can overshoot between polling intervals and is not a filesystem quota. A physical Linux quota test or quota-backed work volume remains a required follow-up before claiming a hard aggregate disk cap.
- The Hermes process itself remains a trusted host process with network access for inference. Host-provider API calls are outside the worker sandbox; no authenticated inference proxy is implemented. Provider env-key allowlisting is the current boundary.

## Verification snapshot (2026-10-17)

- `PASS` — `./.venv/bin/python -m pytest -o addopts='' -q`: **1339 passed, 5 skipped in 41.92s**. The ignored project-local `.venv` was built from `requirements-rpi.txt`. The 5 skipped cases are live Ollama tests because this checkout has no ignored `config.local.yaml`/vault configuration.
- `PASS` — previously missing baseline observer/batch scripts are implemented against their existing tests and documentation; migration assertions now match the current 18 migrations, and the memory test expectation now matches the implemented `default` owner without changing the memory namespace.
- `PASS` — `./.venv/bin/python -m py_compile arda/*.py scripts/capture_hermes_baseline.py scripts/run_hermes_baseline_batch.py`.
- `PASS` — `git diff --check`.
- `NOT_RUN` — the system-Python full suite remains unsuitable because required runtime packages are absent; the project `.venv` full-suite result is authoritative for this checkout.
- `BLOCKED` — live Podman/Hermes test: no `podman` or `hermes` CLI is installed; Docker is installed but its daemon socket is absent. No real container, provider call, or sandbox-negative test ran.
- `BLOCKED` — physical Pi test: Tailscale CLI fails to start, and the `rpi` SSH alias is configured but not reachable/verified. No Pi command or deployment ran.
- `NOT_RUN` — full crash/restart acceptance flow with the new executor against live Podman; stale-lease cancellation of an active command; host-secret/network/accepted-`.git` denial on a real Linux host.
- `NOT_RUN` — merge, deployment, unattended coding, WP-2 implementation.

## Review checklist before WP-1 can pass

- [x] Confirm PR #7 branch and preserve the existing P0-A baseline-parent and recovery behavior.
- [x] Implement the production executor/MCP path and local mocked-boundary tests, including a fake Hermes subprocess check for the effective generated allowlist and provider env separation.
- [x] Keep all candidate commits parented to the frozen baseline and avoid moving accepted refs in the executor.
- [ ] Review exact Hermes version behavior against its installed CLI and verify its effective tool list contains only the ARDA MCP tool.
- [ ] Run a real rootless Podman task, real Hermes coding task, host-secret/network/path-denial tests, and live Core acceptance/recovery test.
- [ ] Run the physical Pi security smoke and document resource/quota behavior.
- [ ] Push the complete, reviewed WP-1 delivery to the existing draft PR; do not merge or deploy.

WP-2 self-dispatch remains gated on the physical WP-1 security/recovery acceptance. Existing mission/objective scheduling does not itself establish autonomous work discovery.
