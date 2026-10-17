# WP-4: Model Gateway, Fallback, and Evidence-Driven Memory

Status: **IMPLEMENTED; OPEN DRAFT PR #9 STACKED ON PR #7; LIVE ACCEPTANCE PARTIAL**

## Purpose and starting point

The master work plan asks for exact-model capability/privacy/cost routing, explicitly approved fallback, expiry-aware project memory, and retrieval evaluation against a no-memory baseline. This package extends the current in-process Ollama/OpenAI-compatible gateways, Core provider authorization, append-only route/provider evidence, project-scoped Mem0/Qdrant memory, and the offline retrieval comparison. It does not add a mandatory proxy service to the Raspberry Pi.

## Design decision

**ADR-004: keep routing in-process for the first Pi release.** The existing Core route decision remains the authority to invoke providers. `RoutedModelGateway` adds an exact model profile and versioned route snapshot above the existing `ModelGateway` protocol. A route selects only profiles that satisfy declared output capability, data class, paid-fallback consent, total attempt/deadline, and optional preflight cost bounds. Auth denial, malformed output, and provider policy rejection are permanent for that request; only classified transient/rate-limit failures can advance to an explicitly enabled fallback. Generation retry never replays tools or external actions.

Live documentation/repository research compared:

- [Bifrost ARM64 setup](https://github.com/maximhq/bifrost/blob/dev/docs/quickstart/gateway/setting-up.mdx) documents an arm64 image, but this checkout has no measurement of its RSS, latency, or startup on the Pi. Bifrost remains a future measured alternative if the in-process adapter set becomes operationally expensive.
- [Bifrost routing](https://github.com/maximhq/bifrost/blob/dev/docs/providers/provider-routing.mdx), [fallback/retries](https://github.com/maximhq/bifrost/blob/dev/docs/features/retries-and-fallbacks.mdx), and [observability](https://github.com/maximhq/bifrost/blob/dev/docs/features/observability/default.mdx) support weighted routing, budgets, and content-log controls; retry multiplication still requires ARDA-owned total-attempt/deadline bounds.
- [LiteLLM SDK and proxy](https://docs.litellm.ai/docs/) offer broad provider coverage. A proxy introduces another Pi service and, for shared budgets, persistence dependencies; `litellm-core` may be prototyped in-process only if adapter maintenance is measured as a problem.
- Exact provider/model feature differences are documented in the [Bifrost support matrix](https://github.com/maximhq/bifrost/blob/dev/docs/overview.mdx). ARDA must validate tool/JSON/streaming behavior per exact profile; OpenAI-compatible API shape alone is not proof of equivalent semantics.

## Integrated changes

- `arda.model_routes` now defines exact versioned model profiles, explicit capability and data-class allowlists, deterministic profile order, explicit paid fallback, a total attempt count and deadline, provider cooldown metadata, and optional cumulative cost preflight. `build_routed_gateway()` reads API keys only from named environment variables. Route/profile snapshots persist append-only in Core SQLite with canonical hashes; gateway objects, prompts, endpoints, and credentials are omitted from snapshots and health summaries.
- `ModelTransientError`, `ModelRateLimitedError`, and `ModelAuthenticationError` separate retryable/provider-quota failures from permanent credential denial. `BudgetedModelGateway` retries only explicitly transient generation errors. OpenAI-compatible and Ollama HTTP status handling does not include provider response bodies in error messages.
- `arda.evidence_memory` adds an append-only structured evidence ledger keyed by project. Records require HTTPS source provenance, source SHA-256, timezone-aware observation/expiry, and are excluded from recall after expiry or outside their project. High-confidence bearer/API-key/email/phone patterns are redacted before persistence; this is not a general PII detector, so source ingestion still needs policy and review. A deterministic fixture evaluator reports precision@k, recall@k, false recalls, and recall lift over an empty-memory baseline.
- Existing `services/memory/retrieval_compare.py` remains the semantic retrieval experiment. Its existing [methodology](../../../../services/memory/docs/retrieval-comparison.md) already provides no-memory/semantic baselines, judged relevance, Recall@k, MRR@k, nDCG@k and dataset fingerprints. It requires reviewed, local evaluation snapshots; synthetic tests are mechanics-only.

## Verification and remaining gates

- The provider profile store uses Core migrations 19-20 on top of WP-1's current migration head. The migration is append-only and route versions bind exact model profile snapshots.
- Unit/fault tests cover transient fallback, authentication denial, private-data fallback rejection, capability mismatch, paid fallback gating, cost preflight, cooldown, bounded deadlines, credential-redacted HTTP classifications, route snapshot integrity, memory scope, expiry, PII/secret redaction, idempotency, and retrieval scores.
- `PASS` — `./.venv/bin/python -m pytest -o addopts='' -q`: **1366 passed, 5 skipped in 45.13s** on the WP-1-integrated branch. The five skipped cases are live Ollama classification checks requiring the ignored local vault config.
- `PASS` — `python3 -m pytest -q test_retrieval_compare.py` from `services/memory`: **13 passed**.
- `PASS` — live synthetic public-prompt smoke via configured route: OpenAI `gpt-4o-mini` returned JSON with 23 input/5 output tokens; local Ollama `qwen3:1.7b` returned JSON with 32 prompt/6 output tokens. No prompt secrets or repository content were sent.
- `PASS` — `python -m py_compile` on changed Python modules, shell syntax checks for the relevant existing installers, and `git diff --check`.
- `NOT_RUN` — GitHub CI; PR #9 currently reports no configured status checks for its branch.
- Synthetic fixtures do not prove live retrieval quality or provider semantics. The currently configured host reports one OpenAI API credential and a local Ollama with `qwen3:8b`, `qwen2.5:7b-instruct`, `qwen3:1.7b`, and `nomic-embed-text:latest`; no second hosted provider credential is configured. Two independent hosted API provider acceptance, forced live 429/timeout/malformed/streaming tests, paid-fallback billing, and exact-model tool conformance remain blocked pending a second provider account/credential and deliberate live test window.
- The current in-process text gateway supports chat and schema-constrained JSON. Streaming, vision, and model tool-call execution are deliberately not advertised as supported capabilities; no profile can route a task requiring them.
- Live smoke probes on this host returned `{"ok":true}` from OpenAI `gpt-4o-mini` (23 input / 5 output tokens) and local Ollama `qwen3:1.7b` (32 prompt / 6 output tokens). These used a public synthetic prompt and prove basic request/response only. No hosted pricing was recorded, and the second independent hosted API provider remains unavailable.
- No Raspberry Pi resource measurements were run. Cost values are deployment configuration, not embedded market pricing. The UTF-8 byte preflight bound is deliberately conservative and not a substitute for provider billing reconciliation.
- No production model route was changed or deployed. Core still requires its existing policy ALLOW before provider invocation.

## Acceptance checklist

- [x] Compare Bifrost, LiteLLM, and the in-process approach; choose the least operationally risky initial Pi design.
- [x] Add exact model capability/privacy/cost profiles, append-only versioned routes, bounded transient fallback, cooldown, and safe health metadata.
- [x] Keep model retry separate from tool/action retry; permanent auth denial and unsupported capability never fall back.
- [x] Store project-scoped sourced, time-valid structured memory with secret/PII redaction and a deterministic no-memory retrieval baseline.
- [x] Exercise one authorized hosted API route and the local Ollama route with non-sensitive synthetic prompts.
- [ ] Test two real hosted providers plus local Ollama; validate actual 429, timeout, malformed/partial response, auth denial, privacy-denied fallback, cost, and streaming/tool/JSON behavior.
- [ ] Run reviewed real-project retrieval labels through semantic/no-memory comparison and report measured quality deltas.
- [ ] Measure Pi ARM64 startup/RSS/latency and review whether an external gateway is justified.
