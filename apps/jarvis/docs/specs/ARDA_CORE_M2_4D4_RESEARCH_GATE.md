# Arda Core M2.4D4 — Adversarial Verifier Research Gate

Date: 2026-10-01
Status: `RESEARCH + CONTRACT ONLY`

## Problem

Structural, behavioral and requirement evidence answer different questions. A
patch can be structurally valid, make tests pass and still solve the wrong
problem or introduce a security weakness. Recent work motivates keeping an
independent reconstruction signal separate: RETRACE reconstructs the problem
from the patch without access to the original issue and then reconciles the
forward/backward views; SWExploit shows that functionally correct repair patches
can still be vulnerable; R4P treats patch reasoning as a useful scalable signal
but not a truth oracle.

Sources:

- [RETRACE — Independent Patch Verification](https://arxiv.org/abs/2608.08950)
- [SWExploit — Red Teaming Program Repair Agents](https://arxiv.org/abs/2509.25894)
- [R4P — Scalable Supervision via Patch Reasoning](https://arxiv.org/abs/2510.22775)

## Proposed D4 flow

```text
TaskContract revision
  → forward expected-intent view

PatchResult + structural/behavioral evidence
  → blind backward reconstruction

forward view + backward view
  → contradiction analysis
  → alignment verdict
  → disagreement record
```

The backward reviewer must not receive:

- original objective or requirement text;
- generator reasoning or chain-of-thought;
- generator model identity;
- the forward reviewer’s conclusion.

It may receive the patch, changed-file scope, repository snapshot/context
allowed by policy, and independently observed structural/behavioral evidence.

## Verdict vocabulary

```text
ALIGNED       reconstructed intent agrees with the active requirements
PARTIAL       some intent aligns; material requirements remain absent/unclear
MISALIGNED    patch appears to target a different or forbidden problem
INCONCLUSIVE  evidence or reconstruction confidence is insufficient
```

`ALIGNED` is not `CORRECT`, `REQUIREMENT_SATISFIED` or
`DEPLOYMENT_APPROVED`.

## Safety conclusions

- model confidence is evidence metadata, not an approval decision;
- unsupported confidence claims are rejected or mapped to `INCONCLUSIVE`;
- forward/backward disagreement is persisted, never silently averaged away;
- verifier identity, version, input hashes and evidence references are durable;
- structural, behavioral, requirement and adversarial results remain separate;
- D4 cannot mutate task state, publish artifacts or approve deployment.

## Scope boundary

This gate defines the contract and research findings only. It does not add an
LLM, multi-model routing, autonomous browsing, arbitrary shell execution,
security scanning or deployment approval. A later implementation must first
pass offline fixtures for blind isolation, contradiction detection, unsupported
confidence, disagreement logging and replay determinism.
