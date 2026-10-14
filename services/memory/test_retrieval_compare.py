"""Offline contract tests for the read-only retrieval comparison utility.

These tests intentionally use a tiny caller-provided corpus and semantic hits;
they never connect to a memory backend or mutate stored memories.
"""

import json
import tempfile
import unittest
from pathlib import Path

import retrieval_compare as rc


def sample_dataset():
    return {
        "version": 1,
        "scope": "project-a",
        "as_of": "2026-10-14T12:00:00",
        "corpus": [
            {
                "id": "old-cache",
                "memory": "Python cache uses Redis",
                "scope": "project-a",
                "status": "superseded",
                "created_at": "2026-10-10T12:00:00",
                "metadata": {},
            },
            {
                "id": "new-cache",
                "memory": "Python cache uses SQLite",
                "scope": "project-a",
                "status": "active",
                "created_at": "2026-10-13T12:00:00",
                "metadata": {},
            },
            {
                "id": "python-db",
                "memory": "Python database connection pooling",
                "scope": "project-a",
                "status": "active",
                "created_at": "2026-10-13T12:00:00",
                "metadata": {},
            },
            {
                "id": "unrelated",
                "memory": "The deployment uses a blue interface",
                "scope": "project-a",
                "status": "active",
                "created_at": "2026-10-13T12:00:00",
                "metadata": {},
            },
        ],
        "queries": [
            {
                "id": "q-cache",
                "query": "Python cache",
                "relevant_ids": ["new-cache", "python-db"],
                # Order is the semantic retrieval order for equal scores.
                "semantic_hits": [
                    {"id": "old-cache", "score": 0.99},
                    {"id": "new-cache", "score": 0.8},
                    {"id": "python-db", "score": 0.8},
                    {"id": "unrelated", "score": 0.2},
                ],
                "replacements": {"old-cache": "new-cache"},
            }
        ],
    }


class TestRetrievalComparison(unittest.TestCase):
    def test_compares_baseline_bm25_and_existing_style_rerank(self):
        result = rc.evaluate(sample_dataset(), k=3)

        self.assertEqual(
            set(result["methods"]),
            {"semantic_baseline", "semantic_add_only_history", "bm25", "semantic_temporal_keyword"},
        )
        for method in result["methods"].values():
            self.assertIn("recall", method["metrics"])
            self.assertIn("mrr", method["metrics"])
            self.assertIn("ndcg", method["metrics"])
            self.assertIn("recall", method["delta_vs_semantic_baseline"])
            self.assertGreaterEqual(method["metrics"]["recall"], 0.0)
            self.assertLessEqual(method["metrics"]["recall"], 1.0)

        # Superseded records never enter normal rankings, even when the
        # semantic provider returned them at the highest score.
        for name, method in result["methods"].items():
            if name == "semantic_add_only_history":
                continue
            self.assertNotIn("old-cache", result["queries"]["q-cache"][method["name"]]["ranked_ids"])
        self.assertIn(
            "old-cache",
            result["queries"]["q-cache"]["semantic_add_only_history"]["ranked_ids"],
        )

    def test_semantic_baseline_preserves_provider_order_after_filtering(self):
        result = rc.evaluate(sample_dataset(), k=3)
        ranked = result["queries"]["q-cache"]["semantic_baseline"]["ranked_ids"]
        self.assertEqual(ranked[:2], ["new-cache", "python-db"])

    def test_semantic_candidate_cutoff_precedes_superseded_filter(self):
        result = rc.evaluate(sample_dataset(), k=2)
        ranked = result["queries"]["q-cache"]["semantic_baseline"]["ranked_ids"]
        self.assertEqual(ranked, ["new-cache"])
        self.assertEqual(result["candidate_k"], 2)

    def test_superseded_leakage_and_replacement_are_reported(self):
        result = rc.evaluate(sample_dataset(), k=3)
        metrics = result["methods"]["semantic_baseline"]["metrics"]

        self.assertEqual(metrics["superseded_leakage_count"], 1)
        self.assertEqual(metrics["superseded_leakage_queries"], 1)
        self.assertAlmostEqual(metrics["superseded_candidate_rate"], 1 / 3)
        self.assertEqual(metrics["replacement_eligible_count"], 1)
        self.assertEqual(metrics["replacement_hit_rate"], 1.0)

        # Reranking filters stale IDs from final hits but reports the raw
        # semantic candidate pool; BM25 only searches active corpus rows.
        self.assertEqual(
            result["methods"]["semantic_temporal_keyword"]["metrics"]["superseded_leakage_count"], 1
        )
        self.assertEqual(result["methods"]["bm25"]["metrics"]["superseded_leakage_count"], 0)

    def test_reranker_only_reorders_semantic_candidates(self):
        data = sample_dataset()
        data["corpus"].append({
            "id": "bm25-only",
            "memory": "Python cache cache cache",
            "scope": "project-a",
            "status": "active",
            "created_at": "2026-10-13T12:00:00",
            "metadata": {},
        })
        result = rc.evaluate(data, k=10)
        reranked = result["queries"]["q-cache"]["semantic_temporal_keyword"]["ranked_ids"]
        self.assertNotIn("bm25-only", reranked)

    def test_every_method_has_stable_tie_breaking(self):
        first = rc.evaluate(sample_dataset(), k=10)
        second = rc.evaluate(sample_dataset(), k=10)
        for name in ("semantic_baseline", "bm25", "semantic_temporal_keyword"):
            self.assertEqual(
                first["queries"]["q-cache"][name]["ranked_ids"],
                second["queries"]["q-cache"][name]["ranked_ids"],
            )
        # The semantic baseline retains the input order for equal scores.
        self.assertEqual(
            first["queries"]["q-cache"]["semantic_baseline"]["ranked_ids"][:2],
            ["new-cache", "python-db"],
        )

    def test_scope_mismatch_is_rejected_before_comparison(self):
        data = sample_dataset()
        data["corpus"][0]["scope"] = "another-project"
        with self.assertRaisesRegex(ValueError, "scope"):
            rc.evaluate(data)

    def test_dataset_and_query_validation(self):
        malformed = sample_dataset()
        malformed["queries"][0]["relevant_ids"] = ["missing-id"]
        with self.assertRaises(ValueError):
            rc.evaluate(malformed)

        malformed = sample_dataset()
        malformed["queries"][0]["semantic_hits"][0]["score"] = "high"
        with self.assertRaises(ValueError):
            rc.evaluate(malformed)

        with self.assertRaises(ValueError):
            rc.evaluate(sample_dataset(), k=0)

        malformed = sample_dataset()
        malformed["as_of"] = "not-a-date"
        with self.assertRaises(ValueError):
            rc.evaluate(malformed)

    def test_temporal_keyword_rerank_uses_fixed_as_of(self):
        data = sample_dataset()
        # New candidate starts slightly behind, but its exact query token gets
        # the MCP-style keyword boost. Fixed `as_of` makes this reproducible.
        data["queries"][0]["semantic_hits"] = [
            {"id": "python-db", "score": 0.78},
            {"id": "new-cache", "score": 0.75},
        ]
        result = rc.evaluate(data, k=2)
        ids = result["queries"]["q-cache"]["semantic_temporal_keyword"]["ranked_ids"]
        self.assertEqual(ids, ["new-cache", "python-db"])

    def test_replacement_eligibility_requires_superseded_old_id(self):
        data = sample_dataset()
        data["queries"][0]["replacements"] = {"new-cache": "python-db"}
        result = rc.evaluate(data, k=3)
        metrics = result["methods"]["semantic_baseline"]["metrics"]
        self.assertEqual(metrics["replacement_eligible_count"], 0)
        self.assertIsNone(metrics["replacement_hit_rate"])


class TestDatasetLoading(unittest.TestCase):
    def test_loads_json_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "eval.json"
            path.write_text(json.dumps(sample_dataset()), encoding="utf-8")
            self.assertEqual(rc.load_dataset(path), sample_dataset())

    def test_rejects_input_over_byte_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.json"
            path.write_text(" " * 64, encoding="utf-8")
            with self.assertRaises(ValueError):
                rc.load_dataset(path, max_bytes=32)

    def test_rejects_oversized_corpus_and_query_count(self):
        data = sample_dataset()
        data["corpus"] = [
            {"id": f"m-{i}", "memory": "fact", "scope": "project-a"}
            for i in range(1001)
        ]
        with self.assertRaises(ValueError):
            rc.evaluate(data)

        data = sample_dataset()
        data["queries"] = [dict(data["queries"][0], id=f"q-{i}") for i in range(501)]
        with self.assertRaises(ValueError):
            rc.evaluate(data)


if __name__ == "__main__":
    unittest.main()
