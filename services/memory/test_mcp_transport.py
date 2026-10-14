#!/usr/bin/env python3
"""Unit tests for transport selection and HTTP-mode admission control.

These cover the decisions made before a single byte is served. The end-to-end
behaviour over a real socket is covered by two scripts that drive a running
server: mcp_auth_check.py (401s, malformed input) and mcp_check.py (tool
semantics, run against both transports to prove they agree).

Run: .venv/bin/python -m unittest test_mcp_transport -v
"""

import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import mcp_transport as t

GOOD = "a" * 64          # long enough to pass the length floor


class TestTransportSelection(unittest.TestCase):

    def test_defaults_to_stdio(self):
        """The existing local registration runs with no flags and no env; it
        must keep getting stdio however this file grows."""
        self.assertEqual(t.transport_from(argv=["mcp_server.py"], env={}), t.STDIO)

    def test_env_selects_http(self):
        self.assertEqual(
            t.transport_from(argv=["x"], env={"MEM0_MCP_TRANSPORT": t.HTTP}), t.HTTP)

    def test_flag_beats_env(self):
        self.assertEqual(
            t.transport_from(argv=["x", "--transport", t.STDIO],
                             env={"MEM0_MCP_TRANSPORT": t.HTTP}), t.STDIO)

    def test_equals_form_of_the_flag(self):
        self.assertEqual(
            t.transport_from(argv=["x", "--transport=" + t.HTTP], env={}), t.HTTP)

    def test_unknown_transport_is_refused(self):
        """Typos must not silently degrade to a different transport."""
        with self.assertRaises(t.ConfigError):
            t.transport_from(argv=["x", "--transport", "sse-ish"], env={})


class TestBind(unittest.TestCase):

    def test_defaults_to_loopback(self):
        self.assertEqual(t.http_bind(env={}), (t.DEFAULT_HOST, t.DEFAULT_PORT))

    def test_port_override(self):
        self.assertEqual(t.http_bind(env={"MEM0_MCP_PORT": "9001"})[1], 9001)

    def test_non_numeric_port_is_refused(self):
        with self.assertRaises(t.ConfigError):
            t.http_bind(env={"MEM0_MCP_PORT": "eight"})

    def test_non_loopback_bind_is_refused_by_default(self):
        """Binding 0.0.0.0 exposes the whole memory store to the network; that
        cannot be something you get by forgetting a flag."""
        with self.assertRaises(t.ConfigError):
            t.http_bind(env={"MEM0_MCP_HOST": "0.0.0.0"})

    def test_non_loopback_bind_allowed_with_explicit_opt_in(self):
        host, _ = t.http_bind(env={"MEM0_MCP_HOST": "0.0.0.0",
                                   "MEM0_MCP_ALLOW_NONLOCAL": "1"})
        self.assertEqual(host, "0.0.0.0")

    def test_refusal_message_names_the_way_out(self):
        with self.assertRaises(t.ConfigError) as ctx:
            t.http_bind(env={"MEM0_MCP_HOST": "0.0.0.0"})
        self.assertIn("MEM0_MCP_ALLOW_NONLOCAL", str(ctx.exception))


class TestTokenLoading(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "mcp-token")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text, mode=0o600):
        Path(self.path).write_text(text)
        os.chmod(self.path, mode)

    def test_env_token_wins(self):
        self.assertEqual(t.load_token(env={"MEM0_MCP_TOKEN": GOOD}), GOOD)

    def test_token_file_is_read_and_stripped(self):
        self._write(GOOD + "\n")
        self.assertEqual(t.load_token(env={"MEM0_MCP_TOKEN_FILE": self.path}), GOOD)

    def test_missing_token_refuses_to_start(self):
        """No token means no server. An HTTP memory store that starts without
        one is worse than one that fails loudly."""
        with self.assertRaises(t.ConfigError):
            t.load_token(env={"MEM0_MCP_TOKEN_FILE": self.path})

    def test_short_token_is_refused(self):
        self._write("hunter2")
        with self.assertRaises(t.ConfigError):
            t.load_token(env={"MEM0_MCP_TOKEN_FILE": self.path})

    def test_group_or_world_readable_token_file_is_refused(self):
        """Anyone else on the box could already have read it."""
        self._write(GOOD, mode=0o644)
        with self.assertRaises(t.ConfigError) as ctx:
            t.load_token(env={"MEM0_MCP_TOKEN_FILE": self.path})
        self.assertIn("chmod 600", str(ctx.exception))

    def test_no_error_message_ever_contains_the_token(self):
        """Refusal messages are printed and logged; a token in one is a leak."""
        self._write(GOOD, mode=0o644)
        for env in ({"MEM0_MCP_TOKEN_FILE": self.path},
                    {"MEM0_MCP_TOKEN": "short"}):
            with self.assertRaises(t.ConfigError) as ctx:
                t.load_token(env=env)
            self.assertNotIn(GOOD, str(ctx.exception))
            self.assertNotIn("short", str(ctx.exception).replace("shortest", ""))

    def test_load_token_falls_back_to_tokens_json(self):
        tokens_json_path = os.path.join(self._tmp.name, "tokens.json")
        with open(tokens_json_path, "w", encoding="utf-8") as f:
            json.dump({GOOD: {"user_id": "alice"}}, f)
        os.chmod(tokens_json_path, 0o600)
        # Point MEM0_MCP_TOKEN_FILE to missing path, but MEM0_MCP_TOKENS_FILE to existing valid json
        missing_token_file = os.path.join(self._tmp.name, "nonexistent-mcp-token")
        tok = t.load_token(env={
            "MEM0_MCP_TOKEN_FILE": missing_token_file,
            "MEM0_MCP_TOKENS_FILE": tokens_json_path,
        })
        self.assertEqual(tok, GOOD)


class TestSharedSecretVerifier(unittest.TestCase):

    def setUp(self):
        self.v = t.SharedSecretVerifier(GOOD)

    def verify(self, token):
        return asyncio.run(self.v.verify_token(token))

    def test_correct_token_is_accepted(self):
        self.assertIsNotNone(self.verify(GOOD))

    def test_wrong_token_is_rejected(self):
        self.assertIsNone(self.verify("b" * 64))

    def test_empty_token_is_rejected(self):
        self.assertIsNone(self.verify(""))
        self.assertIsNone(self.verify(None))

    def test_prefix_of_the_token_is_rejected(self):
        """Guards against a comparison that stops at the shorter string."""
        self.assertIsNone(self.verify(GOOD[:-1]))


class TestMultiTokenVerifier(unittest.TestCase):

    def setUp(self):
        self.tok1 = "a" * 32
        self.tok2 = "b" * 32
        token_map = {
            self.tok1: {"user_id": "alice", "client_id": "worker-1", "scopes": ["read"]},
            self.tok2: {"user_id": "bob", "client_id": "worker-2", "scopes": ["write"]},
        }
        self.v = t.MultiTokenVerifier(token_map)

    def verify(self, token):
        return asyncio.run(self.v.verify_token(token))

    def test_token_for_alice_is_accepted_with_metadata(self):
        res = self.verify(self.tok1)
        self.assertIsNotNone(res)
        self.assertEqual(res.client_id, "worker-1")
        self.assertEqual(res.scopes, ["read"])

    def test_token_for_bob_is_accepted_with_metadata(self):
        res = self.verify(self.tok2)
        self.assertIsNotNone(res)
        self.assertEqual(res.client_id, "worker-2")
        self.assertEqual(res.scopes, ["write"])

    def test_unknown_token_is_rejected(self):
        self.assertIsNone(self.verify("c" * 32))


class TestTokenRegistryValidation(unittest.TestCase):
    """The current memory store has one owner and does not enforce scopes/roles."""

    OWNER = "single-owner"

    def test_accepts_same_owner_credentials_for_rotation(self):
        # Both credentials may briefly coexist during rotation, but they must
        # resolve to the same data owner and claim no unenforced scopes.
        registry = {
            "a" * 64: {"user_id": self.OWNER, "client_id": "worker", "scopes": []},
            "b" * 64: {"user_id": self.OWNER, "client_id": "worker", "scopes": []},
        }
        self.assertEqual(t.validate_token_registry(registry, owner=self.OWNER), registry)

    def test_rejects_metadata_for_another_owner(self):
        registry = {
            "a" * 64: {"user_id": self.OWNER, "scopes": []},
            "b" * 64: {"user_id": "other-owner", "scopes": []},
        }
        with self.assertRaises(ValueError):
            t.validate_token_registry(registry, owner=self.OWNER)

    def test_rejects_nonempty_scopes_that_are_not_enforced(self):
        registry = {"a" * 64: {"user_id": self.OWNER, "scopes": ["read"]}}
        with self.assertRaises(ValueError):
            t.validate_token_registry(registry, owner=self.OWNER)

    def test_rejects_unsupported_role_claim(self):
        registry = {"a" * 64: {"user_id": self.OWNER, "scopes": [], "role": "admin"}}
        with self.assertRaises(ValueError):
            t.validate_token_registry(registry, owner=self.OWNER)


class TestExtraAllowedHosts(unittest.TestCase):
    """$MEM0_MCP_ALLOWED_HOSTS widens the Host header allowlist for a proxied
    worker. It must widen it by exactly what it names and nothing more."""

    def test_unset_adds_nothing(self):
        """The local-only setup must behave exactly as it did before this knob
        existed."""
        self.assertEqual(t.extra_allowed_hosts(env={}), [])

    def test_empty_string_adds_nothing(self):
        self.assertEqual(t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": ""}), [])

    def test_comma_separated_entries_are_all_kept(self):
        self.assertEqual(
            t.extra_allowed_hosts(
                env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443,brain.local:8812"}),
            ["brain.ts.net:443", "brain.local:8812"])

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(
            t.extra_allowed_hosts(
                env={"MEM0_MCP_ALLOWED_HOSTS": " brain.ts.net:443 , brain.local:8812 "}),
            ["brain.ts.net:443", "brain.local:8812"])

    def test_empty_entries_between_commas_are_skipped(self):
        self.assertEqual(
            t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": "a.ts.net:443,,"}),
            ["a.ts.net:443"])

    def test_entry_without_a_port_is_refused(self):
        """A bare host would match that name on every port."""
        with self.assertRaises(t.ConfigError) as ctx:
            t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net"})
        self.assertIn("port", str(ctx.exception))

    def test_non_numeric_port_is_refused(self):
        with self.assertRaises(t.ConfigError):
            t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:https"})

    def test_wildcard_port_is_refused(self):
        """`brain.ts.net:*` is the tempting shortcut that gives the name away on
        every port; it has to be spelled out instead."""
        with self.assertRaises(t.ConfigError) as ctx:
            t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:*"})
        self.assertIn("wildcard", str(ctx.exception))

    def test_wildcard_host_is_refused(self):
        with self.assertRaises(t.ConfigError):
            t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": "*:8812"})

    def test_one_bad_entry_refuses_the_whole_list(self):
        """Dropping the bad one and continuing would half-apply a security
        setting the operator believes is in force."""
        with self.assertRaises(t.ConfigError):
            t.extra_allowed_hosts(
                env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443,oops"})

    def test_bracketed_ipv6_entry_is_accepted(self):
        self.assertEqual(
            t.extra_allowed_hosts(env={"MEM0_MCP_ALLOWED_HOSTS": "[fd00::1]:8812"}),
            ["[fd00::1]:8812"])


class TestTransportSecurity(unittest.TestCase):
    """The allowlist actually handed to the SDK."""

    def test_loopback_entries_are_unconditional(self):
        """The local mem0-http-test registration connects as 127.0.0.1; adding a
        remote name must never be what takes that away."""
        ts = t._transport_security(t.DEFAULT_HOST, t.DEFAULT_PORT,
                                   env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443"})
        for entry in ("127.0.0.1:*", "localhost:*", "[::1]:*"):
            self.assertIn(entry, ts.allowed_hosts)

    def test_extra_hosts_are_appended(self):
        ts = t._transport_security(t.DEFAULT_HOST, t.DEFAULT_PORT,
                                   env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443"})
        self.assertIn("brain.ts.net:443", ts.allowed_hosts)

    def test_unset_env_leaves_the_original_allowlist(self):
        ts = t._transport_security(t.DEFAULT_HOST, t.DEFAULT_PORT, env={})
        self.assertEqual(
            ts.allowed_hosts,
            ["127.0.0.1:*", "localhost:*", "[::1]:*",
             "%s:%d" % (t.DEFAULT_HOST, t.DEFAULT_PORT)])

    def test_duplicate_of_the_bound_address_is_not_repeated(self):
        ts = t._transport_security(
            t.DEFAULT_HOST, t.DEFAULT_PORT,
            env={"MEM0_MCP_ALLOWED_HOSTS": "%s:%d" % (t.DEFAULT_HOST, t.DEFAULT_PORT)})
        self.assertEqual(len(ts.allowed_hosts), len(set(ts.allowed_hosts)))

    def test_origins_are_not_widened(self):
        """Only the Host allowlist opens up. A remote worker sends no Origin;
        widening origins would only help a browser attack us."""
        ts = t._transport_security(t.DEFAULT_HOST, t.DEFAULT_PORT,
                                   env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443"})
        self.assertEqual(ts.allowed_origins,
                         ["http://127.0.0.1:*", "http://localhost:*",
                          "http://[::1]:*"])

    def test_rebinding_protection_stays_on(self):
        ts = t._transport_security(t.DEFAULT_HOST, t.DEFAULT_PORT,
                                   env={"MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443"})
        self.assertTrue(ts.enable_dns_rebinding_protection)


class TestBuildServer(unittest.TestCase):
    """build_server decides admission; it must not quietly produce an
    unauthenticated HTTP server."""

    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            self.name = name
            self.kwargs = kwargs

    def test_stdio_gets_a_plain_server(self):
        s = t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"], env={})
        self.assertEqual(s.kwargs, {})

    def test_http_without_a_token_refuses(self):
        with self.assertRaises(t.ConfigError):
            t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"],
                           env={"MEM0_MCP_TRANSPORT": t.HTTP,
                                "MEM0_MCP_TOKEN_FILE": "/nonexistent/token"})

    def test_http_wires_a_verifier_and_auth_settings(self):
        s = t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"],
                           env={"MEM0_MCP_TRANSPORT": t.HTTP,
                                "MEM0_MCP_TOKEN": GOOD})
        self.assertIsNotNone(s.kwargs.get("token_verifier"))
        self.assertIsNotNone(s.kwargs.get("auth"))
        self.assertEqual(s.kwargs["host"], t.DEFAULT_HOST)

    def test_http_enables_dns_rebinding_protection(self):
        s = t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"],
                           env={"MEM0_MCP_TRANSPORT": t.HTTP,
                                "MEM0_MCP_TOKEN": GOOD})
        ts = s.kwargs["transport_security"]
        self.assertTrue(ts.enable_dns_rebinding_protection)
        self.assertIn("127.0.0.1:*", ts.allowed_hosts)

    def test_http_passes_the_allowlist_through(self):
        s = t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"],
                           env={"MEM0_MCP_TRANSPORT": t.HTTP,
                                "MEM0_MCP_TOKEN": GOOD,
                                "MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net:443"})
        self.assertIn("brain.ts.net:443",
                      s.kwargs["transport_security"].allowed_hosts)

    def test_malformed_allowlist_refuses_to_start(self):
        """Same posture as a missing token: refuse loudly rather than serve with
        a security setting that silently did not apply."""
        with self.assertRaises(t.ConfigError):
            t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"],
                           env={"MEM0_MCP_TRANSPORT": t.HTTP,
                                "MEM0_MCP_TOKEN": GOOD,
                                "MEM0_MCP_ALLOWED_HOSTS": "brain.ts.net"})

    def test_stdio_ignores_the_allowlist_entirely(self):
        """Nothing in HTTP admission control may reach the stdio path — a bad
        value there must not break the local registration."""
        s = t.build_server(self.FakeFastMCP, "mem0-local", argv=["x"],
                           env={"MEM0_MCP_ALLOWED_HOSTS": "garbage"})
        self.assertEqual(s.kwargs, {})


if __name__ == "__main__":
    unittest.main()
