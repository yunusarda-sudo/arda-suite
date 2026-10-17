"""M2.3D worker path: live acquisition plus validated local-model synthesis."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from arda.live_research import LiveSourceAcquirer, SafeHTTPFetcher, SearchAdapter
from arda.research_worker import FixtureSourceAdapter, ResearchBudgetError, ResearchContractError, ResearchSourceError, ResearchTask, build_research, canonical_json
from arda.research_synthesis import ModelGateway, render_synthesis_markdown, synthesize_research
from arda.worker_protocol import WorkerCancellationRequested, WorkerLeaseGuard, WorkerProtocolClient


@dataclass(frozen=True)
class LiveResearchJob:
    query: str
    questions: tuple[str, ...]
    max_sources: int
    time_budget_seconds: float
    source_constraints: dict[str, Any]
    required_output_format: tuple[str, ...]
    expected_claims: tuple[dict[str, str], ...]

    @classmethod
    def from_objective(cls, objective: str) -> "LiveResearchJob":
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
        return cls(raw["query"].strip(), tuple(item.strip() for item in questions), max_sources, budget, dict(constraints), tuple(formats), tuple({"claim_id": item["claim_id"].strip(), "text": item["text"].strip()} for item in expected))

    def objective_for_sources(self, source_refs: tuple[str, ...]) -> ResearchTask:
        return ResearchTask(self.questions, source_refs, self.source_constraints, len(source_refs), self.time_budget_seconds, self.required_output_format, self.expected_claims)


class LiveResearchWorker:
    """Run one live research job entirely through the existing Core protocol."""

    def __init__(self, client: WorkerProtocolClient, search: SearchAdapter, fetcher: SafeHTTPFetcher,
                 gateway: ModelGateway, *, model: str, max_output_bytes: int = 512 * 1024,
                 max_evidence: int = 6, max_excerpt_chars: int = 1_200,
                 max_contract_retries: int = 1):
        self.client = client
        self.search = search
        self.acquirer = LiveSourceAcquirer(fetcher)
        self.gateway = gateway
        self.model = model
        self.max_output_bytes = max_output_bytes
        self.max_evidence = max_evidence
        self.max_excerpt_chars = max_excerpt_chars
        self.max_contract_retries = max_contract_retries

    def run_once(self, *, run_id: str) -> dict[str, Any]:
        claim = self.client.request("claim", {"job_type": "research"}, request_id=f"{run_id}:claim")
        lease = {"job_id": claim["job_id"], "lease_token": claim["lease_token"]}
        guard = None
        try:
            self.client.request("start", lease, request_id=f"{run_id}:start")
            with WorkerLeaseGuard(self.client, lease["job_id"], lease["lease_token"],
                                  run_id=run_id) as guard:
                job = self.client.request("describe", lease, request_id=f"{run_id}:describe")
                spec = LiveResearchJob.from_objective(job["objective"])
                guard.ensure_current()
                acquired = self.acquirer.acquire(spec.query, self.search, max_sources=spec.max_sources,
                                                 cancel_event=guard.cancel_event)
                guard.ensure_current()
                if not acquired.sources:
                    raise ResearchSourceError("live acquisition returned no valid sources")
                source_refs = tuple(source.uri for source in acquired.sources)
                deterministic = build_research(spec.objective_for_sources(source_refs), FixtureSourceAdapter({source.uri: source for source in acquired.sources}))
                deterministic["acquisition_failures"] = list(acquired.failures)
                guard.ensure_current()
                result = synthesize_research(
                    deterministic,
                    self.gateway,
                    model=self.model,
                    max_evidence=self.max_evidence,
                    max_excerpt_chars=self.max_excerpt_chars,
                    max_contract_retries=self.max_contract_retries,
                )
                guard.ensure_current()
            json_bytes = canonical_json(result)
            markdown_bytes = render_synthesis_markdown(result).encode("utf-8")
            if len(json_bytes) + len(markdown_bytes) > self.max_output_bytes:
                raise ResearchBudgetError("research output exceeds limit")
            # Re-check the Core lease after network and model work, before publication.
            self.client.request("heartbeat", lease, request_id=f"{run_id}:fence")
            for kind, content in (("research.json", json_bytes), ("research.md", markdown_bytes)):
                self.client.request("upload_artifact", {**lease, "kind": kind, "path_or_ref": kind, "content_b64": base64.b64encode(content).decode("ascii"), "sha256": hashlib.sha256(content).hexdigest()}, request_id=f"{run_id}:upload:{kind}")
            self.client.request("complete", {**lease, "result": {"schema_version": "m2.3c", "source_count": len(acquired.sources)}}, request_id=f"{run_id}:complete")
            return result
        except Exception as exc:
            try:
                if isinstance(exc, WorkerCancellationRequested) or (guard is not None and guard.cancellation_requested):
                    self.client.request("cancel_ack", lease, request_id=f"{run_id}:cancel-ack")
                else:
                    self.client.request("fail", {**lease, "error": exc.__class__.__name__, "retryable": isinstance(exc, (ResearchSourceError, ResearchBudgetError))}, request_id=f"{run_id}:fail")
            except Exception:
                pass
            raise
