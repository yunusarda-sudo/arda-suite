"""M2.2 localhost HTTP transport integration tests."""

import base64
import hashlib
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from arda.core import Database, JobQueue
from arda.worker_http import (
    HttpWorkerTransport,
    ProtocolTransportError,
    WorkerHTTPServer,
)
from arda.worker_protocol import ProtocolRequest, ProtocolUnavailableError, WorkerProtocolClient
from arda.worker_runtime import WorkerRegistry


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    WorkerRegistry(database).register(
        "mac-primary", "worker-secret", public_key="mac-public-key", capabilities=["pytest"]
    )
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def server(db):
    http = WorkerHTTPServer(db, host="127.0.0.1", port=0)
    http.start()
    try:
        yield http
    finally:
        http.stop()


def _client(server):
    return WorkerProtocolClient(
        "mac-primary", "worker-secret", HttpWorkerTransport(server.url, timeout_seconds=2)
    )


def test_http_rejects_worker_capability_escalation(db, server):
    from arda.worker_http import ProtocolTransportError

    job = JobQueue(db).enqueue("autonomous-coding", "must remain queued",
                               idempotency_key="http-capability-deny")
    with pytest.raises(ProtocolTransportError) as error:
        _client(server).request("claim", {"job_type": "autonomous-coding"},
                                request_id="http-unauthorized-claim")
    assert error.value.status_code == 403
    assert JobQueue(db).get(job.id).status == "queued"


def test_real_local_http_lifecycle_preserves_core_authority(db, server):
    queue = JobQueue(db)
    job = queue.enqueue("test", "HTTP fixture", idempotency_key="http-lifecycle")
    client = _client(server)

    assert client.request("ping", request_id="http-ping")["status"] == "ok"
    claimed = client.request("claim", request_id="http-claim")
    client.request(
        "start", {"job_id": job.id, "lease_token": claimed["lease_token"]}, request_id="http-start"
    )
    heartbeat = client.request(
        "heartbeat", {"job_id": job.id, "lease_token": claimed["lease_token"]},
        request_id="http-heartbeat",
    )
    assert heartbeat["status"] == "running"
    assert heartbeat["lease_token"] == claimed["lease_token"]
    content = b"http report"
    client.request(
        "upload_artifact",
        {
            "job_id": job.id,
            "lease_token": claimed["lease_token"],
            "kind": "test-report.json",
            "path_or_ref": "test-report.json",
            "content_b64": base64.b64encode(content).decode("ascii"),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        request_id="http-upload",
    )
    completed = client.request(
        "complete",
        {"job_id": job.id, "lease_token": claimed["lease_token"], "result": {"ok": True}},
        request_id="http-complete",
    )

    assert completed["status"] == "succeeded"
    assert queue.get(job.id).status == "succeeded"


@pytest.mark.parametrize("location", ["envelope", "payload"])
def test_duplicate_request_keys_are_rejected_before_core_dispatch(db, server, location):
    import http.client

    job = JobQueue(db).enqueue("test", "duplicate field probe", idempotency_key="dup-request")
    request = ProtocolRequest.create("mac-primary", "test-" + "worker", "ping",
                                     {"choice": "ok"}, request_id="dup-request")
    body = json.dumps(request.__dict__, separators=(",", ":"))
    if location == "envelope":
        body = '{"request_id":"discarded",' + body[1:]
    else:
        body = body.replace('"payload":{"choice":"ok"}',
                            '"payload":{"choice":"bad","choice":"ok"}')
    connection = http.client.HTTPConnection("127.0.0.1", urlsplit(server.url).port, timeout=2)
    try:
        connection.request("POST", "/worker/protocol", body=body.encode(),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        assert db.scalar("SELECT COUNT(*) FROM idempotency_keys WHERE key LIKE 'worker-protocol:%'") == 0
        assert JobQueue(db).get(job.id).status == "queued"
    finally:
        connection.close()


def test_response_loss_retry_replays_without_second_completion(db, server):
    job = JobQueue(db).enqueue("test", "Response loss", idempotency_key="http-response-loss")
    real_transport = HttpWorkerTransport(server.url, timeout_seconds=2)
    dropped = {"done": False}

    class DropAfterDelivery:
        def send(self, request):
            result = real_transport.send(request)
            if not dropped["done"]:
                dropped["done"] = True
                raise ProtocolUnavailableError("response lost after server commit")
            return result

    client = WorkerProtocolClient("mac-primary", "worker-secret", DropAfterDelivery())
    with pytest.raises(ProtocolUnavailableError):
        client.request("claim", request_id="http-retry-claim")
    replay = client.request("claim", request_id="http-retry-claim")

    assert replay["job_id"] == job.id
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE status='leased'") == 1


def test_replay_survives_http_server_restart(db):
    job = JobQueue(db).enqueue("test", "Restart replay", idempotency_key="http-restart")
    first = WorkerHTTPServer(db, host="127.0.0.1", port=0)
    first.start()
    try:
        first_response = _client(first).request("claim", request_id="http-restart-claim")
    finally:
        first.stop()

    second = WorkerHTTPServer(db, host="127.0.0.1", port=0)
    second.start()
    try:
        replay = _client(second).request("claim", request_id="http-restart-claim")
    finally:
        second.stop()

    assert replay == first_response
    assert replay["job_id"] == job.id


def test_http_auth_and_size_limits_are_enforced(db, server):
    bad = WorkerProtocolClient(
        "mac-primary", "wrong-secret", HttpWorkerTransport(server.url, timeout_seconds=2)
    )
    with pytest.raises(ProtocolTransportError) as error:
        bad.request("ping", request_id="http-bad-auth")
    assert error.value.status_code == 401

    server.stop()
    limited = WorkerHTTPServer(db, host="127.0.0.1", port=0, max_request_bytes=128)
    limited.start()
    try:
        with pytest.raises(ProtocolTransportError) as error:
            _client(limited).request("ping", {"padding": "x" * 500}, request_id="http-too-large")
        assert error.value.status_code == 413
    finally:
        limited.stop()


@pytest.mark.parametrize("host", ["192.168.1.20", "0.0.0.0", "example.com"])
def test_cleartext_server_rejects_non_loopback_binding(db, host):
    with pytest.raises(ValueError):
        WorkerHTTPServer(db, host=host)


@pytest.mark.parametrize("url", ["http://192.168.1.20:8000", "http://example.com", "ftp://127.0.0.1"])
def test_client_rejects_cleartext_non_loopback_and_unsupported_scheme(url):
    with pytest.raises(ValueError):
        HttpWorkerTransport(url)


@pytest.mark.parametrize("body", [b"[]", b"null", b'{"request_id":123}', b"not-json"])
def test_malformed_envelope_returns_400_without_core_side_effects(db, server, body):
    from http.client import HTTPConnection
    connection = HTTPConnection("127.0.0.1", urlsplit(server.url).port, timeout=2)
    try:
        connection.request("POST", "/worker/protocol", body=body,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        assert db.scalar("SELECT COUNT(*) FROM idempotency_keys") == 0
    finally:
        connection.close()


@pytest.mark.parametrize("headers", ["Content-Length: nope", "Content-Length: -1",
                                      "Content-Length: 2\r\nContent-Length: 3",
                                      "Transfer-Encoding: chunked"])
def test_malformed_framing_returns_400(server, headers):
    with socket.create_connection(("127.0.0.1", urlsplit(server.url).port), timeout=2) as connection:
        connection.sendall(("POST /worker/protocol HTTP/1.1\r\nHost: localhost\r\n" + headers +
                            "\r\nConnection: close\r\n\r\n{}").encode())
        response = connection.recv(4096)
    assert b" 400 " in response.split(b"\r\n", 1)[0]


def test_duplicate_claim_replays_after_worker_client_restart(db, server):
    job = JobQueue(db).enqueue("test", "worker restart", idempotency_key="worker-restart")
    first = _client(server).request("claim", request_id="worker-restart-claim")
    restarted = _client(server)
    assert restarted.request("claim", request_id="worker-restart-claim") == first
    assert JobQueue(db).get(job.id).attempt_count == 1
    with pytest.raises(ProtocolTransportError) as error:
        restarted.request("claim", {"changed": True}, request_id="worker-restart-claim")
    assert error.value.status_code == 409


@pytest.mark.parametrize("message", ["start", "heartbeat", "complete"])
def test_stale_lease_requests_do_not_mutate_core_job(db, server, message):
    queue = JobQueue(db)
    job = queue.enqueue("test", "stale", idempotency_key="stale")
    client = _client(server)
    client.request("claim", request_id="stale-claim")
    before = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
    with pytest.raises(ProtocolTransportError) as error:
        client.request(message, {"job_id": job.id, "lease_token": "old-fencing-token"},
                       request_id=f"stale-{message}")
    assert error.value.status_code == 409
    assert dict(db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()) == dict(before)


def test_hash_mismatch_and_missing_artifact_block_completion(db, server):
    queue = JobQueue(db)
    job = queue.enqueue("test", "hash", idempotency_key="hash")
    client = _client(server)
    claimed = client.request("claim", request_id="hash-claim")
    payload = {"job_id": job.id, "lease_token": claimed["lease_token"]}
    client.request("start", payload, request_id="hash-start")
    with pytest.raises(ProtocolTransportError) as error:
        client.request("upload_artifact", dict(payload, kind="report", path_or_ref="report",
                       content_b64=base64.b64encode(b"actual").decode(), sha256="0" * 64),
                       request_id="hash-upload")
    assert error.value.status_code == 400
    assert db.scalar("SELECT COUNT(*) FROM artifacts") == 0
    with pytest.raises(ProtocolTransportError) as error:
        client.request("complete", payload, request_id="hash-complete")
    assert error.value.status_code == 400
    assert queue.get(job.id).status == "running"


@contextmanager
def _socket_endpoint(handler):
    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    endpoint.daemon_threads = True
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{endpoint.server_address[1]}"
    finally:
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_does_not_forward_worker_secret(status):
    received = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_GET

        def log_message(self, *args):
            pass

    with _socket_endpoint(Sink) as sink_url:
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(status)
                self.send_header("Location", sink_url + "/capture")
                self.end_headers()

            def log_message(self, *args):
                pass

        with _socket_endpoint(Redirect) as redirect_url:
            client = WorkerProtocolClient("mac-primary", "worker-secret",
                                          HttpWorkerTransport(redirect_url, max_retries=0))
            with pytest.raises(ProtocolTransportError) as error:
                client.request("ping", request_id="redirect")
            assert error.value.status_code == status
    assert received == []


def test_socket_timeout_reports_unavailable_without_claiming_job(db):
    entered = threading.Event()
    release = threading.Event()

    class Hold(BaseHTTPRequestHandler):
        def do_POST(self):
            entered.set()
            release.wait(timeout=5)
            self.close_connection = True

        def log_message(self, *args):
            pass

    job = JobQueue(db).enqueue("test", "timeout", idempotency_key="timeout")
    with _socket_endpoint(Hold) as url:
        client = WorkerProtocolClient("mac-primary", "worker-secret",
            HttpWorkerTransport(url, timeout_seconds=0.05, max_retries=0))
        try:
            with pytest.raises(ProtocolUnavailableError):
                client.request("claim", request_id="timeout-claim")
            assert entered.wait(timeout=2)
            assert JobQueue(db).get(job.id).status == "queued"
        finally:
            release.set()


def test_response_lost_on_socket_after_core_commit_replays_claim(db, server):
    forwarded = HttpWorkerTransport(server.url, max_retries=0)
    deliveries = []

    class DropCommittedResponse(BaseHTTPRequestHandler):
        def do_POST(self):
            wire = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            request = ProtocolRequest(**wire)
            deliveries.append(forwarded.send(request))
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            self.close_connection = True

        def log_message(self, *args):
            pass

    job = JobQueue(db).enqueue("test", "lost response", idempotency_key="socket-loss")
    with _socket_endpoint(DropCommittedResponse) as url:
        client = WorkerProtocolClient("mac-primary", "worker-secret",
                                      HttpWorkerTransport(url, max_retries=0))
        with pytest.raises(ProtocolUnavailableError):
            client.request("claim", request_id="socket-loss-claim")
    replay = _client(server).request("claim", request_id="socket-loss-claim")
    assert replay == deliveries[0]
    assert replay["job_id"] == job.id
    assert JobQueue(db).get(job.id).attempt_count == 1


def test_connection_drop_before_dispatch_leaves_job_queued(db):
    class Drop(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            self.close_connection = True

        def log_message(self, *args):
            pass

    job = JobQueue(db).enqueue("test", "connection drop", idempotency_key="drop")
    with _socket_endpoint(Drop) as url:
        client = WorkerProtocolClient("mac-primary", "worker-secret",
                                      HttpWorkerTransport(url, max_retries=0))
        with pytest.raises(ProtocolUnavailableError):
            client.request("claim", request_id="drop-claim")
    assert JobQueue(db).get(job.id).status == "queued"
    assert db.scalar("SELECT COUNT(*) FROM idempotency_keys WHERE key LIKE 'worker-protocol:%'") == 0


def test_partial_request_body_is_bounded_by_server_timeout(db):
    server = WorkerHTTPServer(db, request_timeout_seconds=0.05)
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", urlsplit(server.url).port), timeout=2) as connection:
            connection.sendall(b"POST /worker/protocol HTTP/1.1\r\nHost: localhost\r\n"
                               b"Content-Length: 100\r\n\r\n{")
            response = connection.recv(4096)
        assert response == b"" or b" 408 " in response.split(b"\r\n", 1)[0]
        assert db.scalar("SELECT COUNT(*) FROM idempotency_keys") == 0
    finally:
        server.stop()


def test_duplicate_content_length_rejects_valid_signed_envelope(db, server):
    from dataclasses import asdict
    body = json.dumps(asdict(ProtocolRequest.create(
        "mac-primary", "worker-secret", "ping", request_id="ambiguous-length"))).encode()
    framing = (f"POST /worker/protocol HTTP/1.1\r\nHost: localhost\r\n"
               f"Content-Length: {len(body)}\r\nContent-Length: {len(body) + 1}\r\n"
               "Connection: close\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", urlsplit(server.url).port), timeout=2) as connection:
        connection.sendall(framing + body)
        response = connection.recv(4096)
    assert b" 400 " in response.split(b"\r\n", 1)[0]
    assert db.scalar("SELECT COUNT(*) FROM idempotency_keys") == 0
