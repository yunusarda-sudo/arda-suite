"""M2.3D worker path: live acquisition plus validated local-model synthesis."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from arda.live_research import LiveSourceAcquirer, SafeHTTPFetcher, SearchAdapter
from arda.research_worker import FixtureSourceAdapter, ResearchBudgetError, ResearchContractError, ResearchSourceError, ResearchTask, build_research, canonical_json
from arda.research_synthesis import ModelGateway, render_synthesis_markdown, synthesize_research
from arda.evidence_memory import EvidenceMemoryRecord, EvidenceMemoryStore
from arda.model_routes import RoutedModelGateway, SQLiteModelRouteStore, build_routed_gateway
from arda.core import Database
from arda.worker_protocol import WorkerProtocolClient


@dataclass(frozen=True)
class LiveResearchJob:
    project_id: str | None
    query: str
    questions: tuple[str, ...]
    max_sources: int
    time_budget_seconds: float
    source_constraints: dict[str, Any]
    required_output_format: tuple[str, ...]
    expected_claims: tuple[dict[str, str], ...]

    @classmethod
    def from_objective(cls, objective: str, *, project_id: str | None = None) -> "LiveResearchJob":
        try:
            raw = json.loads(objective)
        except (TypeError, ValueError) as exc:
            raise ResearchContractError("live research objective must be JSON") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("query"), str) or not raw["query"].strip():
            raise ResearchContractError("live research query is required")
        questions = raw.get("research_questions")
        if not isinstance(questions, list) or not questions or not all(isinstance(item, str) and item.strip() for item in questions):
            raise ResearchContractError("research_questions must be a non-empty list")
        try:
            max_sources = int(raw["max_sources"])
            budget = float(raw["time_budget_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchContractError("max_sources and time_budget_seconds are required") from exc
        if max_sources < 1 or budget <= 0:
            raise ResearchContractError("invalid live research limits")
        formats = raw.get("required_output_format")
        if set(formats or ()) != {"research.json", "research.md"}:
            raise ResearchContractError("required_output_format must contain research.json and research.md")
        constraints = raw.get("source_constraints", {})
        if not isinstance(constraints, dict):
            raise ResearchContractError("source_constraints must be an object")
        expected = raw.get("expected_claims", [])
        if not isinstance(expected, list) or not all(isinstance(item, dict) and isinstance(item.get("claim_id"), str) and isinstance(item.get("text"), str) for item in expected):
            raise ResearchContractError("expected_claims is malformed")
        return cls(project_id, raw["query"].strip(), tuple(item.strip() for item in questions), max_sources, budget, dict(constraints), tuple(formats), tuple({"claim_id": item["claim_id"].strip(), "text": item["text"].strip()} for item in expected))

    def objective_for_sources(self, source_refs: tuple[str, ...]) -> ResearchTask:
        return ResearchTask(self.questions, source_refs, self.source_constraints, len(source_refs), self.time_budget_seconds, self.required_output_format, self.expected_claims)


class LiveResearchWorker:
    """Run one live research job entirely through the existing Core protocol."""

    def __init__(self, client: WorkerProtocolClient, search: SearchAdapter, fetcher: SafeHTTPFetcher,
                 gateway: ModelGateway, *, model: str, max_output_bytes: int = 512 * 1024,
                 max_evidence: int = 6, max_excerpt_chars: int = 1_200,
                 max_contract_retries: int = 1,
                 evidence_memory: EvidenceMemoryStore | None = None,
                 memory_ttl_days: int = 30,
                 route_snapshot_hash: str | None = None):
        self.client = client
        self.search = search
        self.acquirer = LiveSourceAcquirer(fetcher)
        self.gateway = gateway
        self.model = model
        self.max_output_bytes = max_output_bytes
        self.max_evidence = max_evidence
        self.max_excerpt_chars = max_excerpt_chars
        self.max_contract_retries = max_contract_retries
        if memory_ttl_days < 1:
            raise ValueError("memory_ttl_days must be positive")
        self.evidence_memory = evidence_memory
        self.memory_ttl_days = memory_ttl_days
        self.route_snapshot_hash = route_snapshot_hash

    @classmethod
    def from_config(cls, client: WorkerProtocolClient, search: SearchAdapter,
                    fetcher: SafeHTTPFetcher, config: dict[str, Any], db: Database, *,
                    route_id: str = "research", environ: dict[str, str] | None = None) -> "LiveResearchWorker":
        """Build the production research path from an explicit route and Core DB."""
        gateway_config = config.get("model_gateway")
        if not isinstance(gateway_config, dict):
            raise ValueError("model_gateway configuration is required")
        gateway = build_routed_gateway(gateway_config, route_id, environ=environ)
        if not isinstance(gateway, RoutedModelGateway):
            raise ValueError("configured research route is not routed")
        route_hash = gateway.persist_snapshot(SQLiteModelRouteStore(db))
        memory_config = config.get("evidence_memory", {})
        if not isinstance(memory_config, dict):
            raise ValueError("evidence_memory configuration must be an object")
        return cls(
            client, search, fetcher, gateway, model="route",
            evidence_memory=EvidenceMemoryStore(db),
            memory_ttl_days=int(memory_config.get("ttl_days", 30)),
            route_snapshot_hash=route_hash,
        )

    def run_once(self, *, run_id: str) -> dict[str, Any]:
        claim = self.client.request("claim", {"job_type": "research"}, request_id=f"{run_id}:claim")
        lease = {"job_id": claim["job_id"], "lease_token": claim["lease_token"]}
        try:
            self.client.request("start", lease, request_id=f"{run_id}:start")
            job = self.client.request("describe", lease, request_id=f"{run_id}:describe")
            spec = LiveResearchJob.from_objective(job["objective"], project_id=job.get("project_id"))
            acquired = self.acquirer.acquire(spec.query, self.search, max_sources=spec.max_sources)
            if not acquired.sources:
                raise ResearchSourceError("live acquisition returned no valid sources")
            source_refs = tuple(source.uri for source in acquired.sources)
            deterministic = build_research(spec.objective_for_sources(source_refs), FixtureSourceAdapter({source.uri: source for source in acquired.sources}))
            deterministic["acquisition_failures"] = list(acquired.failures)
            memory_records = ()
            if self.evidence_memory is not None and spec.project_id:
                memory_records = self.evidence_memory.recall(spec.project_id, spec.query, limit=5)
                _append_memory_evidence(deterministic, memory_records)
            result = synthesize_research(
                deterministic,
                self.gateway,
                model=self.model,
                max_evidence=self.max_evidence,
                max_excerpt_chars=self.max_excerpt_chars,
                max_contract_retries=self.max_contract_retries,
            )
            if self.route_snapshot_hash:
                result["synthesis"]["model_route_snapshot_sha256"] = self.route_snapshot_hash
            json_bytes = canonical_json(result)
            markdown_bytes = render_synthesis_markdown(result).encode("utf-8")
            if len(json_bytes) + len(markdown_bytes) > self.max_output_bytes:
                raise ResearchBudgetError("research output exceeds limit")
            # Re-check the Core lease after network and model work, before publication.
            self.client.request("heartbeat", lease, request_id=f"{run_id}:fence")
            if self.evidence_memory is not None and spec.project_id:
                _store_acquired_memory(self.evidence_memory, spec.project_id, acquired.sources,
                                       ttl_days=self.memory_ttl_days)
            for kind, content in (("research.json", json_bytes), ("research.md", markdown_bytes)):
                self.client.request("upload_artifact", {**lease, "kind": kind, "path_or_ref": kind, "content_b64": base64.b64encode(content).decode("ascii"), "sha256": hashlib.sha256(content).hexdigest()}, request_id=f"{run_id}:upload:{kind}")
            self.client.request("complete", {**lease, "result": {"schema_version": "m2.3c", "source_count": len(acquired.sources)}}, request_id=f"{run_id}:complete")
            return result
        except Exception as exc:
            try:
                self.client.request("fail", {**lease, "error": exc.__class__.__name__, "retryable": isinstance(exc, (ResearchSourceError, ResearchBudgetError))}, request_id=f"{run_id}:fail")
            except Exception:
                pass
            raise


def _append_memory_evidence(result: dict[str, Any], records: tuple[dict[str, Any], ...]) -> None:
    """Add recalled, unexpired project records as explicitly sourced evidence."""
    sources = result["sources"]
    evidence = result["evidence"]
    known = {item["source_id"] for item in sources}
    for record in records:
        source_id = "memory_" + record["record_id"]
        if source_id in known:
            continue
        sources.append({
            "source_id": source_id, "source_type": "project_memory",
            "uri": record["source_uri"], "retrieved_at": record["observed_at"],
            "title": f"Project evidence {record['record_id']}",
            "provenance": {"project_id": record["project_id"],
                           "source_sha256": record["source_sha256"],
                           "valid_until": record["valid_until"]},
        })
        evidence.append({
            "evidence_id": "memory_evidence_" + hashlib.sha256(record["record_id"].encode()).hexdigest()[:16],
            "source_id": source_id, "excerpt": record["content"],
            "provenance": {"source_ref": record["source_uri"],
                           "retrieved_at": record["observed_at"],
                           "source_provenance": {"content_sha256": record["source_sha256"]}},
        })
        known.add(source_id)


def _store_acquired_memory(store: EvidenceMemoryStore, project_id: str, sources: tuple[Any, ...], *, ttl_days: int) -> None:
    observed = datetime.now(timezone.utc)
    valid_until = observed + timedelta(days=ttl_days)
    for source in sources:
        parsed = urlsplit(source.uri)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            continue
        source_hash = source.provenance.get("content_sha256")
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            continue
        store.add(EvidenceMemoryRecord(
            record_id="research_" + project_id + "_" + source_hash,
            project_id=project_id,
            content=source.content,
            source_uri=source.uri,
            source_sha256=source_hash,
            observed_at=observed.isoformat(),
            valid_until=valid_until.isoformat(),
        ))
