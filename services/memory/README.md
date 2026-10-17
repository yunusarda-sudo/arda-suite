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
