import json
import hashlib

from arda.core import Database, JobQueue
from arda.evidence_memory import EvidenceMemoryRecord, EvidenceMemoryStore
from arda.live_research import FetchedSource, SearchCandidate
from arda.live_research_worker import LiveResearchWorker
from arda.model_routes import ModelProfile, ModelRoutePolicy, RoutedModelGateway
from arda.research_synthesis import ModelResponse
from arda.worker_protocol import InMemoryWorkerTransport, WorkerProtocolClient, WorkerProtocolServer
from arda.worker_runtime import WorkerRegistry


class SearchFixture:
    def search(self, query, *, max_results):
        return [SearchCandidate("fixture", query, 1, "Fixture", "https://example.com/source", "snippet", "2026-10-01T00:00:00Z", {})]


class FetchFixture:
    def fetch(self, url):
        content = "Arda validates claims against evidence."
        return FetchedSource(url, url, 200, "text/html", "2026-10-01T00:00:00Z", content, hashlib.sha256(content.encode()).hexdigest())


class ModelFixture:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        pack = json.loads(kwargs["user_prompt"].split("\n", 1)[1].rsplit("\n", 1)[0])["evidence_pack"]
        evidence_id = pack["entries"][0]["evidence_id"]
        return ModelResponse(json.dumps({"status": "OK", "claims": [{"claim_id": "c1", "text": "Arda validates claims against evidence.", "evidence_ids": [evidence_id]}], "conflicts": []}), "fixture", 10, 5)


def test_live_worker_publishes_only_validated_research(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    db.execute("INSERT INTO projects(id, name, created_at, updated_at) VALUES ('arda', 'Arda', 'now', 'now')")
    WorkerRegistry(db).register("live-worker", "live-secret", public_key="fixture", capabilities=["research"])
    objective = json.dumps({
        "query": "Arda evidence",
        "research_questions": ["What does the source establish?"],
        "source_constraints": {"allowed_source_types": ["live_http"]},
        "max_sources": 1,
        "time_budget_seconds": 30,
        "required_output_format": ["research.json", "research.md"],
        "expected_claims": [],
    })
    job = JobQueue(db).enqueue("research", objective, idempotency_key="live-research-fixture", project_id="arda")
    client = WorkerProtocolClient("live-worker", "live-secret", InMemoryWorkerTransport(WorkerProtocolServer(db)))
    result = LiveResearchWorker(client, SearchFixture(), FetchFixture(), ModelFixture(), model="fixture").run_once(run_id="live-fixture")
    assert result["synthesis"]["claims"][0]["evidence_ids"]
    assert JobQueue(db).get(job.id).status == "succeeded"
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE job_id=? AND verified=1", (job.id,)) == 2
    db.close()


def test_configured_route_and_project_evidence_memory_are_used_in_live_worker(tmp_path, monkeypatch):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    db.execute("INSERT INTO projects(id, name, created_at, updated_at) VALUES ('arda', 'Arda', 'now', 'now')")
    WorkerRegistry(db).register("live-worker", "live-secret", public_key="fixture", capabilities=["research"])
    objective = json.dumps({
        "query": "Arda evidence",
        "research_questions": ["What does the source establish?"],
        "source_constraints": {"allowed_source_types": ["live_http"]},
        "max_sources": 1,
        "time_budget_seconds": 30,
        "required_output_format": ["research.json", "research.md"],
        "expected_claims": [],
    })
    job = JobQueue(db).enqueue("research", objective, idempotency_key="live-research-routed", project_id="arda")
    memories = EvidenceMemoryStore(db)
    memories.add(EvidenceMemoryRecord(
        "prior", "arda", "Arda previously validated source integrity.",
        "https://example.com/prior", "a" * 64,
        "2026-10-10T00:00:00Z", "2026-11-10T00:00:00Z",
    ))
    model = ModelFixture()
    route = RoutedModelGateway(
        [ModelProfile("local", "ollama", "fixture-model", model,
                      capabilities=frozenset({"chat", "json_schema"}),
                      allowed_data_classes=frozenset({"public", "internal", "sensitive"}))],
        ModelRoutePolicy(profile_ids=("local",), route_id="research", data_class="sensitive",
                         required_capabilities=frozenset({"chat", "json_schema"})),
    )
    monkeypatch.setattr("arda.live_research_worker.build_routed_gateway", lambda *args, **kwargs: route)
    client = WorkerProtocolClient("live-worker", "live-secret", InMemoryWorkerTransport(WorkerProtocolServer(db)))
    worker = LiveResearchWorker.from_config(
        client, SearchFixture(), FetchFixture(),
        {"model_gateway": {"profiles": [], "routes": {"research": {"profile_ids": ["local"]}}}}, db,
    )

    result = worker.run_once(run_id="live-routed")

    prompt = model.calls[0]["user_prompt"]
    assert "Arda previously validated source integrity." in prompt
    assert result["synthesis"]["model_route_snapshot_sha256"]
    assert db.scalar("SELECT COUNT(*) FROM model_route_snapshots WHERE route_id='research'") == 1
    assert db.scalar("SELECT COUNT(*) FROM evidence_memories WHERE project_id='arda'") == 2
    assert JobQueue(db).get(job.id).status == "succeeded"
    db.close()
