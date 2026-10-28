"""Contract tests for the read-only retrieval evaluation CLI.

The tests use a deterministic fake client. They never connect to Qdrant, Mem0,
or an external model.
Run with: PYTHONPATH=services/memory python3 -m unittest test_memory_eval -v
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import evaluate_retrieval as evaluation


class FakeMemoryClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def search(self, query, top_k, include_superseded=False):
        self.calls.append((query, top_k, include_superseded))
        key = (query, include_superseded)
        return [dict(hit) for hit in self.responses.get(key, [])][:top_k]


class MemoryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = {
            "version": 1,
            "scope": "project-alpha",
            "queries": [
                {
                    "id": "q1",
                    "query": "Where is the project deployed?",
                    "relevant_ids": ["replacement", "deploy-note"],
                    "replacements": {"old-deploy-note": "replacement"},
                },
                {
                    "id": "q2",
                    "query": "Which database does the service use?",
                    "relevant_ids": ["db-note"],
                    "replacements": {},
                },
            ],
        }

    def test_dataset_loads_from_caller_supplied_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "eval.json"
            path.write_text(json.dumps(self.dataset), encoding="utf-8")

            self.assertEqual(evaluation.load_dataset(path), self.dataset)

    def test_rejects_wrong_version_missing_scope_and_duplicate_query_ids(self):
        invalid_datasets = []
        wrong_version = dict(self.dataset, version=2)
        invalid_datasets.append(wrong_version)
        missing_scope = dict(self.dataset)
        missing_scope.pop("scope")
        invalid_datasets.append(missing_scope)
        duplicate_ids = dict(self.dataset, queries=[
            self.dataset["queries"][0],
            dict(self.dataset["queries"][1], id="q1"),
        ])
        invalid_datasets.append(duplicate_ids)

        for data in invalid_datasets:
            with self.subTest(data=data), self.assertRaises(ValueError):
                evaluation.validate_dataset(data)

    def test_evaluates_rank_metrics_and_supersession_without_writes(self):
        fake = FakeMemoryClient({
            (self.dataset["queries"][0]["query"], True): [
                {"id": "old-deploy-note", "status": "superseded"},
                {"id": "replacement", "status": "active"},
                {"id": "deploy-note", "status": "active"},
            ],
            # Deliberately include a superseded hit to prove leakage is measured.
            (self.dataset["queries"][0]["query"], False): [
                {"id": "old-deploy-note", "status": "superseded"},
                {"id": "replacement", "status": "active"},
            ],
            (self.dataset["queries"][1]["query"], True): [
                {"id": "db-note", "status": "active"},
            ],
            (self.dataset["queries"][1]["query"], False): [
                {"id": "db-note", "status": "active"},
            ],
        })

        report = evaluation.evaluate_dataset(self.dataset, fake, k=3)

        # q1's ordinary results include one stale hit and one relevant result;
        # q2 is relevant at rank 1.
        self.assertAlmostEqual(report["metrics"]["recall_at_k"], 0.75)
        self.assertAlmostEqual(report["metrics"]["mrr_at_k"], 0.75)
        q1_ndcg = (1 / math.log2(3)) / (1 + 1 / math.log2(3))
        self.assertAlmostEqual(report["metrics"]["ndcg_at_k"], (q1_ndcg + 1.0) / 2)
        # One of three normal-search hits is superseded, and one of two queries leaks.
        self.assertAlmostEqual(report["metrics"]["superseded_hit_rate"], 1 / 3)
        self.assertAlmostEqual(report["metrics"]["superseded_query_rate"], 0.5)
        self.assertAlmostEqual(report["metrics"]["replacement_hit_rate"], 1.0)
        self.assertEqual(report["scope"], "project-alpha")
        self.assertEqual(report["replacement_mapping_count"], 1)
        self.assertEqual(report["eligible_replacements_at_k"], 1)
        self.assertEqual(len(fake.calls), 4)
        self.assertTrue(all(top_k == 3 for _query, top_k, _flag in fake.calls))
        self.assertEqual(
            [(query, flag) for query, _top_k, flag in fake.calls],
            [
                (self.dataset["queries"][0]["query"], True),
                (self.dataset["queries"][0]["query"], False),
                (self.dataset["queries"][1]["query"], True),
                (self.dataset["queries"][1]["query"], False),
            ],
        )

    def test_rejects_nonpositive_k(self):
        with self.assertRaises(ValueError):
            evaluation.evaluate_dataset(self.dataset, FakeMemoryClient({}), k=0)
        with self.assertRaises(ValueError):
            evaluation.evaluate_dataset(
                self.dataset, FakeMemoryClient({}), k=evaluation.MAX_K + 1
            )

    def test_rejects_oversized_dataset(self):
        oversized = dict(self.dataset)
        oversized["queries"] = self.dataset["queries"] * (evaluation.MAX_QUERIES // 2 + 1)
        with self.assertRaises(ValueError):
            evaluation.validate_dataset(oversized)

    def test_unavailable_store_is_not_scored_as_empty_retrieval(self):
        class UnavailableClient(FakeMemoryClient):
            def _get_memory_client(self):
                return None

        with self.assertRaises(RuntimeError):
            evaluation.evaluate_dataset(self.dataset, UnavailableClient({}), k=3)

    def test_duplicate_result_ids_do_not_inflate_ranking_metrics(self):
        dataset = {
            "version": 1,
            "scope": "project-alpha",
            "queries": [{"id": "q", "query": "question", "relevant_ids": ["a"]}],
        }
        fake = FakeMemoryClient({
            ("question", True): [
                {"id": "a"}, {"id": "a"},
                {"id": "old", "status": "superseded"},
                {"id": "old", "status": "superseded"},
            ],
            ("question", False): [
                {"id": "a"}, {"id": "a"},
                {"id": "old", "status": "superseded"},
                {"id": "old", "status": "superseded"},
            ],
        })

        result = evaluation.evaluate_dataset(dataset, fake, k=5)
        metrics = result["metrics"]
        self.assertEqual(metrics["recall_at_k"], 1.0)
        self.assertEqual(metrics["mrr_at_k"], 1.0)
        self.assertEqual(metrics["ndcg_at_k"], 1.0)
        self.assertEqual(metrics["superseded_hit_rate"], 0.5)
        self.assertEqual(result["queries"][0]["returned_hits"], 2)

    def test_conflicting_duplicate_status_fails_closed_as_superseded(self):
        hits = evaluation._dedupe_hits([
            {"id": "same", "status": "active"},
            {"id": "same", "status": "superseded"},
        ])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["status"], "superseded")


if __name__ == "__main__":
    unittest.main()
