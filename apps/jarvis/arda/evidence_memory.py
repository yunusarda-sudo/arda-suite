"""Project-scoped, sourced, time-valid memory records for autonomous work.

This structured ledger complements (and does not replace) the existing semantic
memory service. It keeps its evidence boundary explicit and expires stale facts.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from arda.core import Database


class EvidenceMemoryError(ValueError):
    """Evidence memory input, identity, or persistence violated its contract."""


_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"), "[REDACTED_BEARER]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"), "[REDACTED_SECRET]"),
    (re.compile(r"(?i)\b(api[_-]?key|key|access[_-]?token|password)\s*[:=]\s*[^\s,;]{8,}"), r"\1=[REDACTED_SECRET]"),
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[ -]?)?\(?\d{3}\)?[ -]\d{3}[ -]\d{4}(?!\w)")
_TOKEN = re.compile(r"[a-z0-9_./-]{2,}")


def _timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceMemoryError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceMemoryError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceMemoryError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _safe_source_uri(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise EvidenceMemoryError("source URI must be HTTPS without credentials or query parameters")
    return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))


def sanitize_memory_text(value: str) -> str:
    """Redact high-confidence secrets, email addresses, and phone-like PII."""
    if not isinstance(value, str) or not value.strip() or len(value) > 32_000:
        raise EvidenceMemoryError("memory content must be non-empty and at most 32 KB")
    safe = value
    for pattern, replacement in _SECRET_PATTERNS:
        safe = pattern.sub(replacement, safe)
    safe = _EMAIL.sub("[REDACTED_EMAIL]", safe)
    safe = _PHONE.sub("[REDACTED_PHONE]", safe)
    return safe


@dataclass(frozen=True)
class EvidenceMemoryRecord:
    record_id: str
    project_id: str
    content: str
    source_uri: str
    source_sha256: str
    observed_at: str
    valid_until: str


@dataclass(frozen=True)
class MemoryRetrievalCase:
    project_id: str
    query: str
    expected_record_ids: tuple[str, ...]


class EvidenceMemoryStore:
    def __init__(self, db: Database):
        self.db = db
        if not db.table_exists("evidence_memories"):
            raise EvidenceMemoryError("database migrations are not current")

    def add(self, record: EvidenceMemoryRecord) -> str:
        if not record.record_id or not _PROJECT.fullmatch(record.project_id):
            raise EvidenceMemoryError("record_id and valid project_id are required")
        source_uri = _safe_source_uri(record.source_uri)
        if not _SHA256.fullmatch(record.source_sha256):
            raise EvidenceMemoryError("source_sha256 must be a lowercase SHA-256 digest")
        observed = _timestamp(record.observed_at, "observed_at")
        valid_until = _timestamp(record.valid_until, "valid_until")
        if valid_until <= observed:
            raise EvidenceMemoryError("valid_until must follow observed_at")
        content = sanitize_memory_text(record.content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = self.db.execute("SELECT * FROM evidence_memories WHERE record_id=?", (record.record_id,)).fetchone()
        if existing is not None:
            expected_identity = (record.project_id, content, source_uri, record.source_sha256,
                                 content_hash, observed.isoformat(), valid_until.isoformat())
            persisted_identity = (existing["project_id"], existing["content"], existing["source_uri"],
                                  existing["source_sha256"], existing["content_sha256"],
                                  existing["observed_at"], existing["valid_until"])
            if persisted_identity != expected_identity:
                raise EvidenceMemoryError("evidence memory identity conflict")
            return record.record_id
        self.db.execute(
            """INSERT INTO evidence_memories(
                record_id, project_id, content, source_uri, source_sha256, content_sha256,
                observed_at, valid_until, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.record_id, record.project_id, content, source_uri, record.source_sha256,
             content_hash, observed.isoformat(), valid_until.isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        return record.record_id

    def recall(self, project_id: str, query: str, *, now: str | None = None, limit: int = 5) -> tuple[dict[str, Any], ...]:
        if not _PROJECT.fullmatch(project_id) or not query.strip() or limit < 1 or limit > 100:
            raise EvidenceMemoryError("project, query, and bounded result limit are required")
        current = _timestamp(now, "now") if now else datetime.now(timezone.utc)
        rows = self.db.execute(
            """SELECT * FROM evidence_memories WHERE project_id=? AND valid_until>?
               ORDER BY observed_at DESC, record_id LIMIT 500""",
            (project_id, current.isoformat()),
        ).fetchall()
        terms = {term for term in _TOKEN.findall(query.casefold()) if len(term) > 2}
        ranked = []
        for row in rows:
            words = {term for term in _TOKEN.findall(row["content"].casefold()) if len(term) > 2}
            if terms and not terms.intersection(words):
                continue
            score = len(terms.intersection(words)) / len(terms) if terms else 0.0
            ranked.append((score, row["observed_at"], row["record_id"], dict(row)))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return tuple({**item[3], "score": item[0]} for item in ranked[:limit])


def evaluate_memory_retrieval(store: EvidenceMemoryStore, cases: Iterable[MemoryRetrievalCase], *, limit: int = 5) -> dict[str, Any]:
    """Deterministic fixture evaluation against an explicit no-memory baseline."""
    total_expected = total_retrieved = correct = false_recall = no_memory_correct = 0
    case_count = 0
    for case in cases:
        case_count += 1
        expected = set(case.expected_record_ids)
        results = store.recall(case.project_id, case.query, limit=limit)
        returned = {str(item["record_id"]) for item in results}
        total_expected += len(expected)
        total_retrieved += len(returned)
        correct += len(expected & returned)
        false_recall += len(returned - expected)
        # The baseline intentionally receives no memory records.
        no_memory_correct += 0
    precision = correct / total_retrieved if total_retrieved else 1.0
    recall = correct / total_expected if total_expected else 1.0
    return {
        "cases": case_count,
        "precision_at_k": round(precision, 6),
        "recall_at_k": round(recall, 6),
        "false_recall_count": false_recall,
        "no_memory_baseline_recall": round(no_memory_correct / total_expected, 6) if total_expected else 1.0,
        "memory_recall_lift": round(recall - (no_memory_correct / total_expected if total_expected else 1.0), 6),
        "limit": limit,
    }
