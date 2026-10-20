"""Grant-aware HTTP CONNECT proxy for isolated Camofox sessions.

Run this process on the trusted ARDA host. The browser container should be on
an internal-only Podman network and reach this listener through its host gateway;
the proxy itself is not a replacement for that network isolation.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
import json
import logging
import select
import socket
import socketserver
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from arda.browser_sessions import host_in_scope
from arda.core import runtime_database_path

log = logging.getLogger(__name__)

MAX_REQUEST_BODY = 16 * 1024 * 1024
MAX_TUNNELS = 128
IO_TIMEOUT_SECONDS = 30
AUTH_RECHECK_SECONDS = 1


class BrowserEgressDenied(PermissionError):
    """The current grant does not authorize the requested proxy destination."""


class BrowserEgressAuthorizer:
    """Read-only, live grant lookup; it never migrates or writes Core state."""

    def __init__(self, db_path: str | Path, *, clock: Callable[[], datetime] | None = None):
        self.db_path = Path(db_path).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def authorize(self, *, user_id: str, grant_token: str, host: str, port: int) -> bool:
        if port not in {80, 443} or not host or not grant_token or not user_id:
            return False
        try:
            uri = self.db_path.as_uri() + "?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=0.5)
            try:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    """SELECT session_id, user_id, domains_json, expires_at, revoked_at, isolated
                       FROM browser_session_grants WHERE user_id=? AND isolated=1""",
                    (user_id,),
                ).fetchone()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return False
        if row is None or row["revoked_at"] is not None:
            return False
        if not hmac.compare_digest(str(row["session_id"]), grant_token):
            return False
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if row["expires_at"] <= now.astimezone(timezone.utc).isoformat():
            return False
        try:
            domains = json.loads(row["domains_json"])
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(domains, list) and host_in_scope(host, domains)


def resolve_public_addresses(host: str, port: int, *, resolver=socket.getaddrinfo) -> list[tuple]:
    """Resolve once, reject any unsafe answer, and return pinned socket addresses."""
    if not host or host.endswith(".") or not host_in_scope(host, (host,)):
        raise BrowserEgressDenied("invalid DNS host")
    try:
        results = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BrowserEgressDenied("DNS resolution failed") from exc
    destinations: list[tuple] = []
    seen: set[tuple] = set()
    if not results:
        raise BrowserEgressDenied("DNS returned no addresses")
    for family, socktype, proto, _canonname, sockaddr in results:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise BrowserEgressDenied("DNS returned an invalid address") from exc
        if not address.is_global:
            raise BrowserEgressDenied("DNS resolved to a non-public address")
        key = (family, socktype, proto, sockaddr)
        if key not in seen:
            destinations.append(key)
            seen.add(key)
    return destinations


class _ThreadingProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, handler, *, authorizer, resolver=socket.getaddrinfo):
        self.authorizer = authorizer
        self.resolver = resolver
        self._slots = threading.BoundedSemaphore(MAX_TUNNELS)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Proxy capacity reached\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ARDA-Egress"
    sys_version = ""

    def log_message(self, _format, *_args):
        # Proxy credentials, full paths, and page data do not enter logs.
        return

    def setup(self):
        super().setup()
        self.connection.settimeout(IO_TIMEOUT_SECONDS)

    def handle_expect_100(self):
        self.send_error(417, "Expect is not supported by the policy proxy")
        self.close_connection = True
        return False

    def _credentials(self) -> tuple[str, str]:
        value = self.headers.get("Proxy-Authorization", "")
        scheme, separator, encoded = value.partition(" ")
        if not separator or scheme.lower() != "basic":
            raise BrowserEgressDenied("proxy authentication required")
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise BrowserEgressDenied("proxy authentication required") from exc
        user_id, separator, token = decoded.partition(":")
        if not separator or not user_id or not token:
            raise BrowserEgressDenied("proxy authentication required")
        return user_id, token

    @staticmethod
    def _authority(value: str, default_port: int) -> tuple[str, int]:
        if not value or any(char in value for char in "@/?#\\"):
            raise BrowserEgressDenied("invalid proxy target")
        parsed = urlsplit("//" + value)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise BrowserEgressDenied("invalid proxy target")
        try:
            port = parsed.port or default_port
        except ValueError as exc:
            raise BrowserEgressDenied("invalid proxy target port") from exc
        host = (parsed.hostname or "").rstrip(".").lower()
        if not host or port not in {80, 443}:
            raise BrowserEgressDenied("proxy target is not allowed")
        return host, port

    def _grant(self, host: str, port: int) -> tuple[str, str]:
        user_id, token = self._credentials()
        if not self.server.authorizer.authorize(user_id=user_id, grant_token=token, host=host, port=port):
            raise BrowserEgressDenied("proxy destination denied")
        return user_id, token

    def _destinations(self, host: str, port: int) -> list[tuple]:
        return resolve_public_addresses(host, port, resolver=self.server.resolver)

    @staticmethod
    def _connect_pinned(destinations: list[tuple]) -> socket.socket:
        last_error: OSError | None = None
        for family, socktype, proto, sockaddr in destinations:
            remote = socket.socket(family, socktype, proto)
            remote.settimeout(IO_TIMEOUT_SECONDS)
            try:
                remote.connect(sockaddr)
                return remote
            except OSError as exc:
                last_error = exc
                remote.close()
        raise BrowserEgressDenied("could not connect to pinned public address") from last_error

    def _reject(self, status: int = 403) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        finally:
            self.close_connection = True

    def do_CONNECT(self):  # noqa: N802 - BaseHTTPRequestHandler API
        remote = None
        established = False
        self.close_connection = True
        try:
            host, port = self._authority(self.path, 443)
            user_id, token = self._grant(host, port)
            destinations = self._destinations(host, port)
            remote = self._connect_pinned(destinations)
            self.send_response(200, "Connection Established")
            self.end_headers()
            established = True
            self._tunnel(remote, user_id, token, host, port)
        except (BrowserEgressDenied, OSError, TimeoutError):
            if not established and not self.wfile.closed:
                self._reject()
        finally:
            if remote is not None:
                remote.close()

    def _tunnel(self, remote: socket.socket, user_id: str, token: str, host: str, port: int) -> None:
        checked_at = time.monotonic()
        while True:
            if time.monotonic() - checked_at >= AUTH_RECHECK_SECONDS:
                if not self.server.authorizer.authorize(
                    user_id=user_id, grant_token=token, host=host, port=port
                ):
                    return
                checked_at = time.monotonic()
            try:
                readable, _, _ = select.select((self.connection, remote), (), (), 0.25)
            except (OSError, ValueError):
                return
            for source in readable:
                destination = remote if source is self.connection else self.connection
                try:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination.sendall(data)
                except OSError:
                    return

    def _absolute_http_target(self) -> tuple[str, int, str]:
        parsed = urlsplit(self.path)
        if parsed.scheme.lower() != "http" or not parsed.hostname or parsed.username or parsed.password:
            raise BrowserEgressDenied("only absolute HTTP proxy requests and CONNECT are supported")
        host = parsed.hostname.rstrip(".").lower()
        try:
            port = parsed.port or 80
        except ValueError as exc:
            raise BrowserEgressDenied("invalid proxy target port") from exc
        if port != 80:
            raise BrowserEgressDenied("only standard HTTP and HTTPS ports are allowed")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        return host, port, path

    def _forward_http(self) -> None:
        upstream = None
        response_started = False
        try:
            host, port, path = self._absolute_http_target()
            self._grant(host, port)
            destinations = self._destinations(host, port)
            length = self.headers.get("Content-Length", "0")
            if self.headers.get("Transfer-Encoding") or not length.isdigit():
                raise BrowserEgressDenied("unsupported request body framing")
            body_size = int(length)
            if body_size > MAX_REQUEST_BODY:
                self._reject(413)
                return
            body = self.rfile.read(body_size) if body_size else b""
            if len(body) != body_size:
                raise BrowserEgressDenied("incomplete request body")
            upstream = self._connect_pinned(destinations)
            request_line = f"{self.command} {path} {self.request_version}\r\n".encode("ascii")
            headers = []
            host_header = host
            for name, value in self.headers.items():
                if name.lower() in {
                    "proxy-authorization", "proxy-connection", "connection", "keep-alive",
                    "transfer-encoding", "host", "content-length",
                }:
                    continue
                headers.append(f"{name}: {value}\r\n".encode("latin-1"))
            headers.extend((
                f"Host: {host_header}\r\n".encode("latin-1"),
                f"Content-Length: {body_size}\r\n".encode("ascii"),
                b"Connection: close\r\n\r\n",
            ))
            upstream.sendall(request_line + b"".join(headers) + body)
            self.close_connection = True
            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                response_started = True
        except (BrowserEgressDenied, OSError, TimeoutError):
            if not response_started and not self.wfile.closed:
                self._reject()
        finally:
            if upstream is not None:
                upstream.close()

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()

    def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()

    def do_PATCH(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()

    def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward_http()


class BrowserEgressProxy:
    """Lifecycle wrapper used by the daemon and socket-level tests."""

    def __init__(self, db_path: str | Path, *, host: str = "127.0.0.1", port: int = 3129,
                 authorizer: BrowserEgressAuthorizer | None = None, resolver=socket.getaddrinfo):
        self.authorizer = authorizer or BrowserEgressAuthorizer(db_path)
        self.server = _ThreadingProxyServer(
            (host, port), _ProxyHandler, authorizer=self.authorizer, resolver=resolver
        )

    @property
    def address(self) -> tuple[str, int]:
        return self.server.server_address

    def serve_forever(self) -> None:
        self.server.serve_forever(poll_interval=0.2)

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARDA grant-scoped browser egress proxy")
    parser.add_argument("--db", type=Path, default=runtime_database_path())
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3129)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    proxy = BrowserEgressProxy(args.db, host=args.bind, port=args.port)
    log.info("ARDA browser egress proxy listening on %s:%s", *proxy.address)
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proxy.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover - command entry point
    raise SystemExit(main())
