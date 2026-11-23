# WP-9: External MCP Client Validation & Ecosystem Documentation (Closing Issue #16)

**Status:** Planned  
**Base:** `main` (`48a1aba`)  
**Goal:** Verify and document standard Model Context Protocol (MCP) HTTP and stdio client integrations (specifically Claude Desktop and external IDE clients), resolve Issue #16, and solidify external client documentation.

---

## Context and boundaries

GitHub Issue #16 highlights that `services/memory/docs/SINGLE-BRAIN.md` currently marks the Claude Desktop HTTP MCP configuration as "TODO and unverified", noting that relying on experimental headers (e.g. `headersHelper`) is unsafe without explicit testing.

Shared Memory provides an authenticated HTTP MCP server requiring bearer tokens, as well as a stdio MCP adapter. This package sets up an automated verification fixture, documents verified client configuration templates, and updates repository documentation to ensure users can safely connect external tools (Claude Desktop, Cursor, Hermes, etc.) to the Shared Memory brain.

---

## Milestones

1. **Isolated Client Test Harness:**
   - Launch a disposable local Shared Memory test instance with ephemeral Qdrant vector storage and known test memories.
   - Test client connection pathways:
     - Direct stdio MCP bridge (e.g., `python -m services.memory.mcp_server`).
     - HTTP MCP with bearer token authorization (`Authorization: Bearer <TOKEN>`).

2. **Claude Desktop Integration Verification:**
   - Test standard Claude Desktop MCP configuration against the isolated test instance.
   - If Claude Desktop native HTTP transport does not support custom headers or bearer tokens directly, provide and document an authenticated lightweight stdio-to-HTTP bridge proxy script.
   - Confirm read-only tool calls (`search_memory`, `list_memories`) and policy-gated writes.

3. **Documentation Update & Single-Brain Guide Completion:**
   - Update `services/memory/docs/SINGLE-BRAIN.md` with:
     - Exact tested Claude Desktop versions and working `claude_desktop_config.json` snippets.
     - Security boundaries (loopback requirement, SSH tunnel requirements for remote Pi access).
   - Update `services/memory/docs/SIRI-SHORTCUTS.md` ensuring all example payloads adhere to updated API schemas.

4. **Issue #16 Resolution & Pull Request:**
   - Prepare verification evidence (sanitized logs, reproducible test commands).
   - Submit Pull Request linking and closing Issue #16.

---

## Acceptance and verification

| Check | Acceptance Criteria | Target Status |
| :--- | :--- | :--- |
| `stdio-mcp-smoke` | Stdio MCP responds correctly to `tools/list` and `tools/call` | PENDING |
| `http-mcp-auth` | Unauthenticated HTTP requests return 401; valid bearer returns 200 | PENDING |
| `claude-integration` | Documented Claude Desktop config functions without leaking credentials | PENDING |
| `docs-verified` | `services/memory/docs/SINGLE-BRAIN.md` TODO marker removed; evidence documented | PENDING |
| `issue-16-closed` | PR submitted with reproduction evidence and closes Issue #16 | PENDING |

---

## Risk and Security Policy

- **No Credential Leaks:** No personal API keys, production tokens, or private network hosts are to be used in tests or included in committed documentation.
- **Network Safety:** External clients must never bind to public interfaces without TLS and bearer authentication.
