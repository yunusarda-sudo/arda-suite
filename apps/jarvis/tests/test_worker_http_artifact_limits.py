"""Decoded artifact byte limits hold across base64 padding boundaries."""
import base64
import hashlib

import pytest

from arda.core import Database, JobQueue
from arda.worker_http import HttpWorkerTransport, ProtocolTransportError, WorkerHTTPServer
from arda.worker_protocol import WorkerProtocolClient
from arda.worker_runtime import WorkerRegistry


@pytest.mark.parametrize("limit", [1, 2, 3])
@pytest.mark.parametrize("enforced_by", ["client", "server"])
def test_artifact_byte_limit_accepts_exact_limit_and_rejects_next_byte(
    tmp_path, limit, enforced_by
):
    db = Database(tmp_path / "artifact-limit.sqlite3")
    db.migrate()
    WorkerRegistry(db).register("mac", "secret", public_key="fixture", capabilities=["test"])
    job = JobQueue(db).enqueue("test", "artifact limit", idempotency_key="artifact-limit")
    server = WorkerHTTPServer(db, max_artifact_bytes=limit if enforced_by == "server" else 100)
    server.start()
    try:
        client = WorkerProtocolClient("mac", "secret", HttpWorkerTransport(
            server.url, max_artifact_bytes=limit if enforced_by == "client" else 100
        ))
        claimed = client.request("claim", request_id="claim")
        lease = {"job_id": job.id, "lease_token": claimed["lease_token"]}
        client.request("start", lease, request_id="start")

        def upload(content, request_id):
            return client.request("upload_artifact", {
                **lease, "kind": "fixture", "path_or_ref": "fixture",
                "content_b64": base64.b64encode(content).decode("ascii"),
                "sha256": hashlib.sha256(content).hexdigest(),
            }, request_id=request_id)

        assert upload(b"x" * limit, "exact-limit")["verified"] is True
        with pytest.raises(ProtocolTransportError) as error:
            upload(b"x" * (limit + 1), "over-limit")
        assert error.value.status_code == 413
        assert db.scalar("SELECT COUNT(*) FROM artifacts") == 1
    finally:
        server.stop()
        db.close()
