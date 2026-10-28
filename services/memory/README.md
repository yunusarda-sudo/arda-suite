# Shared Memory Service

<p align="center">
  <strong>Local-first persistent vector memory daemon with standardized Model Context Protocol (MCP) support.</strong>
</p>

Shared Memory is an independent, local-first service providing long-term semantic memory for AI assistants and agent runtimes. It combines:
- **Vector Storage**: [Qdrant](https://qdrant.tech/) vector database with local [Ollama](https://ollama.ai/) embeddings (`nomic-embed-text`) and memory extraction (`qwen2.5:7b-instruct`).
- **Standardized Interfaces**: Model Context Protocol (MCP) HTTP endpoint with bearer-token authentication.
- **Monitoring & Maintenance**: Interactive web dashboard, automated memory compaction, and offline retrieval evaluation harness.
- **Client Integration**: Pluggable memory adapter for Jarvis and any MCP-compatible agent framework.

---

## Quickstart

For Raspberry Pi or Linux hosts, see the [Pi Setup Guide](docs/RASPBERRY-PI-SETUP.md) or run the automated installer:
```bash
./scripts/install-pi.sh
```

For local development and testing:
```bash
# See docs/INSTALL.md for detailed environment prerequisites
python3 -m pytest -q
```

### Local smoke test

To verify end-to-end memory extraction and retrieval against the active backend:

```bash
python3 smoke_test.py
```

#### Prerequisites and configuration

The test initializes memory via `config.get_memory()` and requires the services defined by your backend configuration:

- **Vector database**: A running Qdrant instance reachable at `MEM0_QDRANT_HOST:MEM0_QDRANT_PORT` (default: `localhost:6333`).
- **Embeddings**: An Ollama instance reachable at `MEM0_OLLAMA_BASE_URL` (default: `http://localhost:11434`) with `nomic-embed-text` installed (`ollama pull nomic-embed-text`).
- **LLM extraction backend**:
  - **Local Ollama** (`MEM0_LLM_PROVIDER=ollama`, default): Requires Ollama running with an instruct model installed, defaulting to `qwen2.5:7b-instruct` (`ollama pull qwen2.5:7b-instruct`) or specified by `MEM0_LLM_MODEL`.
  - **Remote provider** (`MEM0_LLM_PROVIDER` set to `openai`, `nvidia`, `openrouter`, or `groq`): Requires valid API credentials (`MEM0_LLM_API_KEY` or provider-specific keys like `OPENAI_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`) and optional `MEM0_LLM_BASE_URL`.

#### Data lifecycle and cleanup

The test writes sample conversation facts under a fresh, randomized user ID (`smoke_test_<hex>`) so each run starts clean without state carryover. It queries the memories to verify search hits and attempts cleanup in a `finally` block using `mem.delete_all(user_id=user)`.

#### Reporting and scope

- **Success**: Prints `OK — <N> facts stored, <M> found by search.` and exits `0`.
- **Failure**: Prints `FAIL — ...` detailing whether fact extraction or vector search returned no results, and exits `1`. If cleanup fails, a warning is logged.
- **Verification scope**: The smoke test validates live stack connectivity and basic extraction/search flows. It is not a substitute for unit tests (`python3 -m pytest -q`) or deployment and retrieval benchmark verification.

> [!WARNING]
> Run the smoke test only against an isolated development backend. Never run it against live user or production memory stores, as unexpected termination or failed cleanup could leave residual test data in your store or consume hosted API credits.

## Data and network boundaries

- The installer binds MCP, dashboard, and Qdrant to loopback by default.
- The dashboard has no authentication. Do not expose it directly to a network.
- Memory data and credentials live outside this repository under `~/.mem0`
  and the configured Qdrant storage directory. Back them up separately.
- Ollama is the default LLM provider. If you explicitly configure a hosted
  provider, memory extraction content is sent to that provider. Keep its key in
  the ignored, permission-restricted `~/.mem0/config.env`; never commit keys.
- Use an authenticated SSH or Tailscale tunnel for remote MCP access. See
  [SECURITY.md](../../SECURITY.md) for reporting and security guidance.

## Scope

This service is independently deployable and optional. A successful local test
run does not establish Raspberry Pi performance or production readiness. Check
the host's service status and backup before upgrading an existing installation.
