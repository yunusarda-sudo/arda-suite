# Arda Core M2.4D — Verification Research Gate

Date: 2026-10-01
Status: `D1-D3.5 IMPLEMENTED; D4 RESEARCH + CONTRACT`

M2.4D is an independent verification boundary. PatchWorker may produce an
`APPLIED` patch, but it cannot declare correctness. The first slice is
models-free and verifies only structural integrity.

## Evidence-backed design

Recent coding-agent evaluations separate migration/scope audit from behavioral
tests and independent agentic checks. SWE Refactor Bench reports that passing
behavioral tests alone can miss whether the requested migration occurred.
RETRACE likewise treats post-generation verification as a separate signal and
uses forward/backward reconstruction rather than trusting the generator's own
interpretation. SWE-RPG separates requirement clarification and planning from
final patch evaluation. SWE-Proof reports that tests can miss counterexamples;
formal specifications are a later, domain-specific challenger, not this
baseline.

Arda therefore keeps these records separate:

```text
PatchResult       what the worker changed
StructuralEvaluation  whether the evidence is internally consistent
BehavioralEvaluation test/regression outcome (D2)
RequirementEvaluation requirement alignment (D3/D4)
DeploymentApproval    Core policy/approval, never a verifier shortcut
```

## D1 structural checks

`StructuralVerifier` compares the immutable PatchResult with independent
before/after workspace snapshots and checks:

- episode identity;
- patch status is `APPLIED` but not a correctness claim;
- base tree hash;
- result tree hash;
- exact changed/touched file set;
- allowed-file scope;
- forbidden paths unchanged;
- per-file postimage hashes;
- generated unified diff;
- absence of a verification-success field on PatchResult.

The output is one of:

```text
STRUCTURAL_PASS
REJECTED
INCONCLUSIVE
```

No test command, shell command, LLM, or reasoning transcript is used in D1.

## Implemented and later slices

```text
D1  structural verification                    COMPLETE
D2  bounded baseline vs patched test harness   COMPLETE
D3  requirement/acceptance audit contract      COMPLETE
D3.5 requirement revision/versioning           COMPLETE
D4  independent adversarial verifier           RESEARCH + CONTRACT
D5  composed physical verification loop        LATER
```

Formal verification remains a challenger capability for domains where a
faithful formal specification exists. It is not required for the D1 baseline.

References:

- [SWE Refactor Bench](https://arxiv.org/abs/2608.23564)
- [RETRACE independent patch verification](https://arxiv.org/abs/2608.08950)
- [SWE-RPG requirement and planning benchmark](https://arxiv.org/abs/2608.09072)
- [SWE-Proof machine-checked verification](https://arxiv.org/abs/2609.21190)
- [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
