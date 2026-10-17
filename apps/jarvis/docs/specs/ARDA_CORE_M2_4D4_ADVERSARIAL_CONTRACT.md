# Arda Core M2.4D4 — AdversarialVerificationContract v0.1

## Records

### ForwardIntent

Derived from the selected immutable `TaskContract` revision and its typed
requirement evidence:

- `task_contract_revision_id`
- `task_contract_hash`
- expected requirement IDs and statuses
- forbidden changes and allowed scope
- evidence references

The forward view is not allowed to include a later backward verdict.

### BackwardReconstruction

Produced by an independent verifier from a blind input pack:

- `patch_result_id`
- `patch_tree_hash`
- changed file paths and diff hash
- observed behavioral/structural evidence references
- reconstructed problem statement
- reconstructed intent items
- cited patch/evidence references
- confidence value and confidence basis
- `verifier_id` and `verifier_version`

The blind input pack excludes the original task text, generator reasoning,
generator identity and forward conclusion.

### AlignmentEvaluation

```text
episode_id
task_contract_revision_id
patch_result_id
structural_evaluation_id
behavioral_evaluation_id
requirement_audit_id
forward_intent_hash
backward_reconstruction_hash
verdict: ALIGNED | PARTIAL | MISALIGNED | INCONCLUSIVE
matched_requirement_ids[]
unmatched_requirement_ids[]
contradiction_ids[]
disagreement_ids[]
evidence_refs[]
verifier_id
verifier_version
created_at
canonical_hash
```

## Contract rules

1. Backward reconstruction is blind to the generator’s interpretation.
2. Every alignment claim references a patch or independent evidence item.
3. A confidence number without a confidence basis is invalid.
4. Missing evidence yields `INCONCLUSIVE`, never `ALIGNED`.
5. A forbidden or contradictory reconstructed intent yields `MISALIGNED`.
6. Partial requirement coverage yields `PARTIAL`, not `ALIGNED`.
7. Forward/backward disagreement is an explicit record, not a hidden retry.
8. The same input hashes and verifier version must reproduce the same result.
9. The verifier cannot modify `PatchResult`, `BehavioralEvaluation`,
   `RequirementEvaluation` or deployment state.
10. The result is advisory evidence; Core policy remains the approval authority.

## Failure-first acceptance matrix

| Case | Expected result |
| --- | --- |
| backward input contains task text | reject blind review setup |
| backward input contains generator reasoning | reject blind review setup |
| patch has no reconstructable intent | `INCONCLUSIVE` |
| reconstructed intent contradicts forbidden change | `MISALIGNED` |
| only subset of requirements matches | `PARTIAL` |
| all typed requirements align with evidence | `ALIGNED` |
| unsupported confidence claim | `INCONCLUSIVE` |
| forward/backward disagreement | disagreement record + non-final verdict |
| changed input hash on replay | reject as provenance mismatch |
| verifier tries to approve deployment | reject capability boundary |

## Not claimed

This contract does not claim that a model-based verifier is correct, unbiased,
secure by itself or equivalent to a human approval. D4 is one additional signal
in the evidence stack:

```text
Structural + Behavioral + Requirement + Adversarial + real outcome
```
