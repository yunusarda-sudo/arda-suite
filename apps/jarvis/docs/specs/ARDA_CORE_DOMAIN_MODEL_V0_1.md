# Arda Core Domain Model v0.1

Status: **REVIEWED — DESIGN BASELINE**
Date: 2026-10-02

This document describes the domain boundaries that are implemented or evidenced
in the repository. It does not claim that the full end-to-end autonomous
workflow exists.

## 1. Executive decision

Arda Core uses the following ownership model:

```text
TaskContract / RequirementRevision  = what the task means
Episode                             = one tracked work instance
EpisodeEvent                        = append-only lifecycle evidence
ExecutionRecord                     = one bounded runtime execution
PatchResult                         = one proposed/applied repository mutation
VerificationEvaluation              = evidence about a patch or execution
AlignmentEvaluation                = evidence about intent alignment
Hermes                              = coordination/proposal layer
Core                                = authority, persistence and policy boundary
```

`Episode` is a work-instance identity and lifecycle projection. It is not the
owner of every child object and must not become a god object.

## 2. Entity map

```text
TaskIntakeRequest
        |
        v
TaskContract revision chain
        |
        v
Episode projection + EpisodeEvent ledger
        |
        +--> Explorer / execution / patch records
        +--> structural / behavioral / requirement evaluations
        +--> ForwardIntent / reconstruction / alignment evidence
```

The arrows describe identity/provenance references. They do not imply that all
records currently share one database aggregate or that Core automatically
orchestrates the whole graph.

### TaskIntakeRequest

The validated boundary for a human goal. It owns intake identity, canonical
payload hash, task classification, risk/privacy metadata, constraints and
initial requirement information. It is not an execution permission.

### TaskContract and RequirementRevision

The immutable meaning of a task at a particular revision. The durable revision
store enforces parent continuity, revision numbering, task/episode identity,
canonical hashes and append-only history.

`TaskContract` owns objective, requirements, allowed/forbidden scope,
acceptance criteria, uncertainty and task-level capability/policy metadata.
Provider choice, worker lease and verification result do not belong in the
contract payload.

### Episode

An instance of work created from one intake and one initial contract revision.
It owns:

- `episode_id`, `intake_id` and `task_id` identity;
- the attached contract revision/hash;
- current lifecycle status as a projection;
- creation/update timestamps;
- append-only lifecycle events.

It does not own provider internals, workspace contents, model output, patch
correctness or deployment approval.

### EpisodeEvent

Append-only lifecycle evidence with event type, status snapshot, payload and an
idempotency key. SQLite triggers reject update/delete. Duplicate idempotency
keys are rejected.

Current implementation is **state-first plus event ledger**:

```text
append event + update episodes.status atomically
```

It is not full event sourcing: the current status is stored in `episodes` and
is not reconstructed exclusively by replaying events. That is intentional for
v0.1 and must remain explicit.

### PatchResult

The output of the bounded PatchWorker. It owns mutation evidence: patch status,
touched files, diff, before/after tree hashes and file hashes. It must never
claim behavioral correctness or deployment approval.

### ExecutionRecord / ExecutionResult

Execution contract output from the sandbox runtime. It owns backend identity,
requested versus enforced capabilities, bounded output, exit/termination state,
artifact hashes and cleanup status. The Podman adapter does not own Core lease,
fencing or publication authority.

### Verification evaluations

Structural, behavioral and requirement evaluations are evidence records, not
mutations of the patch or task contract. `UNVERIFIED` must remain distinct from
`SATISFIED`; a green test run is not automatically requirement satisfaction.

### Alignment evidence

Forward intent, blind reconstruction and alignment evaluations connect a patch
to a contract revision and evidence. Alignment is another evidence layer:

```text
ALIGNED != CORRECT
ALIGNED != SAFE
ALIGNED != DEPLOYMENT_APPROVED
```

## 3. Ownership table

| Entity | Owns | Must not own |
|---|---|---|
| TaskIntakeRequest | validated human input and payload identity | execution permission |
| TaskContract | objective, requirements, constraints and acceptance meaning | provider execution details |
| RequirementRevision | immutable contract history and provenance | current worker state |
| Episode | work-instance identity and lifecycle projection | all child records and business logic |
| EpisodeEvent | append-only lifecycle evidence | mutable correction/deletion |
| ExecutionRecord | runtime observation and enforcement report | lease authority or approval |
| PatchResult | bounded repository mutation evidence | correctness verdict |
| StructuralEvaluation | patch/tree/hash consistency | user-intent truth |
| BehavioralEvaluation | baseline-vs-patched test evidence | requirement truth by itself |
| RequirementEvaluation | requirement status and evidence | unverified inference as fact |
| AlignmentEvaluation | forward/backward intent comparison | deployment authorization |
| Hermes | proposals, routing/workflow coordination | direct DB/worker/policy bypass |
| Core | persistence, policy, approval, queue and publication authority | provider-specific reasoning |

## 4. Lifecycle model

The implemented Episode vocabulary is:

```text
OPEN
  -> CLARIFYING
  -> PLANNED
  -> RUNNING
  -> BLOCKED
  -> AWAITING_DECISION
  -> DONE
  -> CANCELLED
```

This diagram is a vocabulary guide, not yet a complete transition policy. The
current store accepts a caller-supplied next status when appending an event;
there is no centralized transition matrix or scheduler-driven recovery state
machine yet. That is a known boundary, not a hidden guarantee.

Execution, verification and alignment have their own result vocabularies and
must not be collapsed into Episode status. An Episode being `DONE` can only be
authoritative when the future orchestration layer has recorded the required
evidence and Core policy permits closure.

## 5. Event and state rules

1. Episode identity is stable for the lifetime of the work instance.
2. Contract clarification creates a new immutable revision; it overwrites no
   previous revision.
3. Episode events are append-only and idempotency-keyed.
4. The `episodes` row is a projection and may change status/timestamp only via
   an authorized Core operation.
5. Read-only Diagnostics may inspect state but must not migrate, reconcile,
   append events or alter projections.
6. Child evidence must carry enough identity to bind to episode, task contract
   revision and its direct parent record.
7. Provider output is untrusted evidence until deterministic Core validation
   and the applicable verification layers accept it.

## 6. Current integration gap

The repository has strong, separately tested substrates, but the following
unified orchestration path is not yet implemented:

```text
Human goal
 -> intake
 -> episode
 -> planning
 -> provider run
 -> worker execution
 -> patch
 -> verification
 -> outcome / closure
```

Explorer, execution, patch, verification and alignment records currently have
typed local contracts and provenance fields, but there is not yet one durable
Episode aggregate or query projection that lists every child run. The next
implementation should add references/projections deliberately rather than
making `Episode` own the child domain logic.

## 7. Hermes boundary

Hermes may:

- propose a provider or workflow;
- decompose a typed task into proposed steps;
- request bounded context and read-only diagnostics;
- report provider observations back through Core contracts.

Hermes may not:

- create or mutate Episode state directly;
- rewrite TaskContract or promote a candidate requirement by itself;
- bypass PolicyEngine, ApprovalService, queue leases or fencing;
- publish artifacts or verification results directly;
- change a verification/alignment verdict;
- treat a route proposal as approval.

The intended boundary is:

```text
Hermes proposal
        -> Core policy/approval
        -> Core durable job/event
        -> worker/provider adapter
        -> Core validation and evidence persistence
```

## 8. Deferred decisions

These are intentionally not frozen by this review:

- full event-sourcing and replay semantics;
- centralized Episode transition matrix and recovery orchestrator;
- one unified child-record query projection;
- provider registry and deterministic routing policy;
- experience/evaluation memory promotion rules;
- final Episode closure criteria;
- distributed event ordering beyond current timestamp/id ordering.

They require implementation evidence or a separate ADR. They must not be
silently inferred from the current Episode ledger.

## 9. Review conclusion

The domain boundary is healthy enough for a Hermes contract design, provided
Hermes is treated as a proposal/coordination layer and not as a second Core.
The immediate next design artifact should be `HERMES_V0_1_ORCHESTRATION_CONTRACT.md`;
no provider routing implementation is implied by this document.
