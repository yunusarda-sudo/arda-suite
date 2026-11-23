# One brain, many workers

One machine holds the memory, the models and the vector store. Every other
machine is a thin client that owns none of it and asks over authenticated MCP.

A worker needs no Ollama, no Qdrant, no mem0, not even this repo's venv — the
remote client in `mem0_backend.py` is stdlib-only. That is the whole point: the
expensive, stateful half stays in one place, and adding a machine costs three
files and a token.

> **Status.** The code and this procedure are ready and the loopback path is
> verified end to end. The Tailscale Serve activation in §2 is a manual step that
> has **not** been run yet — nothing here claims a tailnet is currently serving.

## The shape

```
                    BRAIN  (this machine)
  ┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │   Ollama :11434                                          │
  │   qwen2.5:7b-instruct  ─→  mem0  ──→  Qdrant :6333       │
  │   nomic-embed-text         extraction  vectors + payloads│
  │                                                          │
  │   ~/.mem0/architectures/*.json   (plans, phases, tasks)  │
  │                                                          │
  │   dashboard :8899  ── no auth, loopback only ──┐         │
  │                                                 │         │
  │   MCP HTTP :8812/mcp  ── bearer token ──┐      │         │
  └─────────────────────────────────────────┼──────┼─────────┘
                                            │      │
                    Tailscale Serve :443 ───┘      │  SSH tunnel
                    (TLS, tailnet only)            │  (-L 8899)
                                            │      │
  ┌─────────────────────────────────────────┼──────┼─────────┐
  │                                         ▼      ▼         │
  │   Claude Code                                            │
  │     ├── mem0_backend.py      remote backend, stdlib only │
  │     ├── mcp_transport.py     token loading rules         │
  │     └── bin/mcp-auth-header  prints the Authorization    │
  │                                                          │
  │   NO Ollama.  NO Qdrant.  NO mem0.  NO venv.             │
  └──────────────────────────────────────────────────────────┘
                    WORKER  (every other machine)
```

Two independent layers, and neither substitutes for the other:

| Layer | Question it answers | Mechanism |
|---|---|---|
| Tailscale | is this device allowed on my network | tailnet membership / ACLs |
| Bearer token | is this caller allowed to touch my memory | `token_verifier`, 401 |

Do not drop the token because Tailscale is in front. A tailnet is a network
boundary, not an application one.

Extraction always happens on the brain. A remote write hands raw text to
`add_memory`, so the LLM, the embedder and the vector store never leave this
machine. Known asymmetry: the junk filter deletes by memory id, which the MCP
surface does not expose, so remote writes get the brain's extraction rules but
not the post-hoc junk sweep.

## 1. Brain: the MCP server

Already installed as a launchd agent, bound to loopback:

```bash
mem0 status                                   # MCP HTTP: ✅ auth zorunlu
mem0 http-install                             # if it is not loaded yet
```

The server stays on `127.0.0.1:8812`. Serve is the only thing that listens on
the tailnet, so Qdrant and Ollama are unreachable from anywhere else by
construction rather than by firewall rule.

**One required setting before a worker can connect.** DNS rebinding protection
checks the `Host` header *after* the token, so a request that dialled a tailnet
name is rejected with **421** even though its token was accepted. Name the
hostname workers will use:

```xml
<!-- ~/Library/LaunchAgents/com.mem0.mcp-http.plist, EnvironmentVariables -->
<key>MEM0_MCP_ALLOWED_HOSTS</key>
<string><memory-host-tailnet-name>:443</string>
```

```bash
launchctl kickstart -k gui/$(id -u)/com.mem0.mcp-http
```

Comma-separate multiple entries. Every entry needs an explicit port, wildcards
are refused, and the loopback entries are always present — a bad value stops the
server rather than being silently ignored.

## 2. Brain: Tailscale Serve

```bash
tailscale serve --bg --https=443 http://localhost:8812
tailscale serve status          # must show the mapping
tailscale funnel status         # must stay EMPTY — funnel is public internet
```

Serve terminates TLS with the tailnet's own certificate and forwards plain HTTP
that never leaves the host. Serve config is stored by `tailscaled` and is
expected to survive reboots — verify that after a reboot rather than assuming it.

Then confirm from the worker that the endpoint answers **and still refuses
anonymous callers**:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{}' https://<memory-host-tailnet-name>/mcp        # expect 401
```

## 3. Worker: files and token

Three files, nothing else:

```bash
mkdir -p ~/arda-suite/services/memory/bin
scp brain:~/arda-suite/services/memory/bin/mcp-auth-header  ~/arda-suite/services/memory/bin/
scp brain:~/arda-suite/services/memory/mem0_backend.py      ~/arda-suite/services/memory/
scp brain:~/arda-suite/services/memory/mcp_transport.py     ~/arda-suite/services/memory/
chmod +x ~/arda-suite/services/memory/bin/mcp-auth-header
```

The token, once:

```bash
mkdir -p ~/.mem0
scp brain:~/.mem0/mcp-token ~/.mem0/ && chmod 600 ~/.mem0/mcp-token
```

`mcp-auth-header` refuses to emit a token from a group- or world-readable file,
so the `chmod` is load-bearing, not hygiene. On Windows there is no equivalent of
this script — store the token under `%USERPROFILE%` with an ACL limited to your
account and provide a native helper that prints the same one-key JSON.

## 4. Worker: Claude Code

```bash
claude mcp add-json mem0-brain \
  '{"type":"http","url":"https://<memory-host-tailnet-name>/mcp","headersHelper":"'"$HOME"'/arda-suite/services/memory/bin/mcp-auth-header"}' \
  -s user

claude mcp list         # expect: mem0-brain ... ✔ Connected
```

`-s user` so it works in every project, and so no repo ever carries the config.
`headersHelper` takes a command **string**; only the path is stored, never the
token. Expand `$HOME` yourself as above — a literal `~` is not expanded here.

For the SessionStart hooks to use the brain too, the worker's environment needs:

```bash
export MEM0_BACKEND=remote
export MEM0_BRAIN_URL=https://<memory-host-tailnet-name>/mcp
```

A typo in `MEM0_BACKEND` refuses the memory operation rather than quietly
writing into a local store that is not the brain.

## 5. Worker: Claude Desktop (Verified)

Tested on **Claude Desktop (macOS, build 6.28899.x+)**.

> [!IMPORTANT]
> `claude_desktop_config.json` uses the standard Model Context Protocol (MCP) client
> schema. It **does not support** the CLI-specific `headersHelper` parameter. If you
> configure `"type": "http"` with a `headersHelper` directly in `claude_desktop_config.json`,
> Claude Desktop will fail to authenticate (returning HTTP 401 Unauthorized) because the
> Bearer token is omitted.

Use one of the two verified configurations below depending on whether the brain runs locally or remotely:

### Option A: Local Brain on the Same Machine (Recommended stdio)

When running Claude Desktop on the same host where Shared Memory is installed:

```jsonc
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "mem0-brain": {
      "command": "/ABSOLUTE/PATH/TO/arda-suite/services/memory/bin/mem0-python",
      "args": [
        "/ABSOLUTE/PATH/TO/arda-suite/services/memory/mcp_server.py"
      ]
    }
  }
}
```

This runs the server over standard input/output (`stdio`) without network overhead or token management.

### Option B: Remote Brain via Tailscale / HTTPS (`mcp-remote` Bridge)

When connecting Claude Desktop to a remote Central Brain that requires HTTP Bearer token authentication:

Use `mcp-remote` to bridge Desktop's stdio transport to the remote authenticated HTTP endpoint:

```jsonc
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "mem0-brain": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<memory-host-tailnet-name>/mcp",
        "--header",
        "Authorization: Bearer <TOKEN>"
      ]
    }
  }
}
```

*Replace `<memory-host-tailnet-name>` with your verified Tailscale hostname and `<TOKEN>` with your private 0600 token from `~/.mem0/mcp-token`.* Never commit this file or expose the bearer token.

## 6. Dashboard: SSH tunnel only

```bash
ssh -N -L 8899:localhost:8899 user@memory-host     # leave running
open http://localhost:8899                     # on the worker
```

> **The dashboard must NEVER be network-bound.** It has **no authentication of
> any kind** — no token, no password, no check. It reads and writes memory,
> architectures, skills and agent runs. Anything that can reach port 8899 owns
> the brain. `dashboard/server.py` hard-codes `HOST = "localhost"` with no
> environment override, and that is a feature. Do not put it behind Serve, do not
> bind it to a tailnet address, do not "temporarily" expose it.

The tunnel is the correct shape because it authenticates with SSH keys and
terminates on loopback at both ends.

## 7. Token rotation

Order matters. Do it this way and there is no window where a worker gets 401:

```bash
# 1. Generate the new token on the brain, but do NOT activate it yet.
( umask 077; openssl rand -hex 32 > ~/.mem0/mcp-token.new )

# 2. Distribute to EVERY worker first. They are not using it yet.
scp ~/.mem0/mcp-token.new worker:~/.mem0/mcp-token.new

# 3. On each worker, swap it in. The brain still accepts the old one,
#    so this is still a no-downtime step.
ssh worker 'mv ~/.mem0/mcp-token.new ~/.mem0/mcp-token && chmod 600 ~/.mem0/mcp-token'

# 4. Only now activate it on the brain, and restart.
mv ~/.mem0/mcp-token.new ~/.mem0/mcp-token && chmod 600 ~/.mem0/mcp-token
launchctl kickstart -k gui/$(id -u)/com.mem0.mcp-http

# 5. Verify from a worker.
claude mcp list                                # expect ✔ Connected
```

The brain holds exactly one token — there is no per-worker credential and no
expiry. So a leaked token is valid until this procedure replaces it, and
rotating revokes every worker at once. Rotate on any suspicion: a token that
touched a commit, a log, a chat or a screenshot is leaked, whether or not anyone
used it.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **421** `Invalid Host header` | the name the worker dialled is not in the allowlist — note the token was already accepted, so this is not an auth problem | add `host:port` to `MEM0_MCP_ALLOWED_HOSTS` in the plist (§1), then `launchctl kickstart -k gui/$(id -u)/com.mem0.mcp-http` |
| **401** `invalid_token` | token missing, wrong, truncated, or the file is group/world readable | `ls -l ~/.mem0/mcp-token` → must be `-rw-------`; `~/arda-suite/services/memory/bin/mcp-auth-header` must print one JSON line; compare against the brain's copy |
| **connection refused** / unreachable | Serve not running, or Tailscale down on either end | `tailscale serve status` on the brain (mapping present?), `tailscale status` on both, `tailscale ping brain` |
| `mcp-auth-header` exits non-zero | no token file, empty, shorter than 32 chars, or odd characters | it prints the reason on stderr and never the token; re-copy per §3 |
| worker writes vanish silently | `MEM0_BACKEND` typo, or the brain was unreachable | writes return `None` and hooks fail open by design; check `logs/backend.log` on the worker |
| dashboard unreachable | tunnel dropped | the `ssh -N -L` process must stay running; re-run §6 |

Blast-radius check on the brain — everything but Serve must be loopback:

```bash
lsof -nP -iTCP -sTCP:LISTEN | grep -E '8812|6333|11434|8899'   # all 127.0.0.1
tailscale funnel status                                        # must be empty
```
