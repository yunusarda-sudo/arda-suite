# Shared Memory installation

Use the root [ARDA Suite setup](../../../README.md) for the complete local-first
product. The Jarvis app and memory service keep separate Python environments.

## Local development

```bash
cd services/memory
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Run the MCP server over stdio for a local MCP client:

```bash
.venv/bin/python mcp_server.py
```

### Smoke testing

Once your backend services (Qdrant and Ollama) are running, run the local smoke test to verify end-to-end extraction and retrieval:

```bash
.venv/bin/python smoke_test.py
```

See the [Shared Memory smoke test guide](../README.md#local-smoke-test) for backend configuration, cleanup behavior, and data isolation requirements.

HTTP mode is optional and requires a private bearer token. The default Pi
installer creates `~/.mem0/mcp-token` with mode `0600` and binds MCP,
the dashboard, and Qdrant to loopback. See
[`RASPBERRY-PI-SETUP.md`](RASPBERRY-PI-SETUP.md) before installing on a host.

Provider credentials belong in the ignored `~/.mem0/config.env` file or a
secret manager. Never paste them into tracked configuration or client command
arguments.
