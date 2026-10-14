"""How the mem0-local MCP server is reachable — transport selection and, for
HTTP, who is allowed to talk to it.

Kept out of mcp_server.py so the tool definitions there stay purely about memory
semantics; nothing in this file changes what a tool does, only who can call it
and over which pipe.

Two modes:

  stdio (default)   unchanged. The local Claude Code registration spawns the
                    server as a subprocess and owns the pipe, so the OS process
                    boundary is the whole security model — no token involved.

  streamable-http   opt-in. The pipe becomes a socket that anything on the host
                    can connect to, so it refuses to start without a shared
                    secret and binds loopback-only unless explicitly told not to.

Auth uses the SDK's own extension point rather than hand-rolled middleware: give
FastMCP a `token_verifier` plus `AuthSettings` and it wraps the endpoint in
RequireAuthMiddleware/BearerAuthBackend itself. All this module supplies is the
one-method verifier that says whether a presented bearer token is ours.

Environment:
  MEM0_MCP_TRANSPORT      "stdio" (default) | "streamable-http"
  MEM0_MCP_HOST           bind address, default 127.0.0.1
  MEM0_MCP_PORT           bind port, default 8812
  MEM0_MCP_ALLOW_NONLOCAL "1" to permit a non-loopback bind (deliberate opt-in)
  MEM0_MCP_ALLOWED_HOSTS  comma-separated extra `host:port` values accepted in
                          the Host header (for a reverse proxy / tailnet name)
  MEM0_MCP_TOKEN          the shared secret, or
  MEM0_MCP_TOKEN_FILE     path to a 0600 file holding it
                          (default ~/.mem0/mcp-token)
"""

import hmac
import json
import os
import stat
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8812
DEFAULT_TOKEN_FILE = os.path.expanduser("~/.mem0/mcp-token")
DEFAULT_TOKENS_FILE = os.path.expanduser("~/.mem0/tokens.json")

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# A token this short is guessable at HTTP speeds; `openssl rand -hex 32` gives 64.
MIN_TOKEN_LEN = 32

STDIO = "stdio"
HTTP = "streamable-http"


class ConfigError(RuntimeError):
    """Refusal to start. The message is safe to print — it never carries the token."""


def transport_from(argv=None, env=None):
    """Chosen transport: `--transport X` beats $MEM0_MCP_TRANSPORT beats stdio."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    choice = env.get("MEM0_MCP_TRANSPORT", STDIO)
    for i, a in enumerate(argv):
        if a == "--transport" and i + 1 < len(argv):
            choice = argv[i + 1]
        elif a.startswith("--transport="):
            choice = a.split("=", 1)[1]
    if choice not in (STDIO, HTTP):
        raise ConfigError(
            "unknown transport %r (expected %s or %s)" % (choice, STDIO, HTTP)
        )
    return choice


def _reject_loose_permissions(path):
    """A secret readable by anyone else on the box is already leaked."""
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            "token file %s is group/world accessible; run: chmod 600 %s" % (path, path)
        )


def load_token(env=None):
    """The shared secret, from the environment or a private file.

    Never falls back to a default or generated value: an HTTP server that
    invents its own password is one nobody knows is unprotected.
    """
    env = os.environ if env is None else env
    token = (env.get("MEM0_MCP_TOKEN") or "").strip()
    source = "$MEM0_MCP_TOKEN"
    if not token:
        path = env.get("MEM0_MCP_TOKEN_FILE") or DEFAULT_TOKEN_FILE
        source = path
        if not os.path.exists(path):
            tokens_path = env.get("MEM0_MCP_TOKENS_FILE") or DEFAULT_TOKENS_FILE
            if os.path.exists(tokens_path):
                _reject_loose_permissions(tokens_path)
                try:
                    with open(tokens_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and data:
                        for tok in data:
                            if len(tok) >= MIN_TOKEN_LEN:
                                return tok
                except Exception:
                    pass
            raise ConfigError(
                "HTTP mode needs a token: set $MEM0_MCP_TOKEN or create %s "
                "(openssl rand -hex 32 > %s && chmod 600 %s)" % (path, path, path)
            )
        _reject_loose_permissions(path)
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    if len(token) < MIN_TOKEN_LEN:
        raise ConfigError(
            "token from %s is %d chars; need at least %d"
            % (source, len(token), MIN_TOKEN_LEN)
        )
    return token


def load_tokens(env=None):
    """Load token dictionary mapping token string to metadata dictionary.
    Supports either multi-token ~/.mem0/tokens.json or single-token ~/.mem0/mcp-token.
    """
    env = os.environ if env is None else env
    tokens_path = env.get("MEM0_MCP_TOKENS_FILE") or DEFAULT_TOKENS_FILE
    if os.path.exists(tokens_path):
        _reject_loose_permissions(tokens_path)
        try:
            with open(tokens_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                valid_tokens = {}
                for tok, meta in data.items():
                    if len(tok) >= MIN_TOKEN_LEN:
                        valid_tokens[tok] = meta if isinstance(meta, dict) else {"user_id": str(meta)}
                if valid_tokens:
                    return valid_tokens
        except json.JSONDecodeError:
            raise ConfigError("tokens file %s is not valid JSON" % tokens_path)

    # Fallback to single token
    token = load_token(env)
    return {token: {"user_id": env.get("MEM0_OWNER", "default"), "client_id": "mem0-local"}}


def validate_token_registry(token_map, owner):
    """Reject token claims that the single-owner memory tools do not enforce."""
    if not isinstance(token_map, dict) or not token_map:
        raise ConfigError("HTTP token registry must contain at least one token")
    for token, meta in token_map.items():
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LEN or not isinstance(meta, dict):
            raise ConfigError("HTTP token registry contains an invalid entry")
        if meta.get("user_id", owner) != owner:
            raise ValueError("HTTP token registry user_id must match MEM0_OWNER")
        scopes = meta.get("scopes") or []
        if not isinstance(scopes, list) or scopes:
            raise ValueError("HTTP token scopes are not enforced; use an empty scopes list")
        if meta.get("role") not in (None, "developer"):
            raise ValueError("HTTP token role is not enforced; only developer is supported")
    return token_map


class MultiTokenVerifier:
    """FastMCP TokenVerifier for same-owner credential rotation."""

    def __init__(self, token_map, default_client_id="mem0-local"):
        self._tokens = token_map or {}
        self._default_client_id = default_client_id

    async def verify_token(self, token):
        from mcp.server.auth.provider import AccessToken

        if not token:
            return None
        matched_meta = None
        for registered_token, meta in self._tokens.items():
            if hmac.compare_digest(token, registered_token):
                matched_meta = meta
                break
        if not matched_meta:
            return None
        client_id = matched_meta.get("client_id") or matched_meta.get("user_id") or self._default_client_id
        scopes = matched_meta.get("scopes") or []
        return AccessToken(
            token=token, client_id=client_id, scopes=scopes, expires_at=None
        )


class SharedSecretVerifier(MultiTokenVerifier):
    """FastMCP's TokenVerifier protocol against one static shared secret.
    Preserved for backward compatibility.
    """

    def __init__(self, token, client_id="mem0-local"):
        super().__init__({token: {"client_id": client_id}}, default_client_id=client_id)
        self._token = token
        self._client_id = client_id


def http_bind(env=None):
    """(host, port) for HTTP mode, refusing a non-loopback bind by default.

    Reaching this server means reaching the memory store behind it, so widening
    the bind address is a decision that has to be made out loud.
    """
    env = os.environ if env is None else env
    host = env.get("MEM0_MCP_HOST", DEFAULT_HOST)
    try:
        port = int(env.get("MEM0_MCP_PORT", DEFAULT_PORT))
    except ValueError:
        raise ConfigError("MEM0_MCP_PORT must be a number, got %r"
                          % env.get("MEM0_MCP_PORT"))
    if host not in LOOPBACK and env.get("MEM0_MCP_ALLOW_NONLOCAL") != "1":
        raise ConfigError(
            "refusing to bind %s: that exposes the memory store beyond this "
            "machine. Set MEM0_MCP_ALLOW_NONLOCAL=1 if that is really intended "
            "(and put it behind a private network, not a port forward)." % host
        )
    return host, port


def _split_host_port(entry):
    """(host, port) for an allowlist entry, or None if it is not `host:port`.

    IPv6 is written bracketed (`[::1]:8812`) so the last colon is unambiguous.
    """
    host, sep, port = entry.rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    return host, port


def extra_allowed_hosts(env=None):
    """Additional Host header values from $MEM0_MCP_ALLOWED_HOSTS.

    A worker reaching the brain through Tailscale Serve or any reverse proxy
    sends the *name* it dialled, not the address we bound; DNS rebinding
    protection rejects that with 421 long after the token has been checked. This
    is how that name gets named — explicitly, one entry at a time.

    Every entry must carry a port and must not be a wildcard: `brain.ts.net:*`
    would accept the same name on any port, and a bare host would accept it on
    all of them. Both would hand back the DNS rebinding protection this list
    exists to preserve, so a loose entry is refused rather than narrowed.
    """
    env = os.environ if env is None else env
    raw = env.get("MEM0_MCP_ALLOWED_HOSTS") or ""
    out = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "*" in entry:
            raise ConfigError(
                "MEM0_MCP_ALLOWED_HOSTS entry %r contains a wildcard; list each "
                "host:port explicitly" % entry
            )
        if _split_host_port(entry) is None:
            raise ConfigError(
                "MEM0_MCP_ALLOWED_HOSTS entry %r needs an explicit port "
                "(e.g. %s:%d)" % (entry, entry, DEFAULT_PORT)
            )
        out.append(entry)
    return out


def _transport_security(host, port, env=None):
    """Host/Origin allowlist so a browser on this machine cannot be tricked into
    driving the server (DNS rebinding). Loopback names plus whatever we bound,
    plus anything $MEM0_MCP_ALLOWED_HOSTS names.

    The loopback entries are unconditional: they are what the local HTTP
    registration on this machine connects as, and no amount of remote
    configuration is allowed to cost us that.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "%s:%d" % (host, port)]
    for entry in extra_allowed_hosts(env):
        if entry not in hosts:
            hosts.append(entry)
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def build_server(fastmcp_cls, name, argv=None, env=None):
    """The FastMCP instance for the selected transport.

    stdio gets exactly what it always got — `fastmcp_cls(name)` — so the local
    registration cannot regress through this path.
    """
    if transport_from(argv, env) != HTTP:
        return fastmcp_cls(name)

    from mcp.server.auth.settings import AuthSettings

    host, port = http_bind(env)
    env = os.environ if env is None else env
    tokens = validate_token_registry(load_tokens(env), env.get("MEM0_OWNER", "default"))
    base = "http://%s:%d" % (host, port)
    # No authorization server actually runs here; tokens are issued out of band
    # (you write one into the token file). These URLs only populate the RFC 9728
    # protected-resource metadata, which a static-token client never consults.
    return fastmcp_cls(
        name,
        host=host,
        port=port,
        token_verifier=MultiTokenVerifier(tokens),
        auth=AuthSettings(issuer_url=base, resource_server_url=base),
        transport_security=_transport_security(host, port, env),
    )
