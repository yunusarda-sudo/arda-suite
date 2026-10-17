"""HTTP transport adapter for the M2.1 worker protocol."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import ipaddress
import socket
import threading
from http.client import HTTPException
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener

from arda.core import Database, StaleLeaseError
from arda.worker_protocol import (
    ProtocolAuthenticationError,
    ProtocolReplayConflictError,
    ProtocolReplayInFlightError,
    ProtocolRequest,
    ProtocolUnavailableError,
    WorkerProtocolServer,
    WorkerCapabilityError,
    _canonical,
    _reject_duplicate_keys,
)


def _response_canonical(request_id: str, worker_id: str, result: Any) -> bytes:
    return _canonical(request_id, worker_id, "response", result)


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _private_interface(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
        if address.is_unspecified or address.is_multicast or address.is_reserved:
            return False
        if address.is_private:
            return True
        # Tailscale IPv4 addresses use CGNAT space (100.64.0.0/10).
        return address.version == 4 and ipaddress.ip_network("100.64.0.0/10").supernet_of(
            ipaddress.ip_network(f"{address}/32")
        )
    except ValueError:
        return False


def _artifact_size(payload: dict, limit: int) -> bool:
    encoded = payload.get("content_b64", "")
    if not isinstance(encoded, str):
        raise ValueError("artifact content must be base64 text")
    if len(encoded) > 4 * ((limit + 2) // 3):
        return False
    try:
        return len(base64.b64decode(encoded, validate=True)) <= limit
    except (binascii.Error, UnicodeError) as exc:
        raise ValueError("invalid base64 artifact") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProtocolTransportError(ConnectionError):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class HttpWorkerTransport:
    """POST-only transport; retrying the same request preserves request-id semantics."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10,
        max_retries: int = 2,
        max_request_bytes: int = 1_048_576,
        max_artifact_bytes: int = 512 * 1024,
        max_response_bytes: int = 1_048_576,
        allow_private_http: bool = False,
    ):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("invalid worker endpoint URL")
        if parsed.scheme == "http" and not _loopback(parsed.hostname) and not (
            allow_private_http and _private_interface(parsed.hostname)
        ):
            raise ValueError("cleartext HTTP requires a loopback SSH tunnel")
        if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0
                or any(type(n) is not int or n <= 0 for n in
                       (max_request_bytes, max_artifact_bytes, max_response_bytes))):
            raise ValueError("timeouts and size limits must be positive")
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.max_request_bytes = max_request_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.max_response_bytes = max_response_bytes

    def send(self, request: ProtocolRequest) -> dict[str, Any]:
        body = json.dumps({
            "request_id": request.request_id,
            "worker_id": request.worker_id,
            "message_type": request.message_type,
            "payload": request.payload,
            "auth_token": request.auth_token,
            "signature": request.signature,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if request.message_type == "upload_artifact":
            if not _artifact_size(request.payload, self.max_artifact_bytes):
                raise ProtocolTransportError("artifact exceeds client size limit", status_code=413)
        if len(body) > self.max_request_bytes:
            raise ProtocolTransportError("request exceeds client size limit", status_code=413)
        http_request = Request(
            f"{self.base_url}/worker/protocol",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._opener.open(http_request, timeout=self.timeout_seconds) as response:
                    if response.headers.get("Content-Length") and int(response.headers["Content-Length"]) > self.max_response_bytes:
                        raise ProtocolTransportError("response exceeds client size limit", status_code=502)
                    raw = response.read(self.max_response_bytes + 1)
                    if len(raw) > self.max_response_bytes:
                        raise ProtocolTransportError("response exceeds client size limit", status_code=502)
                try:
                    wire = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
                except (UnicodeError, ValueError) as exc:
                    raise ProtocolTransportError("malformed response", status_code=502) from exc
                if not isinstance(wire, dict) or not isinstance(wire.get("result"), dict) or not isinstance(wire.get("signature"), str):
                    raise ProtocolTransportError("invalid response envelope", status_code=502)
                if wire.get("request_id") != request.request_id or wire.get("worker_id") != request.worker_id:
                    raise ProtocolAuthenticationError("response identity mismatch")
                result = wire["result"]
                expected = hmac.new(
                    request.auth_token.encode("utf-8"),
                    _response_canonical(request.request_id, request.worker_id, result),
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(expected, wire.get("signature", "")):
                    raise ProtocolAuthenticationError("response signature mismatch")
                return result
            except HTTPError as exc:
                try:
                    body = exc.read(512).decode("utf-8", errors="replace")
                except OSError:
                    body = ""
                if exc.code in {502, 503, 504} and attempt < self.max_retries:
                    last_error = exc
                    continue
                raise ProtocolTransportError(body or "worker protocol request failed", status_code=exc.code) from exc
            except (ProtocolTransportError, ProtocolAuthenticationError):
                raise
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    continue
                raise ProtocolUnavailableError(str(exc)) from exc
        raise ProtocolUnavailableError(str(last_error or "worker transport unavailable"))


class WorkerHTTPServer:
    """Bounded HTTP endpoint delegating all decisions to WorkerProtocolServer."""

    def __init__(
        self,
        db: Database,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_request_bytes: int = 1_048_576,
        max_artifact_bytes: int = 512 * 1024,
        max_response_bytes: int = 1_048_576,
        request_timeout_seconds: float = 10,
        allow_private_http: bool = False,
    ):
        if not _loopback(host) and not (allow_private_http and _private_interface(host)):
            raise ValueError("worker endpoint must bind to loopback or explicit private interface")
        if (not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0
                or any(type(n) is not int or n <= 0 for n in
                       (max_request_bytes, max_artifact_bytes, max_response_bytes))):
            raise ValueError("timeouts and size limits must be positive")
        self.request_timeout_seconds = request_timeout_seconds
        self.db = db
        self.host = host
        self.max_request_bytes = max_request_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.max_response_bytes = max_response_bytes
        self.db_path = db.path
        self._httpd: ThreadingHTTPServer | None = None
        self._thread = None
        self.port = port

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("HTTP server is not started")
        return f"http://{self.host}:{self._httpd.server_address[1]}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        db_path = self.db_path
        max_request_bytes = self.max_request_bytes
        max_artifact_bytes = self.max_artifact_bytes
        max_response_bytes = self.max_response_bytes

        request_timeout_seconds = self.request_timeout_seconds

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(request_timeout_seconds)

            def do_POST(self):  # noqa: N802 - stdlib handler API
                if self.path != "/worker/protocol":
                    self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
                    return
                try:
                    lengths = self.headers.get_all("Content-Length", [])
                    if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                        raise ValueError("invalid content length")
                    content_length = int(lengths[0])
                    if content_length > max_request_bytes:
                        self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
                        return
                    raw = self.rfile.read(content_length)
                    if len(raw) != content_length:
                        raise ValueError("incomplete request body")
                    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
                    if (not isinstance(data, dict) or not isinstance(data.get("payload"), dict)
                            or any(not isinstance(data.get(key), str) or not data[key]
                                   for key in ("request_id", "worker_id", "message_type", "auth_token", "signature"))):
                        raise ValueError("invalid request envelope")
                    request = ProtocolRequest(
                        data["request_id"], data["worker_id"], data["message_type"],
                        data["payload"], data["auth_token"], data["signature"],
                    )
                    if request.message_type == "upload_artifact":
                        if not _artifact_size(request.payload, max_artifact_bytes):
                            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "artifact too large")
                            return
                    # sqlite3 connections are thread-bound; every HTTP request
                    # gets its own connection while sharing the same durable DB.
                    request_db = Database(db_path)
                    try:
                        result = WorkerProtocolServer(request_db).handle(request)
                    finally:
                        request_db.close()
                    body = json.dumps({
                        "request_id": request.request_id,
                        "worker_id": request.worker_id,
                        "result": result,
                        "signature": hmac.new(
                            request.auth_token.encode("utf-8"),
                            _response_canonical(request.request_id, request.worker_id, result),
                            hashlib.sha256,
                        ).hexdigest(),
                    }, separators=(",", ":")).encode("utf-8")
                    if len(body) > max_response_bytes:
                        self._error(HTTPStatus.BAD_GATEWAY, "response too large")
                        return
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return  # Delivery may have committed; replay remains Core-owned.
                except socket.timeout:
                    self._error(HTTPStatus.REQUEST_TIMEOUT, "request timeout")
                except WorkerCapabilityError as exc:
                    self._error(HTTPStatus.FORBIDDEN, str(exc))
                except (ProtocolAuthenticationError, PermissionError) as exc:
                    self._error(HTTPStatus.UNAUTHORIZED, str(exc))
                except (ProtocolReplayConflictError, ProtocolReplayInFlightError, StaleLeaseError) as exc:
                    self._error(HTTPStatus.CONFLICT, str(exc))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid worker request")
                except Exception as exc:  # noqa: BLE001 - endpoint must return structured failure
                    self._error(HTTPStatus.INTERNAL_SERVER_ERROR, exc.__class__.__name__)

            def _error(self, status: HTTPStatus, message: str):
                body = json.dumps({"error": message}).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format, *_args):
                return

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None
