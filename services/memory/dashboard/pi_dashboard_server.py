#!/usr/bin/env python3
"""Loopback-only Pickable Pi monitor and Hermes chat dashboard."""

import ipaddress
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pi_bridge import BusyError, ask_hermes, get_status  # noqa: E402
from pi_page import PI_PAGE_HTML  # noqa: E402

HOST = os.environ.get("PI_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("PI_DASHBOARD_PORT", "8890"))
MAX_BODY = 32_768


def _loopback_host(value):
    try:
        return value == "localhost" or ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _valid_host_header(value):
    try:
        parsed = urlsplit("//" + (value or ""))
        return (not parsed.username and not parsed.password and not parsed.path
                and not parsed.query and not parsed.fragment
                and _loopback_host(parsed.hostname or "") and parsed.port == PORT)
    except ValueError:
        return False


def _valid_origin(value):
    try:
        parsed = urlsplit(value or "")
        return (parsed.scheme == "http" and parsed.path in ("", "/")
                and not parsed.query and not parsed.fragment
                and _valid_host_header(parsed.netloc))
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "PiControl/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _local(self, origin=False):
        if not _valid_host_header(self.headers.get("Host", "")):
            self._json({"error": "loopback dashboard requests only"}, 421)
            return False
        value = self.headers.get("Origin")
        if origin and value is not None and not _valid_origin(value):
            self._json({"error": "cross-origin request rejected"}, 403)
            return False
        return True

    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, error):
        if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
            return self._json({"error": str(error) or "Pi request timed out"}, 504)
        if isinstance(error, BusyError):
            return self._json({"error": str(error)}, 409)
        if isinstance(error, (OSError, ValueError)):
            return self._json({"error": str(error)}, 503)
        return self._json({"error": "Pi dashboard request failed"}, 500)

    def do_GET(self):
        if not self._local():
            return
        path = urlsplit(self.path).path
        if path in ("/", "/pi"):
            body = PI_PAGE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; "
                             "base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/pi/status":
            try:
                return self._json(get_status())
            except Exception as exc:
                return self._error(exc)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local(origin=True):
            return
        if urlsplit(self.path).path != "/api/pi/chat":
            return self._json({"error": "not found"}, 404)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
            return self._json({"error": "invalid request framing"}, 400)
        if not lengths[0].isascii() or not lengths[0].isdecimal():
            return self._json({"error": "invalid content length"}, 400)
        size = int(lengths[0])
        if size > MAX_BODY:
            return self._json({"error": "request body is too large"}, 413)
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json({"error": "invalid JSON body"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "JSON body must be an object"}, 400)
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            return self._json({"error": "message is required"}, 400)
        if len(message) > 6000:
            return self._json({"error": "message exceeds 6000 characters"}, 413)
        history = body.get("history", [])
        if not isinstance(history, list) or len(history) > 12:
            return self._json({"error": "history must contain at most 12 messages"}, 400)
        for item in history:
            if (not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
                    or not isinstance(item.get("content"), str) or len(item["content"]) > 2000):
                return self._json({"error": "invalid conversation history"}, 400)
        try:
            return self._json(ask_hermes(message.strip(), history))
        except Exception as exc:
            return self._error(exc)

    def log_message(self, fmt, *args):
        sys.stderr.write("[pi-dashboard] " + fmt % args + "\n")


def main():
    if not _loopback_host(HOST):
        raise SystemExit("PI_DASHBOARD_HOST must be localhost or a loopback IP")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Pi Control Dashboard → http://{HOST}:{PORT}/pi", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
