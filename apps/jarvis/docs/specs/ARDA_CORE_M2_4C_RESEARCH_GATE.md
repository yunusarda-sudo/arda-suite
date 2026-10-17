# Arda Core M2.4C — Patch Worker Research Gate

Date: 2026-10-01
Status: `RESEARCH_COMPLETE / CONTRACT_GATE_NEXT`
Scope: deterministic patch generation and verification boundary only

M2.4C does not start with autonomous coding loops, git commits, pushes,
deployment, or self-modification. The first slice is:

```text
ExplorerRun
  → bounded writable workspace
  → preimage-verified edit
  → unified diff artifact
  → hashes/provenance
  → independent verification handoff
```

## Current ecosystem findings

### SWE-agent / SWE-ReX

SWE-agent keeps the agent loop and execution environment separate and can save
a generated patch for later local application. Its documented flow exposes
`apply_patch_locally` as an explicit action and stores the patch as a separate
file. This is a useful boundary pattern, but Arda must not import its agent
loop or let an external framework own Core state.

### mini-SWE-agent

mini-SWE-agent supports action parsing from markdown blocks or tool calls and
can run an iterative edit/test loop. That flexibility is useful for a later
worker, but it is intentionally outside M2.4C v0.1: arbitrary command actions
would bypass the narrow patch contract and Podman policy boundary.

### OpenHands

OpenHands is a general CodeAct-style agent platform whose normal workflow can
combine workspace interaction, command execution, and file edits. It is a
future orchestration reference, not a dependency or authority for the first
Patch Worker. Arda keeps Explorer, patch application, verification, and Core
publication as separate components.

### Aider

Aider documents repository maps and several edit formats, including exact
search/replace blocks and unified diffs. Its unified-diff guidance favors a
familiar, high-level edit representation over brittle line-number-only edits.
Arda adopts the useful part—bounded, explicit edit operations and a generated
unified diff—but adds mandatory ExplorerRun provenance and preimage hashes.

### SWE-bench / SWE-Cycle

SWE-bench evaluates a produced patch in a separate harness and records patch
and test artifacts rather than treating the agent's own claim as proof.
SWE-Cycle explicitly separates environment reconstruction, implementation,
and verification. These support the Arda rule:

```text
PatchWorker result ≠ PatchEvaluation
```

## Decision

Use a deterministic `replace_exact` operation for M2.4C v0.1:

- file path must be in the ExplorerRun selected scope;
- expected file/blob hash must match before editing;
- exact old text must occur exactly once;
- replacement size and total touched files/bytes are bounded;
- writes stay inside the workspace and cannot follow an escaping symlink;
- no git index, commit, branch, remote, or deployment mutation;
- generated unified diff, file hashes, and provenance become artifacts;
- independent verification receives the result but is not performed by the
  Patch Worker.

AST-aware edits, fuzzy matching, whole-file rewrites, autonomous repair loops,
and model-specific formats remain later challengers. They can be benchmarked
against the same contract, but they do not weaken the deterministic baseline.

## Deferred

```text
LLM patch proposal              M2.4C later slice
automatic test/repair loop      M2.4D or later
git commit/push                 later approval-bound integration
PR/deployment                   later milestone
arbitrary shell                 never part of PatchContract v0.1
```

References:

- [SWE-agent local patch application](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/cl_tutorial.md)
- [mini-SWE-agent action configuration](https://github.com/SWE-agent/mini-swe-agent/blob/main/docs/advanced/yaml_configuration.md)
- [Aider edit formats](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/more/edit-formats.md)
- [Aider repository map](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/repomap.md)
- [SWE-bench evaluation](https://github.com/SWE-bench/SWE-bench)
- [SWE-Cycle paper](https://arxiv.org/abs/2605.13139)
