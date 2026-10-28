# Arda Core M2.4C — PatchContract v0.1

Status: contract gate; implementation not started

## Boundary

`PatchWorker` may modify only an isolated writable repository workspace and
only files selected by an immutable `ExplorerRun`. It produces a candidate
patch and evidence. It does not decide whether the patch is correct.

`PatchEvaluation` is an independent record produced by a separate verifier.
The Patch Worker cannot set `verification_status=passed` and cannot publish a
successful Core job on its own.

## PatchRequest

```json
{
  "episode_id": "episode-...",
  "patch_id": "patch-...",
  "explorer_run_id": "run-...",
  "base_tree_hash": "sha256...",
  "allowed_files": [
    {"path": "arda/example.py", "blob_sha256": "sha256..."}
  ],
  "edits": [
    {
      "operation": "replace_exact",
      "path": "arda/example.py",
      "expected_blob_sha256": "sha256...",
      "old_text": "return old\n",
      "new_text": "return new\n",
      "max_replacements": 1
    }
  ],
  "limits": {
    "max_files": 4,
    "max_added_bytes": 32768,
    "max_removed_bytes": 32768,
    "max_total_bytes": 65536
  }
}
```

The request is invalid if the ExplorerRun is missing, the base tree differs,
the path is not selected, the file hash differs, the old text is absent or
matches more than once, or any limit is exceeded.

Paths are normalized relative paths. Absolute paths, `..`, symlink escapes,
directory replacement, special files, and writes outside the workspace are
rejected. Duplicate edit paths are ordered deterministically and must not
reuse a stale preimage hash after an earlier edit.

## PatchResult

```json
{
  "episode_id": "episode-...",
  "patch_id": "patch-...",
  "explorer_run_id": "run-...",
  "status": "applied|rejected|failed",
  "touched_files": ["arda/example.py"],
  "unified_diff": "--- a/arda/example.py\n+++ b/arda/example.py\n...",
  "base_tree_hash": "sha256...",
  "result_tree_hash": "sha256...",
  "file_hashes": {"arda/example.py": "sha256..."},
  "edit_count": 1,
  "rejection_reason": null,
  "worker_version": "patch-worker-v0.1"
}
```

`unified_diff` is generated from the before/after workspace state; it is not
accepted as the sole source of truth for applying the edit. No result is
considered verified merely because `status=applied`.

## PatchEvaluation

The verifier owns fields such as:

```json
{
  "episode_id": "episode-...",
  "patch_id": "patch-...",
  "verification_status": "pending|passed|failed|blocked",
  "targeted_tests": [],
  "regression_tests": [],
  "syntax_status": "not_run|passed|failed",
  "scope_status": "passed|failed",
  "artifact_hashes": {},
  "evaluator_version": "...",
  "failure_class": null
}
```

The verifier may run in the M2.4B Podman runtime later, but that is a
separate handoff. PatchWorker does not run tests in v0.1.

## Failure-first acceptance matrix

```text
missing ExplorerRun / base tree mismatch
unselected file write
absolute path / ../ traversal
symlink file escape / symlink directory escape
missing preimage
ambiguous preimage (>1 match)
blob hash mismatch
duplicate/conflicting edit
max files exceeded
added/removed/total byte budget exceeded
binary or special file
write interruption / atomic rollback
diff generation mismatch
result hash mismatch
duplicate patch_id with same input
same patch_id with conflicting input
worker crash before result artifact
worker result must not claim verification
```

## Acceptance gate

M2.4C v0.1 cannot close until deterministic fixture tests prove every failure
case above, four independent patch fixtures are byte-stable across reruns,
the generated diff and hashes are reproducible, no out-of-scope file changes
survive a rejected operation, and the independent evaluation record remains
separate from `PatchResult`.
