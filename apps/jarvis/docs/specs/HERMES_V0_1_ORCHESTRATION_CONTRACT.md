# Hermes v0.1 Orchestration Contract

Status: **DESIGN BASELINE — NOT IMPLEMENTED**
Date: 2026-10-02
Depends on: `ARDA_CORE_DOMAIN_MODEL_V0_1.md`

Hermes is a coordination and proposal layer above Arda Core. It is not a
second source of truth, policy engine, worker controller or verification
authority.

## 1. Purpose and non-goals

Hermes v0.1 may organize a typed task into a proposed route and bounded steps.
The first version must make provider selection auditable without making
provider selection autonomous or irreversible.

In scope:

- provider and capability registry read models;
- deterministic route proposal generation;
- proposal provenance and expiry;
- Core policy/approval handoff;
- provider request/result envelopes;
- read-only episode/context queries needed for a proposal.

Out of scope:

- autonomous agent creation;
- training a routing model;
- self-modification or self-promotion;
- direct worker execution;
- direct artifact publication;
- direct Episode mutation;
- policy bypass or approval inference;
- treating provider text as a verified outcome.

## 2. Authority boundary

```text
Human Goal
    -> Core TaskIntake / TaskContract
    -> Core Episode
    -> Hermes RouteProposal
    -> Core Policy + optional Approval
    -> Core RouteDecision
    -> Core durable Job / provider adapter
    -> Core evidence + verification
```

The following distinctions are mandatory:

```text
RouteProposal    != RouteDecision
RouteDecision    != Approval
ProviderResult   != VerifiedOutcome
VerifiedOutcome  != Promotion
EpisodeCreated   != ExecutionPermission
```

Hermes can propose. Core decides, authorizes, persists and evaluates.

## 3. Registry read models

### ProviderDescriptor

```yaml
provider_id: codex
display_name: Codex
kind: local|remote|subscription|service
capabilities:
  - coding.patch
  - coding.review
privacy:
  data_residency: cloud
  accepts_private_data: false
  network_required: true
limits:
  max_context_tokens: null
  max_output_tokens: null
  rate_class: unknown
cost:
  unit: unknown
  estimate_available: false
availability:
  status: unknown
  observed_at: null
descriptor_version: provider-descriptor-v0.1
```

Registry data is descriptive evidence, not permission. A provider claiming a
capability does not prove that it may receive a particular TaskContract.

### CapabilityDescriptor

Capabilities are typed strings, for example:

```text
research.acquire
research.synthesize
coding.explore
coding.patch
coding.review
verification.requirement_audit
```

Capability matching is necessary but not sufficient. Privacy, risk, approval,
network and host/runtime constraints are checked by Core policy.

## 4. Task input to Hermes

Hermes receives a read-only, typed task view rather than an unstructured prompt:

```yaml
task_id: task-123
episode_id: episode-123
task_contract_revision_id: revision-2
task_contract_hash: sha256:...
task_type: coding
risk: high
privacy: private
required_capabilities:
  - coding.patch
constraints: []
accepted_requirement_ids: []
unresolved_uncertainties: []
```

The task view is bound to the exact contract revision/hash. Hermes must not
silently reinterpret a later revision as the earlier input.

## 5. RouteProposal

`RouteProposal` is an immutable, expiring suggestion. It contains no authority
to execute:

```yaml
proposal_id: route-proposal-123
episode_id: episode-123
task_contract_revision_id: revision-2
task_contract_hash: sha256:...
candidates:
  - provider_id: codex
    capability_matches:
      - coding.patch
    reasons:
      - required capability match
    constraint_checks:
      privacy: requires_core_policy_check
      risk: high
    rank: 1
  - provider_id: local-qwen
    capability_matches:
      - coding.review
    reasons:
      - private local fallback
    constraint_checks:
      privacy: compatible
      risk: requires_deep_verification
    rank: 2
selection_basis:
  router_version: deterministic-router-v0.1
  registry_snapshot_hash: sha256:...
  task_view_hash: sha256:...
created_at: 2026-10-02T00:00:00Z
expires_at: 2026-10-02T00:10:00Z
```

The proposal must be rejected as stale if the task contract revision, registry
snapshot or policy-relevant input changes before Core evaluates it.

`confidence` is intentionally not an authority field. A model or router may
provide explanatory scores, but Core must not interpret them as approval.

## 6. Core RouteDecision

Only Core may create a `RouteDecision` from a proposal:

```yaml
decision_id: route-decision-123
proposal_id: route-proposal-123
episode_id: episode-123
provider_id: codex
decision: ALLOW|REQUIRE_APPROVAL|DENY|DEFER
policy_snapshot_hash: sha256:...
task_contract_hash: sha256:...
approval_id: null
expires_at: 2026-10-02T00:10:00Z
created_by: CORE_POLICY
```

`ALLOW` means only that this route may proceed under the recorded policy. It
does not mean the provider's eventual output is correct. `REQUIRE_APPROVAL`
must bind approval to the exact route payload and task snapshot. `DENY` and
`DEFER` must not create an executable provider job.

## 7. Provider request/result boundary

Provider adapters receive a bounded request envelope containing the authorized
route and explicitly allowed context. They do not receive Core credentials,
unbounded database access or authority objects.

```yaml
ProviderRequest:
  request_id: provider-request-123
  episode_id: episode-123
  task_contract_revision_id: revision-2
  route_decision_id: route-decision-123
  context_refs: []
  input_snapshot_hash: sha256:...
  output_contract: patch-candidate-v0.1
  timeout_seconds: 120
```

The adapter returns an untrusted `ProviderResult`:

```yaml
ProviderResult:
  request_id: provider-request-123
  provider_id: codex
  status: returned|timeout|unavailable|malformed|rejected
  output_ref: artifact-or-response-reference
  output_hash: sha256:...
  provider_provenance: {}
  received_at: 2026-10-02T00:01:00Z
```

The result must pass the applicable Core contract, scope, artifact, structural,
behavioral, requirement and alignment checks before it can contribute to a
`VerifiedOutcome`.

## 8. Hermes permissions

Hermes MAY:

- read an authorized TaskContract/Episode view;
- read provider/capability registry projections;
- create a RouteProposal;
- create a bounded workflow proposal;
- submit a proposal to Core for policy evaluation;
- submit provider observations through the adapter boundary;
- report uncertainty or inability to route.

Hermes MUST NOT:

- create, update or close an Episode directly;
- append EpisodeEvents directly;
- mutate TaskContract or promote requirements;
- call workers, Podman, shell, GitHub write or external side effects directly;
- consume approvals or mint leases/fencing tokens;
- write artifacts as verified;
- change structural, behavioral, requirement or alignment evaluations;
- convert a provider response into a successful outcome;
- select a cloud provider when Core privacy policy forbids it.

## 9. Failure and stale-input rules

The first implementation must fail closed for:

1. missing or unknown provider;
2. missing required capability;
3. task-contract hash mismatch;
4. expired proposal or decision;
5. registry snapshot mismatch;
6. privacy/policy mismatch;
7. missing approval for a `REQUIRE_APPROVAL` route;
8. duplicate request with conflicting payload hash;
9. malformed provider result;
10. provider result referencing unknown artifacts or evidence;
11. provider timeout or unavailable state;
12. stale Episode or superseded contract revision.

Retry may reuse the same idempotency key only when the canonical request payload
is identical. A retry must never silently become a new route or broaden scope.

## 10. Provenance requirements

Every proposal/decision/provider attempt must preserve:

```text
episode_id
task_contract_revision_id
task_contract_hash
proposal/decision/request identity
registry snapshot hash
policy snapshot hash
provider identity/version when known
input and output hashes
created/received timestamps
```

Secrets, API keys, private credentials and raw authorization envelopes must not
be written to proposals, events, artifacts or diagnostics.

## 11. Deterministic v0.1 router

The first router is a pure policy/ranking function over typed inputs:

```text
TaskContract view
 + ProviderRegistry snapshot
 + CapabilityRegistry snapshot
 + Core policy snapshot
 -> RouteProposal
```

It must not train itself, call an LLM to choose a provider, or change registry
weights based on an unverified provider claim. Evaluation memory may later
provide measured capability evidence, but promotion of a routing rule is a
separate experiment and approval boundary.

## 12. Acceptance criteria for implementation

Hermes v0.1 is not complete until the implementation demonstrates:

- proposal creation without Episode mutation;
- stable canonical proposal hash;
- deterministic candidate ordering for identical snapshots;
- stale contract/proposal rejection;
- Core-only RouteDecision creation;
- policy/approval separation;
- provider result treated as untrusted;
- duplicate identical request replay without duplicate side effect;
- conflicting idempotency payload rejection;
- no provider credential leakage;
- read-only diagnostics showing proposal/decision/provider evidence;
- targeted failure-first tests and isolated full-suite evidence.

No provider integration, Hermes daemon or autonomous routing is implied by this
contract document.
