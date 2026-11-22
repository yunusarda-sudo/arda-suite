"""Fixed transport probe never starts or completes an unexpected claimed job."""
import pytest
from arda.core import Database, JobQueue
from arda.worker_runtime import WorkerRegistry
from arda.worker_http import WorkerHTTPServer


def test_smoke_requires_explicit_fixture_claim_before_start(tmp_path):
    from arda.worker_transport_cli import smoke
    db = Database(tmp_path / 'core.sqlite3')
    db.migrate()
    WorkerRegistry(db).register('mac', 'secret', public_key='fixture', capabilities=['test'])
    job = JobQueue(db).enqueue('test', 'transport fixture', idempotency_key='smoke')
    server = WorkerHTTPServer(db)
    server.start()
    try:
        with pytest.raises(ValueError, match='fixture'):
            smoke(server.url, 'mac', 'secret', 'other-job', 'probe')
        assert JobQueue(db).get(job.id).status == 'leased'
    finally:
        server.stop()
        db.close()


def test_smoke_lifecycle_and_repeat_replay(tmp_path):
    from arda.worker_transport_cli import smoke
    db = Database(tmp_path / 'core.sqlite3')
    db.migrate()
    WorkerRegistry(db).register('mac', 'secret', public_key='fixture', capabilities=['test'])
    job = JobQueue(db).enqueue('test', 'transport fixture', idempotency_key='smoke')
    server = WorkerHTTPServer(db)
    server.start()
    try:
        first = smoke(server.url, 'mac', 'secret', job.id, 'probe')
        assert first['complete']['status'] == 'succeeded'
        assert first['upload_artifact']['verified'] is True
        assert smoke(server.url, 'mac', 'secret', job.id, 'probe') == first
        assert db.scalar('SELECT COUNT(*) FROM artifacts') == 1
    finally:
        server.stop()
        db.close()
