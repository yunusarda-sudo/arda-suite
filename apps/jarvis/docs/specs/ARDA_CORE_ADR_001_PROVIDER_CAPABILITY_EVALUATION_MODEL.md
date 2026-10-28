# ADR-001: Provider Capability and Evaluation Model

Status: **PROPOSED — CONTRACT ONLY**
Date: 2026-10-02
Scope: Hermes v0.1 / Arda Core orchestration

## Context

Hermes must coordinate different kinds of providers: cloud models, local
models, Codex/Claude/Gemini-style adapters, human reviewers, internal services
and workers. A provider name or benchmark claim is not enough to route a task.
Routing must be constrained by the typed TaskContract and informed by measured,
provenance-bound outcomes.

The repository already has related but separate concepts:

- `TaskContract.required_capabilities` describes task requirements;
- worker registrations carry execution capabilities;
- verifier provenance records provider/model identity and snapshots;
- the Pi model bake-off records measured local-model outcomes;
- Hermes v0.1 currently defines registry read models but no implementation.

These concepts must not be collapsed into one mutable “model quality” score.

## Decision

Introduce three distinct logical records:

```text
CapabilityDescriptor
        |
        v
ProviderDescriptor
        |
        v
ProviderEvaluationRecord
```

They have different owners and lifecycles:

| Record | Meaning | Mutable? | Authority |
|---|---|---:|---|
| CapabilityDescriptor | What a capability means and what evidence it requires | versioned | Core contract |
| ProviderDescriptor | What a provider declares and what constraints it has | versioned | Registry authority |
| ProviderEvaluationRecord | What an observed provider run demonstrated | append-only | Core evidence |

No record alone grants execution permission. Core policy evaluates the task,
privacy, risk, approval, host capability and current provider evidence before
creating a RouteDecision.

## CapabilityDescriptor

Capabilities are typed, versioned contract identifiers rather than prose:

```yaml
capability_id: coding.patch
version: 0.1
category: coding
description: produce a bounded repository patch candidate
required_evidence:
  - patch_result
  - structural_evaluation
  - behavioral_evaluation
  - requirement_evaluation
constraints:
  requires_repository_snapshot: true
  requires_sandbox: true
  may_write_external_side_effects: false
```

Initial capability examples:

```text
research.acquire
research.synthesize
coding.explore
coding.patch
coding.review
verification.requirement_audit
human.review
```

Capability matching is a necessary condition for routing, never a sufficient
condition for permission.

## ProviderDescriptor

Providers are broader than model APIs:

```yaml
provider_id: qwen3-1.7b-pi
provider_kind: local_model
identity:
  model_name: qwen3:1.7b
  runtime: ollama
  runtime_version: observed-version
  model_snapshot_hash: sha256:...
capabilities:
  - verification.blind_reconstruction
constraints:
  local_only: true
  network_required: false
  private_data_allowed: true
availability:
  status: observed
  observed_at: 2026-10-02T00:00:00Z
limits:
  max_context_tokens: null
  max_output_tokens: null
cost:
  class: local
  estimate_available: false
```

Provider declarations are claims. A declaration such as `coding.patch` does
not prove that the provider can produce a safe patch. Provider identity must be
bound to the strongest available snapshot (model digest, runtime version,
adapter version or immutable service version). A display name is not identity.

Worker capabilities remain an execution-host concern. A worker may expose
`pytest` or `podman` capability without becoming a reasoning provider, and a
model provider may be able to propose a patch without being allowed to execute
it.

## ProviderEvaluationRecord

Evaluation records are append-only observations tied to one exact workload and
one immutable provider snapshot:

```yaml
evaluation_id: eval-123
provider_id: qwen3-1.7b-pi
provider_snapshot_hash: sha256:...
capability_id: verification.blind_reconstruction
task_type: verification
risk_class: medium
fixture_set_hash: sha256:...
contract_version: d4b-v0.1
runtime_snapshot_hash: sha256:...
episode_id: episode-123
attempts: 1
accepted: true
first_pass: true
correction_count: 0
hallucinated_evidence_count: 0
confidence_violation_count: 0
abstention_classification: useful|neutral|unsafe|unknown
latency_ms: 54800
peak_rss_bytes: 0
swap_bytes: 0
temperature_max_c: null
output_hash: sha256:...
created_at: 2026-10-02T00:00:00Z
evaluator_version: bakeoff-v0.1
```

Unavailable telemetry must be represented as `null`/`unknown`, never as zero.
An aggregate must retain its sample count, fixture-set hash, provider snapshot
hash and evaluator version.

## Evaluation metrics

Metrics are capability-specific. The following are appropriate for the D4B
blind reconstruction workload:

```text
contract_valid_rate
first_pass_rate
valid_correction_rate
hallucinated_evidence_rate
confidence_violation_rate
useful_abstention_rate
timeout_rate
median_latency
p95_latency
peak_rss
swap_pressure
```

For other capabilities, the evidence contract changes. A research provider may
need source provenance and citation coverage; a patch provider needs structural,
behavioral and requirement evidence. No universal score should erase these
dimensions.

### Abstention quality

Abstention is not automatically a failure. It is useful when evidence is
insufficient or the task is outside the provider contract, and unsafe when the
provider fabricates certainty or suppresses a known contradiction. The record
must distinguish these cases.

### Resource evidence

Latency and quality cannot be evaluated without workload identity. Resource
metrics must record the host/runtime/model snapshot where observable. Missing
temperature, power or memory telemetry remains unknown.

## Promotion and routing rules

The first router is deterministic and consumes only versioned descriptors plus
accepted evaluation aggregates. It must not infer capability from provider
prose or a single successful run.

Promotion from candidate to preferred role requires:

1. repeated evaluation on a fixed fixture set;
2. immutable provider and runtime snapshot identity;
3. zero forbidden-contract violations for the accepted capability;
4. explicit resource constraints satisfied;
5. comparison against the current baseline;
6. an auditable promotion decision with policy/approval provenance.

Promotion is role-specific:

```text
qwen3:1.7b  -> fast local verifier candidate
qwen3.5:4b -> deep verifier candidate
```

This does not imply that either model is a general coding provider or that a
candidate may execute side effects.

## Routing input and output

The deterministic router consumes:

```text
TaskContract view
ProviderDescriptor snapshot
CapabilityDescriptor snapshot
accepted ProviderEvaluation aggregates
Core policy snapshot
resource/availability observations
```

It emits a `RouteProposal`, never a `RouteDecision`:

```yaml
proposal_id: proposal-123
episode_id: episode-123
task_contract_revision_id: revision-2
task_contract_hash: sha256:...
candidates:
  - provider_id: codex
    matched_capabilities: [coding.patch]
    disqualifiers: []
    rank: 1
  - provider_id: qwen3-1.7b-pi
    matched_capabilities: [verification.blind_reconstruction]
    disqualifiers: [not_a_patch_provider]
    rank: 2
registry_snapshot_hash: sha256:...
evaluation_snapshot_hash: sha256:...
expires_at: 2026-10-02T00:10:00Z
```

Core then applies privacy, risk, approval and availability policy. A proposal
must be rejected if its contract or registry snapshot is stale.

## Security and truth rules

- Provider results are untrusted until Core validation.
- Evaluation records cannot promote requirements or approve deployment.
- A cloud provider is ineligible when TaskContract privacy or Core policy
  requires local-only processing.
- Provider credentials never enter descriptors, evaluations, events or model
  context.
- Identical evaluation retries reuse idempotency identity; conflicting payloads
  are rejected.
- A model score never overrides a capability mismatch or policy denial.
- Three providers agreeing is not ground truth without structural, behavioral,
  requirement or real-outcome evidence.

## Current status and next implementation boundary

This ADR does not add registry tables or routing code. The next implementation
slice should be:

1. typed `CapabilityDescriptor` and `ProviderDescriptor` validation;
2. canonical hashes and version checks;
3. read-only registry projections;
4. append-only `ProviderEvaluationRecord` contract;
5. deterministic proposal ranking with failure-first tests.

Provider adapters, automatic promotion, evaluation-memory learning and Hermes
daemon behavior remain later slices.

## Consequences

Positive:

- routing becomes evidence-based and role-specific;
- local and cloud providers share one contract without sharing trust;
- bake-off results become reusable evidence rather than informal reports;
- resource and privacy constraints remain visible to Core policy.

Costs:

- registry and evaluation snapshots add persistence/provenance work;
- metrics must be capability-specific;
- promotion is slower than selecting a model by reputation;
- missing telemetry must remain explicitly unknown.
