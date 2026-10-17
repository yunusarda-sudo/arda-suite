# Arda Core M2.4B — ExecutionContract v0.1

Status: v0.1 implementation + physical acceptance complete
Baseline backend: rootless Podman 5.x
Image identity: immutable `name@sha256:<64 hex>` only

## Boundary

`ExecutionRuntime` executes an already-authorized, lease-bound request. It
does not claim a job, mint a lease, approve policy, mutate Core state, or
publish artifacts. The caller remains responsible for Core JobQueue fencing
and verified artifact publication.

## Request

```json
{
  "execution_id": "exec-...",
  "repository_snapshot": "/absolute/read-only/snapshot",
  "argv": ["pytest", "-q"],
  "cwd": "/workspace/source",
  "environment_allowlist": {"PYTHONUNBUFFERED": "1"},
  "mounts": [],
  "network_policy": "none",
  "resource_limits": {
    "pids": 32,
    "cpu_millis": 100,
    "memory_mb": 512,
    "memory_required": false
  },
  "timeout_seconds": 30,
  "output_limits": {"stdout_bytes": 65536, "stderr_bytes": 65536},
  "expected_artifacts": ["test-report.json"]
}
```

`argv` is a non-empty sequence of arguments. No shell string, shell
interpolation, or implicit inherited environment is accepted.

`cwd` is restricted to `/workspace/source` or `/workspace/work`. Source is
read-only; work is the only default writable area. Extra mounts must be
explicit, unique, and below `/workspace`. Artifact paths must be relative,
non-traversing, regular files inside the scratch workspace, and cannot be
symlinks escaping that workspace.

The image reference must include a 64-hex SHA-256 digest. The runtime uses
`--pull=never`; image acquisition is a separate trusted preparation step.

## Capability and enforcement model

The runtime reports a capability snapshot and a separate enforcement report.
Requested limits never imply that a limit was enforced.

```json
{
  "capabilities": {
    "network_isolation": true,
    "read_only_mounts": true,
    "writable_scratch": true,
    "pid_limit": true,
    "cpu_limit": true,
    "memory_limit": false,
    "seccomp": true,
    "user_namespace": true,
    "artifact_export": true
  },
  "requested": {
    "pids": 32,
    "cpu_millis": 100,
    "memory_mb": 512,
    "memory_required": false,
    "network": "none",
    "timeout_seconds": 30
  },
  "enforcement": {
    "pids": "enforced",
    "cpu": "enforced",
    "memory": "unsupported_host",
    "network": "enforced",
    "timeout": "enforced"
  }
}
```

If `memory_required=true` and the host has no memory controller, execution is
rejected before the container starts with `CapabilityMismatchError`. Optional
unsupported limits are reported as `unsupported_host`; they are never called
enforced.

## Result

`ExecutionResult` contains backend/version, capability and enforcement
reports, exit/termination state, bounded stdout/stderr, duration, cleanup
status, and SHA-256 hashes for allowlisted artifacts found in `/workspace/work`.
It does not contain credentials or the host environment.

## Podman baseline policy

The first adapter must use rootless Podman with:

- `--network=none`;
- read-only root filesystem;
- read-only source mount and dedicated writable scratch mount;
- dropped capabilities and `no-new-privileges`;
- explicit PID/CPU/timeout settings where the host supports them;
- no inherited host environment;
- bounded stdout/stderr capture;
- deterministic cleanup with `--rm`.
- immutable image digest and no execution-time image pull.

Memory enforcement is an explicit host capability and is currently
`unsupported_host` on the Pi used by the physical spike.

## Failure-first acceptance

- empty or non-string argv is rejected;
- shell-like command strings are not interpreted;
- missing source, invalid cwd, unsafe mount, and invalid limits are rejected;
- non-`none` network policy is rejected in v0.1;
- required unsupported memory capability rejects before start;
- output limit terminates the process and returns an explicit reason;
- timeout terminates the process and cleans the workspace;
- non-zero exit is a failed execution, not a successful artifact publication;
- secrets are absent unless explicitly allowlisted;
- cleanup is reported separately from command status;
- artifact traversal, symlink escape, workspace escape, and mount collision are rejected;
- orphan child output cannot keep an execution alive past the process-group timeout;
- result publication remains outside the runtime and must be lease/fencing-bound.
