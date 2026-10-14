# Raspberry Pi setup

This guide installs the Shared Memory service from the ARDA Suite checkout. It
keeps Qdrant, MCP, and the dashboard on loopback. The dashboard has no
authentication and must not be exposed to a network.

## Install

From the repository root on the Pi:

```bash
cd services/memory
./scripts/install-pi.sh
```

The installer provisions Docker/Qdrant, Ollama, a Python virtual environment,
and the MCP, dashboard, and maintenance systemd units. It creates
`~/.mem0/mcp-token` and `~/.mem0/config.env` with private permissions. Provider
credentials, if required by your chosen model provider, belong only in the
ignored `~/.mem0/config.env` file.

Docker must be available from the configured signed apt repositories. The
installer pins Qdrant to `qdrant/qdrant:v1.19.2`; install Ollama separately
using its official instructions and review its installer before running it.
The setup script does not pipe remote installer scripts into a shell.

## Verify

```bash
systemctl status mem0-mcp mem0-dashboard mem0-pipeline.timer
curl http://127.0.0.1:6333/healthz
```

The installer does not migrate or overwrite an existing Qdrant database. Before
upgrading an existing host, inspect its current service units, Qdrant volume,
owner id, and token configuration and keep a separate backup.

## Remote MCP access

Use an authenticated SSH or Tailscale tunnel to reach the MCP endpoint. Keep the
dashboard and Qdrant bound to loopback. Do not set `MEM0_MCP_ALLOW_NONLOCAL=1`
or change either service's bind address on an Internet-facing host.

The service templates and install behavior are maintained in
[`../systemd/`](../systemd/) and [`../scripts/install-pi.sh`](../scripts/install-pi.sh).
