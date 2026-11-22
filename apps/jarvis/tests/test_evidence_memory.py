from __future__ import annotations

import hashlib

import pytest

from arda.core import Database
from arda.evidence_memory import (
    EvidenceMemoryError,
    EvidenceMemoryRecord,
    EvidenceMemoryStore,
    MemoryRetrievalCase,
    evaluate_memory_retrieval,
)


def record(record_id, project, content, *, valid_until="2027-01-01T00:00:00Z", source="https://example.test/docs"):
    return EvidenceMemoryRecord(
        record_id=record_id,
        project_id=project,
        content=content,
        source_uri=source,
        source_sha256="a" * 64,
        observed_at="2026-01-01T00:00:00Z",
        valid_until=valid_until,
    )


@pytest.fixture
def memory_db(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    yield db
    db.close()


def test_evidence_memory_is_project_scoped_and_expires_stale_records(memory_db):
    store = EvidenceMemoryStore(memory_db)
    store.add(record("fresh", "arda", "Use Python 3.12 for the Pi worker."))
    store.add(record("other-project", "opvia", "Use Python 3.12 for the Pi worker."))
    store.add(record("expired", "arda", "Use Python 3.11 for the Pi worker.",
                     valid_until="2026-02-01T00:00:00Z"))

    results = store.recall("arda", "Pi worker Python", now="2026-06-01T00:00:00Z")

    assert [item["record_id"] for item in results] == ["fresh"]
    assert results[0]["source_uri"] == "https://example.test/docs"


def test_memory_redacts_secrets_and_pii_before_persistence(memory_db):
    store = EvidenceMemoryStore(memory_db)
    store.add(record("redacted", "arda", "Contact dev@example.com or +90 555 123 4567; key=abcdefghijk12345"))

    row = memory_db.execute("SELECT content, content_sha256 FROM evidence_memories WHERE record_id='redacted'").fetchone()
    assert "dev@example.com" not in row["content"]
    assert "+90 555 123 4567" not in row["content"]
    assert "abcdefghijk12345" not in row["content"]
    assert row["content_sha256"] == hashlib.sha256(row["content"].encode()).hexdigest()


@pytest.mark.parametrize("bad", [
    record("query-secret", "arda", "fact", source="https://example.test/docs?token=secret"),
    record("bad-project", "ARDA", "fact"),
    record("naive-date", "arda", "fact").__class__(
        "naive-date", "arda", "fact", "https://example.test/docs", "a" * 64,
        "2026-01-01T00:00:00", "2027-01-01T00:00:00Z"),
])
def test_memory_rejects_unscoped_or_unsafe_evidence(memory_db, bad):
    with pytest.raises(EvidenceMemoryError):
        EvidenceMemoryStore(memory_db).add(bad)


def test_memory_records_are_idempotent_and_conflicts_fail(memory_db):
    store = EvidenceMemoryStore(memory_db)
    original = record("stable", "arda", "reproducible result")
    assert store.add(original) == store.add(original)
    with pytest.raises(EvidenceMemoryError, match="identity conflict"):
        store.add(record("stable", "arda", "different result"))
    with pytest.raises(EvidenceMemoryError, match="identity conflict"):
        store.add(record("stable", "arda", "reproducible result", valid_until="2028-01-01T00:00:00Z"))
    with pytest.raises(Exception, match="append-only"):
        memory_db.execute("DELETE FROM evidence_memories WHERE record_id='stable'")


def test_retrieval_report_compares_accuracy_to_empty_no_memory_baseline(memory_db):
    store = EvidenceMemoryStore(memory_db)
    store.add(record("python", "arda", "The Pi executor uses Python 3.12."))
    store.add(record("hermes", "arda", "Hermes is isolated with rootless Podman."))
    report = evaluate_memory_retrieval(store, [
        MemoryRetrievalCase("arda", "Pi executor Python", ("python",)),
        MemoryRetrievalCase("arda", "Hermes rootless Podman", ("hermes",)),
    ])

    assert report["cases"] == 2
    assert report["precision_at_k"] == 1.0
    assert report["recall_at_k"] == 1.0
    assert report["false_recall_count"] == 0
    assert report["no_memory_baseline_recall"] == 0.0
    assert report["memory_recall_lift"] == 1.0
