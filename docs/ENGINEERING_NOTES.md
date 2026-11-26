# ARDA Suite — Engineering Evidence & Architecture Notes

This document preserves the implementation evidence, design questions, and verification audit trails across the ARDA Suite codebase.

---

## The Engineering Problem

An agent that returns a plausible answer is different from one that can safely change files or call tools. ARDA explores the engineering between **intent and action**: permission gates, constrained execution, verification, audit trails, and persistent context.

```mermaid
flowchart TD
    A[Voice or Task Input] --> B[Jarvis & ARDA Core]
    B --> C[Plan & Select Capability]
    C --> D{Permission Boundary}
    D -->|Allow / Human Approved| E[Tool Execution]
    D -->|Deny| F[Recorded Refusal]
    E --> G[Verification & Audit]
    B -. Optional Memory Adapter .-> H[Shared Memory Service]
    H --> I[Authenticated MCP HTTP & Local API]
    I --> J[(Qdrant Vector DB)]
    I --> K[Local Ollama / Configured Provider]
```

---

## Engineering Evidence Matrix

| Question | Implementation & Evidence |
| :--- | :--- |
| **How are tool calls permissioned?** | [Permission policy](apps/jarvis/arda/agent_permission.py), [tool registry](apps/jarvis/arda/agent_tools.py), [permission tests](apps/jarvis/tests/test_agent_permission.py) |
| **How does a multi-step task run and get observed?** | [Executor](apps/jarvis/arda/agent_executor.py), [step verifier](apps/jarvis/arda/agent_verifier.py), [audit log](apps/jarvis/arda/agent_audit.py) |
| **Where is task state coordinated?** | [Control plane](apps/jarvis/arda/control_plane.py), [execution bridge](apps/jarvis/arda/execution_bridge.py) |
| **How do clients authenticate to Shared Memory?** | [MCP transport](services/memory/mcp_transport.py), [transport tests](services/memory/test_mcp_transport.py) |
| **How is memory kept outside the application lifecycle?** | [Memory service](services/memory/mcp_server.py), [component and data boundaries](PRODUCT_LAYOUT.md) |
| **Which design decisions are documented?** | [Jarvis architecture](apps/jarvis/docs/ARCHITECTURE.md), [Specifications index](apps/jarvis/docs/specs/README.md) |

---

## Operating & Security Boundaries

- **Tool Execution Policy**: Tool invocation passes through an explicit **AUTO / CONFIRM / DENY** evaluation engine. High-risk filesystem or terminal actions require human-in-the-loop confirmation.
- **Shared Memory MCP Transport**: HTTP MCP and dashboard listeners bind to loopback (`127.0.0.1`) by default. HTTP MCP requires bearer-token authentication. See [SECURITY.md](../SECURITY.md) and [mcp_transport.py](../services/memory/mcp_transport.py).
- **Privacy by Default**: Model execution defaults to local Ollama. Cloud LLM providers are strictly opt-in.
- **Data Exclusion**: Private memory databases, Qdrant vectors, auth tokens, model weights, and machine-specific configs are excluded from Git tracking.

---

## Resilient Agent Lifecycle Patterns

ARDA Suite incorporates defensive runtime patterns adapted from production open-source agent runtimes:

1. **Fast-Path / Slow-Path Intent Routing**:
   Deterministic commands (time checks, exact tool dispatches, status probes) bypass model inference using zero-allocation keyword matching before escalating to full LLM evaluation.

2. **Singleton Daemon Spawner & Startup Crash Preservation**:
   Background processes (memory service, local listeners) synchronize startup behind an async mutex (`_spawn_lock`). On startup, non-empty previous logs are rotated to `.log.1` rather than truncated, preserving crash backtraces across unexpected process exits.

3. **Structured Tool Denial Recovery**:
   When a user denies a high-risk tool approval (`CONFIRM -> DENIED`), the executor does not terminate the conversation; instead, it injects a structured denial outcome (`{ status: "denied_by_user", reason: ... }`) back into the planner to let the agent formulate an alternative path.

4. **Deterministic Exit Hierarchy**:
   Subprocess scripts and CLI entrypoints enforce strict Unix exit code semantics (`0` = success, `1` = clean empty result/no match, `2` = syntax/usage error).

