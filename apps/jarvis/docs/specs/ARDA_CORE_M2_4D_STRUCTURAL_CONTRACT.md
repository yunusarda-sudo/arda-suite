# Arda Core M2.4D1 — Structural Verification Contract v0.1

Status: implemented; behavioral verification not started

## VerificationRequest

```json
{
  "episode_id": "episode-...",
  "baseline_workspace": "/isolated/before",
  "patched_workspace": "/isolated/after",
  "patch_result": "immutable PatchResult",
  "allowed_files": ["arda/example.py"],
  "forbidden_paths": [".github/workflows", "pyproject.toml"]
}
```

The verifier owns both workspace references independently of the worker. It
does not trust a worker-provided tree hash without recomputing it.

## StructuralEvaluation

```json
{
  "episode_id": "episode-...",
  "patch_id": "patch-...",
  "status": "STRUCTURAL_PASS|REJECTED|INCONCLUSIVE",
  "checks": {
    "base_tree_hash": true,
    "result_tree_hash": true,
    "touched_files_exact": true,
    "scope_allowed": true,
    "forbidden_paths_unchanged": true,
    "file_hashes_match": true,
    "diff_matches": true
  },
  "failure_class": null,
  "evaluator_version": "structural-verifier-v0.1"
}
```

`STRUCTURAL_PASS` does not mean the patch is correct, tests pass, or the patch
may be promoted. It only means the PatchResult is consistent with the
independent snapshots and declared scope.

## D1 failure-first matrix

```text
patch not APPLIED
episode mismatch
base tree mismatch
result tree mismatch
extra changed file
missing touched file
out-of-scope file
forbidden path changed
postimage hash mismatch
unified diff mismatch
verification claim smuggled into PatchResult
```

## Separation rule

The verifier returns an immutable value object. Persistence, Core job
completion, approval, deployment, and promotion remain outside this D1
component and require later policy-controlled integrations.
