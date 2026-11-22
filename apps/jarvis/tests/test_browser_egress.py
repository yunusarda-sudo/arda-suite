import base64
import http.client
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from arda.awareness import ProjectRegistry
from arda.browser_egress import (
    BrowserEgressAuthorizer,
    BrowserEgressDenied,
    BrowserEgressProxy,
    _ProxyHandler,
    resolve_public_addresses,
)
from arda.browser_sessions import BrowserSessionRegistry
from arda.core import Database


def _public_resolver(host, port, *, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.fixture
def grant_db(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    db = Database(db_path)
    db.migrate()
    ProjectRegistry(db).create("project-alpha", "Alpha")
    registry = BrowserSessionRegistry(db)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator")
    try:
        yield db, db_path, registry, grant
    finally:
        db.close()


def _proxy(db_path, *, resolver=_public_resolver, authorizer=None):
    proxy = BrowserEgressProxy(
        db_path, host="127.0.0.1", port=0,
        resolver=resolver, authorizer=authorizer,
    )
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    return proxy, thread


def _proxy_auth(user_id, token):
    value = base64.b64encode(f"{user_id}:{token}".encode()).decode()
    return "Basic " + value


def test_authorizer_checks_exact_live_grant_scope_and_user_identity(grant_db):
    db, db_path, registry, grant = grant_db
    authorizer = BrowserEgressAuthorizer(db_path)
    check = dict(user_id=grant["user_id"], grant_token=grant["session_id"], port=443)
    assert authorizer.authorize(host="example.com", **check)
    assert authorizer.authorize(host="sub.example.com", **check)
    assert not authorizer.authorize(host="evil-example.com", **check)
    assert not authorizer.authorize(host="example.com", **{**check, "user_id": "other"})
    assert not authorizer.authorize(host="example.com", **{**check, "grant_token": "wrong"})
    assert not authorizer.authorize(host="127.0.0.1", **check)
    registry.revoke(grant["session_id"], revoked_by="operator")
    assert not authorizer.authorize(host="example.com", **check)


def test_authorizer_denies_expired_grants(grant_db):
    db, db_path, registry, grant = grant_db
    now = datetime.now(timezone.utc)
    registry = BrowserSessionRegistry(db, clock=lambda: now)
    registry.revoke(grant["session_id"], revoked_by="operator")
    # The row is revoked in this fixture, so create a separate short-lived grant.
    fresh = registry.create("project-alpha", ["example.com"], created_by="operator", ttl_seconds=60)
    authorizer = BrowserEgressAuthorizer(db_path, clock=lambda: now + timedelta(seconds=61))
    assert not authorizer.authorize(
        user_id=fresh["user_id"], grant_token=fresh["session_id"], host="example.com", port=443
    )


def test_dns_answers_are_pinned_and_any_non_public_answer_denies():
    pinned = resolve_public_addresses("www.example.com", 443, resolver=_public_resolver)
    assert pinned == [(socket.AF_INET, socket.SOCK_STREAM, 6, ("93.184.216.34", 443))]

    def rebinding(_host, port, *, type):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    with pytest.raises(BrowserEgressDenied, match="non-public"):
        resolve_public_addresses("www.example.com", 443, resolver=rebinding)

    for address in ("127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "fd00::1"):
        def private_answer(_host, port, *, type, address=address):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (address, port, 0, 0) if family == socket.AF_INET6 else (address, port))]

        with pytest.raises(BrowserEgressDenied, match="non-public"):
            resolve_public_addresses("www.example.com", 443, resolver=private_answer)


def test_proxy_blocks_out_of_scope_target_before_dns_or_network(grant_db):
    _db, db_path, _registry, grant = grant_db
    calls = []

    def resolver(*args, **kwargs):
        calls.append((args, kwargs))
        return _public_resolver(*args, **kwargs)

    proxy, thread = _proxy(db_path, resolver=resolver)
    try:
        conn = http.client.HTTPConnection(*proxy.address, timeout=3)
        conn.putrequest("GET", "http://evil.example.net/private", skip_host=True)
        conn.putheader("Proxy-Authorization", _proxy_auth(grant["user_id"], grant["session_id"]))
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 403
        assert response.read() == b""
        assert calls == []
    finally:
        proxy.shutdown()
        thread.join(timeout=2)


def test_proxy_forwards_in_scope_http_without_forwarding_proxy_credentials(grant_db, monkeypatch):
    _db, db_path, _registry, grant = grant_db

    class Upstream:
        def __init__(self):
            self.sent = bytearray()
            self.response = bytearray(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

        def sendall(self, data):
            self.sent.extend(data)

        def recv(self, _size):
            if not self.response:
                return b""
            value = bytes(self.response)
            self.response.clear()
            return value

        def close(self):
            pass

    upstream = Upstream()
    connected = []
    monkeypatch.setattr(_ProxyHandler, "_connect_pinned", staticmethod(lambda addresses: connected.extend(addresses) or upstream))
    proxy, thread = _proxy(db_path)
    try:
        conn = http.client.HTTPConnection(*proxy.address, timeout=3)
        conn.putrequest("GET", "http://example.com/safe", skip_host=True)
        conn.putheader("Proxy-Authorization", _proxy_auth(grant["user_id"], grant["session_id"]))
        conn.putheader("Host", "evil.example.net")
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 200
        assert response.read() == b"ok"
        sent = bytes(upstream.sent)
        assert sent.startswith(b"GET /safe HTTP/1.1\r\n")
        assert b"Host: example.com\r\n" in sent
        assert b"Proxy-Authorization" not in sent
        assert connected == [(socket.AF_INET, socket.SOCK_STREAM, 6, ("93.184.216.34", 80))]
    finally:
        proxy.shutdown()
        thread.join(timeout=2)


def test_connect_tunnel_closes_when_core_revokes_grant(grant_db, monkeypatch):
    _db, db_path, registry, grant = grant_db
    remote, remote_peer = socket.socketpair()
    monkeypatch.setattr(_ProxyHandler, "_connect_pinned", staticmethod(lambda _addresses: remote))
    proxy, thread = _proxy(db_path)
    client = socket.create_connection(proxy.address, timeout=3)
    try:
        client.sendall(
            b"CONNECT example.com:443 HTTP/1.1\r\n"
            + f"Proxy-Authorization: {_proxy_auth(grant['user_id'], grant['session_id'])}\r\n\r\n".encode()
        )
        response = client.recv(1024)
        assert response.startswith(b"HTTP/1.1 200 Connection Established")
        client.sendall(b"before-revoke")
        remote_peer.settimeout(2)
        assert remote_peer.recv(64) == b"before-revoke"
        registry.revoke(grant["session_id"], revoked_by="operator")
        assert not proxy.server.authorizer.authorize(
            user_id=grant["user_id"], grant_token=grant["session_id"], host="example.com", port=443
        )
        client.settimeout(3)
        assert client.recv(1) == b""
        remote_peer.settimeout(1)
        assert remote_peer.recv(1) == b""
    finally:
        client.close()
        remote_peer.close()
        proxy.shutdown()
        thread.join(timeout=2)
