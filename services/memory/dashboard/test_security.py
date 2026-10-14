"""Regression tests for the dashboard's local-only HTTP security boundary."""

import http.client
import json
import sys
import threading
import unittest
from email.message import Message
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MEM0_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(MEM0_ROOT))

import server


class TestDashboardSecurityHelpers(unittest.TestCase):
    def test_bind_must_be_loopback(self):
        for host in ("0.0.0.0", "::", "192.168.1.20", "dashboard.example"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                server.validate_dashboard_host(host)

    def test_loopback_bind_values_are_accepted(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                self.assertIsNone(server.validate_dashboard_host(host))

    def test_host_header_must_be_local_and_use_expected_port(self):
        port = 8899
        for value in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            with self.subTest(value=value):
                self.assertTrue(server.is_local_host_header(value, port))
        for value in ("attacker.example:8899", "localhost.evil:8899",
                      "127.0.0.1:8900", "127.0.0.1"):
            with self.subTest(value=value):
                self.assertFalse(server.is_local_host_header(value, port))

    def test_origin_must_be_loopback_and_match_port(self):
        port = 8899
        for value in (f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                      f"http://[::1]:{port}"):
            with self.subTest(value=value):
                self.assertTrue(server.is_allowed_origin(value, port))
        for value in ("https://attacker.example:8899", "http://localhost.evil:8899",
                      "http://127.0.0.1:8900", "null", "file:///tmp/page"):
            with self.subTest(value=value):
                self.assertFalse(server.is_allowed_origin(value, port))

    def test_content_length_is_bounded_and_must_be_valid(self):
        cap = server.MAX_REQUEST_BODY_BYTES
        self.assertEqual(server.parse_content_length({"Content-Length": "12"}, cap), 12)
        for value in (str(cap + 1), "-1", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.parse_content_length({"Content-Length": value}, cap)

    def test_truncated_request_body_is_rejected(self):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = Message()
        handler.headers["Content-Length"] = "5"
        handler.rfile = BytesIO(b"{}")
        replies = []
        handler._send_json = lambda obj, status=200: replies.append((obj, status))

        result = handler._read_body()

        self.assertIsNone(result)
        self.assertEqual(replies[-1][1], 400)

    def test_oversized_request_body_is_rejected_before_reading(self):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = Message()
        handler.headers["Content-Length"] = str(server.MAX_REQUEST_BODY_BYTES + 1)
        handler.rfile = BytesIO(b"")  # Must not block waiting for the advertised body.
        replies = []
        handler._send_json = lambda obj, status=200: replies.append((obj, status))

        result = handler._read_body()

        self.assertIsNone(result)
        self.assertEqual(replies[-1][1], 413)


class TestHarnessRequestDirectory(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def _request(self, method, host, origin, path="/api/harness/run", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        if body is None:
            body = {"task": "safe test task", "cwd": "/etc"}
        conn.request(
            method, path, body=json.dumps(body),
            headers={
                "Host": host,
                "Origin": origin,
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.status

    def _post(self, host, origin):
        return self._request("POST", host, origin)

    def test_state_changing_api_rejects_foreign_host_or_origin(self):
        with patch.object(server, "run_harness_execution", return_value={"status": "blocked"}) as run:
            host_status = self._post("attacker.example", f"http://127.0.0.1:{self.port}")
            origin_status = self._post(
                f"127.0.0.1:{self.port}", "http://attacker.example"
            )

        self.assertIn(host_status, (403, 421))
        self.assertIn(origin_status, (403, 421))
        run.assert_not_called()

    def test_delete_and_patch_reject_cross_site_origins(self):
        local_host = f"127.0.0.1:{self.port}"
        foreign_origin = "http://attacker.example"
        delete_status = self._request(
            "DELETE", local_host, foreign_origin,
            path="/api/memory?id=memory-id",
        )
        patch_status = self._request(
            "PATCH", local_host, foreign_origin,
            path="/api/memory", body={"id": "memory-id", "text": "changed"},
        )
        self.assertEqual(delete_status, 403)
        self.assertEqual(patch_status, 403)

    def test_request_cannot_choose_harness_working_directory(self):
        with patch.object(server, "run_harness_execution", return_value={"status": "blocked"}) as run:
            status = self._request(
                "POST", f"127.0.0.1:{self.port}", f"http://127.0.0.1:{self.port}",
                body={"task": "safe test task", "cwd": "/etc",
                      "verify_command": "python3 -c 'raise SystemExit(0)'"},
            )
            self.assertEqual(status, 200)
            kwargs = run.call_args.kwargs
            self.assertNotIn("cwd", kwargs)
            self.assertNotIn("verify_command", kwargs)


if __name__ == "__main__":
    unittest.main()
