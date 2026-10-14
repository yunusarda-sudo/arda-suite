"""Where memory lives, as far as a hook is concerned.

A hook should say "store this" and "give me the rows"; it should not know whether
that means a Qdrant on this box or an HTTPS call to another one. That knowledge
lives here and nowhere else, so Qdrant and Ollama stay implementation details of
the Central Brain rather than things every worker has to be told about.

    MEM0_BACKEND=local   (default)  this machine is the brain
    MEM0_BACKEND=remote             talk to the brain over authenticated MCP

The local backend is the existing code path, unchanged in behaviour: mem0 does
the extraction, the junk filter prunes what it should not have kept, and recall
reads Qdrant's scroll API directly because that is fast and this runs on every
session start.

The remote backend deliberately does no extraction of its own. It hands the raw
text to the brain's `add_memory` tool, so the LLM, the embedder and the vector
store all stay on one machine — a worker needs no Ollama, no Qdrant, no mem0, and
(because the client below is stdlib-only) not even the `mcp` package.

Known asymmetry, worth stating rather than discovering: the junk filter runs
after extraction and deletes by memory id, which the MCP surface does not expose.
Remote writes therefore get the brain's extraction rules (config.CUSTOM_INSTRUCTIONS)
but not the post-hoc junk sweep.
"""

import json
import os
import urllib.request

LOCAL = "local"
REMOTE = "remote"
# A value that is neither. Not folded into LOCAL: "MEM0_BACKEND=remot" on a
# worker would silently write the session's memory into a local Qdrant that is
# not the brain — a typo would cost the memory, quietly. Refusing is the smaller
# failure, and the operation is the only thing that fails, never the session.
INVALID = "invalid"

OWNER = os.environ.get("MEM0_OWNER", "default")
DEFAULT_AGENT = "claude-code"

MEM0_ROOT = os.path.dirname(os.path.abspath(__file__))

# Hooks must never hang a session; every call here is bounded.
HTTP_TIMEOUT = 20
RECALL_TIMEOUT = 5


def backend_name(env=None):
    """LOCAL (also when unset), REMOTE, or INVALID for anything else."""
    env = os.environ if env is None else env
    raw = env.get("MEM0_BACKEND")
    if raw is None or not raw.strip():
        return LOCAL
    choice = raw.strip().lower()
    return choice if choice in (LOCAL, REMOTE) else INVALID


def log(message):
    """Diagnostic line, file only.

    Never stdout: on SessionStart that stream is the model's context, and a
    configuration complaint injected there is noise the model would try to act
    on. Failures here are swallowed — logging must not become the thing that
    breaks a hook.
    """
    try:
        d = os.path.join(MEM0_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        import datetime
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(d, "backend.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (ts, message))
    except Exception:
        pass


def _invalid_backend_message(env=None):
    env = os.environ if env is None else env
    return ("MEM0_BACKEND=%r is not %r or %r — refusing the memory operation "
            "(set it correctly or unset it to use the local store)"
            % (env.get("MEM0_BACKEND"), LOCAL, REMOTE))


# --- remote: the smallest MCP client that can call three tools ---------------

class McpError(RuntimeError):
    pass


class MiniMcpClient:
    """Streamable-HTTP MCP over urllib.

    Not a general client — it does initialize + tools/call and nothing else, on
    purpose. Pulling the real SDK onto every worker would drag a dependency tree
    onto machines whose only job is to hand text to the brain.
    """

    def __init__(self, url, token, timeout=HTTP_TIMEOUT):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id = None

    def _headers(self):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             "Authorization": "Bearer %s" % self.token}
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        return h

    def _post(self, body, expect_reply=True):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            sid = r.headers.get("mcp-session-id")
            if sid:
                self.session_id = sid
            raw = r.read().decode("utf-8", "replace")
        return self._parse(raw) if expect_reply else None

    @staticmethod
    def _parse(raw):
        """The server answers as SSE (`event: message` / `data: {...}`), and may
        answer as plain JSON depending on configuration. Accept both."""
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raw = raw.strip()
        if raw.startswith("{"):
            return json.loads(raw)
        raise McpError("unparseable MCP reply")

    def connect(self):
        reply = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "mem0-hook", "version": "1"}},
        })
        if "error" in reply:
            raise McpError("initialize failed: %s" % reply["error"].get("message"))
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   expect_reply=False)
        return self

    def call(self, tool, arguments):
        """Tool result as plain text. Raises on transport or tool error."""
        reply = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": tool, "arguments": arguments}})
        if "error" in reply:
            raise McpError("%s failed: %s" % (tool, reply["error"].get("message")))
        result = reply.get("result") or {}
        if result.get("isError"):
            raise McpError("%s returned an error" % tool)
        parts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        return "\n".join(parts)


def _remote_client(timeout=HTTP_TIMEOUT, env=None):
    env = os.environ if env is None else env
    url = env.get("MEM0_BRAIN_URL")
    if not url:
        raise McpError("MEM0_BACKEND=remote needs $MEM0_BRAIN_URL")
    import sys
    sys.path.insert(0, MEM0_ROOT)
    from mcp_transport import load_token          # same token rules as the server
    return MiniMcpClient(url, load_token(env), timeout=timeout).connect()


# --- write -------------------------------------------------------------------

def write(text, scope, agent=DEFAULT_AGENT, env=None):
    """Hand `text` to memory. Returns the facts that were added, or None if the
    write FAILED.

    The None-vs-[] distinction is load-bearing for the callers: [] means the
    extractor read the text and found nothing durable (advance the cursor), None
    means the text never landed (do not advance, or that window is lost).
    """
    chosen = backend_name(env)
    if chosen == INVALID:
        log("write refused: " + _invalid_backend_message(env))
        return None                      # not consumed — the caller holds its cursor
    if chosen == REMOTE:
        return _write_remote(text, scope, agent, env)
    return _write_local(text, scope, agent)


def _write_local(text, scope, agent):
    import sys
    sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
    from mem0_common import add_to_mem0_local
    return add_to_mem0_local(text, scope, agent)


def _parse_added(reply_text):
    """`add_memory` reports lines like "[ADD] the fact". Anything else (including
    "No new facts were extracted") means nothing was added."""
    out = []
    for line in (reply_text or "").splitlines():
        line = line.strip()
        if line.startswith("[ADD]"):
            out.append({"memory": line[5:].strip(), "event": "ADD"})
    return out


def _write_remote(text, scope, agent, env=None):
    try:
        client = _remote_client(env=env)
        reply = client.call("add_memory",
                            {"text": text, "scope": scope, "agent": agent})
    except Exception:
        return None                      # caller keeps its cursor; nothing lost
    return _parse_added(reply)


# --- read (recall) -----------------------------------------------------------

def rows(env=None, timeout=RECALL_TIMEOUT):
    """Every memory row owned by OWNER as {memory, run_id, agent_id, created_at}.

    Raises on failure; recall is best-effort and its caller decides what to do
    with that (it injects nothing rather than delaying a session start).
    """
    chosen = backend_name(env)
    if chosen == INVALID:
        log("read refused: " + _invalid_backend_message(env))
        raise McpError("invalid MEM0_BACKEND")   # recall's fail-open injects nothing
    if chosen == REMOTE:
        return _rows_remote(env, timeout)
    return _rows_local(timeout)


def _qdrant_scroll_url(env=None):
    env = os.environ if env is None else env
    host = env.get("MEM0_QDRANT_HOST", "localhost")
    port = env.get("MEM0_QDRANT_PORT", "6333")
    return "http://%s:%s/collections/mem0_local/points/scroll" % (host, port)


def _rows_local(timeout):
    """Read all local rows through the shared owner-scoped cursor reader."""
    from memory_store import read_inventory
    return read_inventory(owner=OWNER, include_superseded=True, timeout=timeout)


def parse_listing(text):
    """`list_memories` renders "[YYYY-MM-DD HH:MM] (scope/agent) fact" per line.

    Parsed leniently: a line that does not match is skipped rather than failing
    the batch, because one odd row must not cost a session its whole recall.
    """
    out = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.startswith("["):
            continue
        try:
            ts, rest = line[1:].split("]", 1)
            rest = rest.lstrip()
            if not rest.startswith("("):
                continue
            attr, memory = rest[1:].split(")", 1)
            scope, _, agent = attr.partition("/")
        except ValueError:
            continue
        memory = memory.strip()
        status = "active"
        if memory.startswith("[SUPERSEDED]"):
            status = "superseded"
            memory = memory[len("[SUPERSEDED]"):].strip()
        if not memory:
            continue
        out.append({"memory": memory,
                    "run_id": None if scope == "-" else scope,
                    "agent_id": None if agent == "-" else agent,
                    "status": status,
                    "created_at": ts.strip()})
    return out


def _rows_remote(env, timeout):
    client = _remote_client(timeout=timeout, env=env)
    return parse_listing(client.call("list_memories", {}))
