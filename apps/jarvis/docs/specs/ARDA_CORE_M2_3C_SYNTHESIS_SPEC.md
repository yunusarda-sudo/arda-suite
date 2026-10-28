# Arda Core M2.3C — Evidence-Constrained Synthesis

Durum: **implemented offline contract slice**

M2.3C adds an untrusted model-proposal stage after the existing M2.3A/M2.3B
source and evidence pipeline. The model is never the source of truth.

## Pipeline

```text
Sources
  -> Evidence
  -> bounded EvidencePack
  -> ModelGateway
  -> structured candidate claims
  -> evidence-ID validation
  -> deterministic Markdown/JSON result
```

## Guarantees

- `build_evidence_pack` deterministically orders evidence and bounds evidence
  count, excerpt size and total serialized size.
- The pack contains source metadata, evidence IDs, excerpts and provenance; it
  does not contain arbitrary Core state or credentials.
- `ModelGateway` is provider-independent. The first concrete path is a small
  OpenAI-compatible HTTP gateway with injectable transport for offline tests.
- A factual candidate claim must contain one or more evidence IDs belonging to
  the current pack. Unknown IDs, duplicate claim IDs, malformed output and
  evidence-less claims are rejected.
- `INSUFFICIENT_EVIDENCE` is a valid bounded result. It cannot contain claims.
- `CONFLICTING_EVIDENCE` preserves structured conflict IDs and both evidence
  sides; it does not silently select a source.
- Markdown is rendered from validated claims and evidence references, never
  from arbitrary model-written citation strings.
- Provider failures are normalized without exposing API keys or raw provider
  payloads.
- Usage metadata records model and token counts when supplied by the provider.

## Exclusions

This slice does not add live LLM calls, LiteLLM deployment, autonomous
browsing, recursive crawling, browser automation, shell/code execution,
persistent learning or autonomous task creation. Those remain separate gates.
