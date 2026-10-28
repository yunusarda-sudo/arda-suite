# Arda Core Capability Ontology v0.1

Status: **PROPOSED — CONTRACT ONLY**
Date: 2026-10-02
Depends on: `ARDA_CORE_ADR_001_PROVIDER_CAPABILITY_EVALUATION_MODEL.md`

This document defines the first canonical capability identifiers used by
TaskContract, ProviderDescriptor and future evaluation records. It is not a
provider list and does not grant permission to execute an action.

## 1. Naming rules

Capability IDs use the form:

```text
<domain>.<operation>
```

Rules:

- IDs are lowercase ASCII and stable once published.
- A provider name is never a capability ID.
- A capability describes an observable contract, not a model brand or quality
  claim.
- `required_capabilities` contains canonical IDs only; aliases are resolved at
  intake and are not persisted as truth.
- Breaking semantic changes require a new capability version or ID.
- A capability can require multiple evidence types and Core policy gates.

## 2. Namespace separation

Arda distinguishes three kinds of capability:

```text
provider.*   reasoning or information transformation capability
worker.*     execution-host capability
human.*      human decision/review capability
```

The first v0.1 task-facing capabilities use domain namespaces (`coding.*`,
`research.*`, `verification.*`, `planning.*`) and may be supplied by provider,
worker or human adapters only when their contract explicitly permits it.

Examples:

```text
coding.patch             != worker.execute
verification.audit       != human.review
research.acquire         != network.egress
```

Capability matching never bypasses privacy, approval, sandbox, lease or policy
checks.

## 3. Canonical v0.1 capabilities

### Planning

| ID | Meaning | Minimum evidence |
|---|---|---|
| `planning.decompose` | Turn a typed task into bounded proposed steps | plan hash, task revision hash, step scope |
| `planning.route` | Produce ranked provider candidates for a task | registry snapshot, policy inputs, proposal hash |

Neither capability may create an Episode or execute a step directly.

### Research

| ID | Meaning | Minimum evidence |
|---|---|---|
| `research.acquire` | Acquire bounded sources through an approved adapter | source URI, retrieval time, content hash, provenance |
| `research.extract` | Extract bounded evidence from an acquired source | evidence IDs, source links, extractor provenance |
| `research.synthesize` | Produce claims constrained by an EvidencePack | claim/evidence links, validation result, output hash |

Search snippets are not full-source evidence. Live network access is a policy
and adapter concern, not implied by `research.acquire`.

### Coding

| ID | Meaning | Minimum evidence |
|---|---|---|
| `coding.explore` | Produce bounded repository context | ExplorerRun hash, scope, provenance |
| `coding.patch` | Produce or apply a bounded repository patch candidate | PatchResult, preimage/tree hashes, scope |
| `coding.review` | Review a patch without owning its mutation | review input/output hashes, evaluator provenance |

`coding.patch` does not imply commit, push, deployment or correctness.

### Verification

| ID | Meaning | Minimum evidence |
|---|---|---|
| `verification.structural` | Check patch/tree/diff/hash consistency | StructuralEvaluation |
| `verification.behavioral` | Compare baseline and patched behavior under one TestPlan | BehavioralEvaluation, runtime snapshot |
| `verification.requirement` | Evaluate typed requirements against evidence | RequirementEvaluation, requirement revision hash |
| `verification.alignment` | Compare forward intent with independent reconstruction | AlignmentEvaluation, both intent hashes |

Verification capabilities produce evidence; none of them grants deployment
approval or makes a provider result authoritative.

### Human decision

| ID | Meaning | Minimum evidence |
|---|---|---|
| `human.review` | A human examines a bounded decision/evidence package | approval identity, payload hash, timestamp |
| `human.approve` | A human authorizes one exact policy-bound action | Approval snapshot hash, expiry, consumer record |

Human capabilities are explicit authority boundaries and must not be simulated
by model confidence.

### Execution host

These IDs describe worker/runtime substrate and must not be confused with model
reasoning:

| ID | Meaning | Minimum evidence |
|---|---|---|
| `worker.execute` | Run an authorized argv-only job in a bounded runtime | ExecutionResult, enforcement report, cleanup |
| `worker.sandbox` | Enforce the required filesystem/process/network boundary | capability snapshot, observed enforcement |
| `worker.artifact_export` | Export bounded, hash-verifiable artifacts | artifact hash, size, publication result |

`worker.execute` never means arbitrary shell, network access or deployment.

## 4. Capability descriptor schema

Every canonical capability is described by a versioned descriptor:

```yaml
capability_id: coding.patch
version: 0.1
domain: coding
operation: patch
description: bounded repository mutation producing PatchResult evidence
input_contract: PatchRequest-v0.1
output_contract: PatchResult-v0.1
evidence_required:
  - patch_result
  - preimage_hash
  - result_tree_hash
policy_requirements:
  - repository_scope
  - sandbox_required
  - no_external_side_effects
side_effect_class: repository_workspace_only
```

Descriptors are immutable snapshots when referenced by a proposal or
evaluation. A descriptor edit must produce a new version and snapshot hash.

## 5. Task requirement semantics

TaskContract may require capabilities by ID:

```yaml
required_capabilities:
  - coding.explore
  - coding.patch
  - verification.behavioral
```

The router must distinguish:

```text
required capability       = task cannot proceed without it
preferred capability      = ranking signal only
forbidden capability      = policy/contract exclusion
```

Only required capabilities belong in the authoritative TaskContract. A model’s
inferred preference remains a proposal annotation until promoted through the
requirement revision process.

## 6. Provider and worker mapping

A descriptor maps a provider or worker to capabilities with separate status:

```yaml
provider_id: qwen3-1.7b-pi
capability_id: verification.alignment
status: candidate|validated|suspended|retired
evidence_snapshot_hash: sha256:...
valid_until: 2026-10-09T00:00:00Z
```

Status meanings:

- `candidate`: declared or lightly observed; not sufficient for high-risk route;
- `validated`: accepted evidence for a named workload/contract version;
- `suspended`: existing evidence is not currently routable;
- `retired`: historical only; never selected for new work.

Status is scoped to capability, workload and environment. A provider validated
for `verification.alignment` on Pi is not automatically validated for
`coding.patch` on Mac.

## 7. Anti-inflation rules

The ontology intentionally excludes broad, untestable IDs such as:

```text
smart
best
general_reasoning
autonomous_agent
good_coder
```

If a capability cannot specify an input contract, output contract and minimum
evidence, it remains a research label, not a routable capability.

Quality metrics belong in `ProviderEvaluationRecord`, not in the capability ID.
For example:

```text
coding.patch + first_pass_rate + regression_rate
verification.alignment + abstention_quality + hallucination_rate
```

This avoids creating capabilities such as `coding.patch.high_quality` from an
unreviewed aggregate.

## 8. Capability promotion rules

Promotion from `candidate` to `validated` requires:

1. fixed capability contract version;
2. immutable provider/runtime snapshot;
3. repeated workload evidence;
4. independent evaluator or oracle where applicable;
5. no forbidden-contract violation;
6. resource/privacy constraints recorded;
7. Core policy decision and provenance.

Promotion never changes old evaluation records and never changes an existing
TaskContract revision. A later regression may suspend a mapping without
rewriting its history.

## 9. Initial registry boundary

The first implementation should expose read-only registry views for:

```text
list_capabilities()
get_capability(id, version)
list_providers()
get_provider(id, snapshot)
list_provider_evaluations(provider_id, capability_id)
```

Registry writes, evaluation append and capability promotion belong to Core
authority, not Hermes. Hermes consumes snapshots and emits RouteProposal only.

## 10. Deferred ontology extensions

Do not add these to v0.1 without a separate contract:

- vision capabilities;
- deployment/release capabilities;
- email/browser/social side effects;
- autonomous agent creation;
- cost/energy optimization capabilities;
- learned composite capabilities;
- cross-provider delegation capabilities.

These may be added later as versioned descriptors with explicit policy and
evidence requirements.

## 11. Decision summary

The v0.1 ontology is deliberately small:

```text
planning.*
research.*
coding.*
verification.*
human.*
worker.*
```

It defines what can be requested and measured, not who is trusted. Provider
trust remains an evidence-backed, environment-specific and revocable mapping.
