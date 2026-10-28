# Hermes Local Tool Compatibility Bake-off v0.1

Date: 2026-10-07

## Result

No `LOCAL_HERMES_CANDIDATE` was selected. TaskForge remains **BLOCKED**.

All runs used disposable Hermes homes/workspaces, no Arda MCP, no cloud
provider, `model.streaming: false`, named `custom:local-ollama`, and the same
terminal prompt. Raw probes used `stream:false`, `num_ctx:8192`, and a bounded
96-token output.

| Model | Raw `/api/chat` | Raw `/v1` | Hermes terminal | Hermes file | Class |
|---|---:|---:|---:|---:|---|
| qwen3.5:4b | PASS (previous verified) | PASS (previous verified) | RESOURCE_ABORT / not completed | not run | RESOURCE_UNSUITABLE |
| phi4-mini:3.8b-q4_K_M | FAIL: serialized content, no structured call | FAIL: serialized content, no structured call | not run | not run | MODEL_TOOL_CALL_INCOMPATIBILITY |
| granite3.3:2b | PASS | PASS | FAIL: 0 tool_use, 0 tool_result | not run | HERMES_TRANSPORT_OR_NORMALIZATION |
| qwen3:4b-thinking-2507-q4_K_M | FAIL: HTTP 500 | PASS | FAIL: 0 tool_use, 0 tool_result | not run | MODEL_TOOL_CALL_INCOMPATIBILITY |

The two candidates with both raw routes producing structured tool calls still
failed at Hermes' non-interactive loop. This reproduces the earlier qwen result
and strengthens the conclusion that the current failure boundary is Hermes'
request/response normalization or tool-loop invocation path, not simply one
qwen model or the Ollama OpenAI-compatible endpoint.

## Resource observations

The qwen3.5 run filled the 2 GB swap and reached approximately 62.8°C before
the controlled resource abort. Granite terminal smoke reached 51.8°C with
swap nearly exhausted. Qwen3 Thinking reached 49.6°C with swap nearly
exhausted. No thermal abort threshold of 80°C was reached. The optional
5.2 GB LFM2.5 challenger was not pulled because its footprint is not clearly
safe on this 8 GB Pi after observed swap pressure.

## Goal judge and TaskForge gate

`goal_judge` was not run because no candidate achieved both Hermes terminal and
file-loop compatibility. TaskForge was not started.

## Next experiment

Inspect and instrument Hermes' non-interactive tool-call normalization against a
known-good Granite raw response, without changing production code or starting
TaskForge.

Artifacts on Pi:

`./artifacts/hermes-local-tool-bakeoff/`

Official candidate references: [Phi-4-mini](https://ollama.com/library/phi4-mini),
[Granite 3.3](https://ollama.com/library/granite3.3),
[Qwen3 tags](https://ollama.com/library/qwen3/tags), and
[LFM2.5](https://ollama.com/library/lfm2.5).
