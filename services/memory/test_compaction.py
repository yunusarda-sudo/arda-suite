#!/usr/bin/env python3
"""Unit tests for compaction.py (Memory Compaction & Staleness Sweeper)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import compaction


class TestCompaction(unittest.TestCase):

    @patch("compaction.read_inventory")
    @patch("compaction.get_memory_client")
    def test_compact_scope_propagates_read_failure_before_mutation(self, mock_get_client, mock_inventory):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_inventory.side_effect = compaction.MemoryReadError("Qdrant unavailable")

        with self.assertRaises(compaction.MemoryReadError):
            compaction.compact_scope(scope="target", dry_run=False)

        mock_client.add.assert_not_called()
        mock_client.update.assert_not_called()

    @patch("compaction.read_inventory")
    @patch("compaction.get_memory_client")
    def test_sweep_propagates_read_failure_before_mutation(self, mock_get_client, mock_inventory):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_inventory.side_effect = compaction.MemoryReadError("Qdrant unavailable")

        with self.assertRaises(compaction.MemoryReadError):
            compaction.sweep_stale_memories(scope="target", dry_run=False)

        mock_client.update.assert_not_called()

    @patch.dict("os.environ", {"MEM0_OWNER": "test-owner"})
    @patch("compaction.read_inventory")
    @patch("compaction.get_memory_client")
    def test_compact_scope_uses_inventory_filtered_by_owner_and_scope(self, mock_get_client, mock_inventory):
        rows = [
            {"id": "a", "run_id": "target", "memory": "Docker service uses port 8080 mapping."},
            {"id": "b", "run_id": "target", "memory": "Expose port 8080 in Docker compose for service."},
        ]
        mock_inventory.return_value = rows
        mock_get_client.return_value = MagicMock()

        result = compaction.compact_scope(scope="target", dry_run=True)

        self.assertEqual(result["memories_compacted"], 0)
        self.assertEqual(result["would_supersede"], 2)
        mock_inventory.assert_called_once_with(
            owner="test-owner", scope="target", include_superseded=True
        )

    @patch.dict("os.environ", {"MEM0_OWNER": "test-owner"})
    @patch("compaction.read_inventory")
    @patch("compaction.get_memory_client")
    def test_sweep_uses_inventory_filtered_by_owner_and_scope(self, mock_get_client, mock_inventory):
        mock_inventory.return_value = [
            {"id": "target", "run_id": "target", "memory": "Update missing/target.py before release."},
        ]
        mock_get_client.return_value = MagicMock()

        result = compaction.sweep_stale_memories(scope="target", dry_run=True, workspace_dir="/tmp")

        self.assertEqual([item["id"] for item in result["candidates"]], ["target"])
        mock_inventory.assert_called_once_with(
            owner="test-owner", scope="target", include_superseded=True
        )

    @patch.dict("os.environ", {"MEM0_OWNER": "test-owner"})
    @patch("compaction.read_inventory")
    @patch("compaction.get_memory_client")
    def test_compact_scope_rejects_blank_scope_before_inventory_or_mutation(self, mock_get_client, mock_inventory):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        with self.assertRaisesRegex(ValueError, "scope is required"):
            compaction.compact_scope(scope="", dry_run=False)

        mock_inventory.assert_not_called()
        mock_client.add.assert_not_called()
        mock_client.update.assert_not_called()

    @patch.dict("os.environ", {"MEM0_OWNER": "test-owner"})
    @patch("compaction.read_inventory")
    @patch("compaction.get_memory_client")
    def test_sweep_rejects_blank_scope_before_inventory_or_mutation(self, mock_get_client, mock_inventory):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        with self.assertRaisesRegex(ValueError, "scope is required"):
            compaction.sweep_stale_memories(scope="", dry_run=False)

        mock_inventory.assert_not_called()
        mock_client.update.assert_not_called()

    def test_extract_keywords(self):
        kw = compaction.extract_keywords("Always use PostgreSQL migrations with Prisma ORM in production.")
        self.assertIn("postgresql", kw)
        self.assertIn("migrations", kw)
        self.assertIn("prisma", kw)
        self.assertNotIn("with", kw)
        self.assertNotIn("in", kw)

    def test_cluster_memories(self):
        memories = [
            {"id": "m1", "memory": "PostgreSQL database migrations require Prisma schema locking.", "status": "active"},
            {"id": "m2", "memory": "Run Prisma migrations before starting PostgreSQL containers.", "status": "active"},
            {"id": "m3", "memory": "Frontend uses Tailwind CSS with dark mode toggle.", "status": "active"},
            {"id": "m4", "memory": "Superseded PostgreSQL tip", "status": "superseded"},
        ]
        clusters = compaction.cluster_memories(memories, min_cluster_size=2)
        self.assertEqual(len(clusters), 1)
        cluster_ids = [m["id"] for m in clusters[0]]
        self.assertIn("m1", cluster_ids)
        self.assertIn("m2", cluster_ids)
        self.assertNotIn("m3", cluster_ids)
        self.assertNotIn("m4", cluster_ids)

    @patch("graph_store.get_neighbors")
    def test_cluster_memories_graph_augmented(self, mock_neighbors):
        def fake_nbrs(node_id, direction="both"):
            if node_id in ("ga1", "ga2"):
                return [{"neighbor_id": "tech:qdrant"}, {"neighbor_id": "file:config.py"}]
            return []
        mock_neighbors.side_effect = fake_nbrs

        memories = [
            {"id": "ga1", "memory": "Vector dimension set to 768 cosine.", "status": "active"},
            {"id": "ga2", "memory": "Port mapped to 6333 localhost endpoint.", "status": "active"},
            {"id": "ga3", "memory": "Frontend button color is navy blue.", "status": "active"},
        ]
        clusters = compaction.cluster_memories(memories, min_cluster_size=2)
        self.assertEqual(len(clusters), 1)
        cluster_ids = [m["id"] for m in clusters[0]]
        self.assertIn("ga1", cluster_ids)
        self.assertIn("ga2", cluster_ids)
        self.assertNotIn("ga3", cluster_ids)

    def test_synthesize_cluster(self):
        cluster = [
            {"id": "m1", "memory": "Use PostgreSQL migrations."},
            {"id": "m2", "memory": "Always run Prisma schema validate."},
        ]
        summary = compaction.synthesize_cluster(cluster, scope="backend_service", use_llm=False)
        self.assertIn("Consolidated Architectural Synthesis (backend_service):", summary)
        self.assertIn("Use PostgreSQL migrations.", summary)
        self.assertIn("Always run Prisma schema validate.", summary)

    def test_compact_scope_dry_run(self):
        memories = [
            {"id": "1", "memory": "Docker container requires port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for API service.", "status": "active"},
        ]
        res = compaction.compact_scope(scope="devops", dry_run=True, memories=memories)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["clusters_found"], 1)
        self.assertEqual(res["memories_compacted"], 0)
        self.assertEqual(res["would_supersede"], 2)
        self.assertNotIn("new_id", res["syntheses"][0])

    @patch("compaction.get_memory_client")
    def test_compact_scope_live(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.add.return_value = [{"id": "synth_123"}]
        mock_get_client.return_value = mock_client


        with patch("compaction.read_inventory", return_value=[
            {"id": "1", "memory": "Docker container requires port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for API service.", "status": "active"},
        ]) as mock_inventory:
            res = compaction.compact_scope(scope="devops", dry_run=False)
        mock_inventory.assert_called_once()
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["clusters_found"], 1)
        self.assertEqual(res["syntheses"][0]["new_id"], "synth_123")

        # Verify old memories were superseded
        self.assertEqual(mock_client.update.call_count, 2)
        mock_client.update.assert_any_call("1", metadata={"status": "superseded", "superseded_by": "synth_123"})
        mock_client.update.assert_any_call("2", metadata={"status": "superseded", "superseded_by": "synth_123"})
        self.assertEqual(res["syntheses"][0]["status"], "completed")

    @patch("compaction.get_memory_client")
    @patch("compaction.read_inventory")
    def test_live_compaction_reports_partial_supersession_failure(self, mock_inventory, mock_get_client):
        mock_inventory.return_value = [
            {"id": "1", "memory": "Docker service uses port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for service.", "status": "active"},
        ]
        client = MagicMock()
        client.add.return_value = [{"id": "synth_123"}]
        client.update.side_effect = [None, RuntimeError("write failed")]
        mock_get_client.return_value = client

        result = compaction.compact_scope(scope="devops", dry_run=False)

        synthesis = result["syntheses"][0]
        self.assertEqual(synthesis["status"], "partial")
        self.assertEqual(synthesis["new_id"], "synth_123")
        self.assertEqual(synthesis["supersession_errors"], [{"id": "2", "error": "write failed"}])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["memories_compacted"], 1)

    @patch("compaction.get_memory_client")
    @patch("compaction.read_inventory")
    def test_live_compaction_does_not_supersede_without_synthesis_id(self, mock_inventory, mock_get_client):
        mock_inventory.return_value = [
            {"id": "1", "memory": "Docker service uses port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for service.", "status": "active"},
        ]
        client = MagicMock()
        client.add.return_value = []
        mock_get_client.return_value = client

        result = compaction.compact_scope(scope="devops", dry_run=False)

        self.assertEqual(result["syntheses"][0]["status"], "error")
        self.assertIn("did not return an ID", result["syntheses"][0]["error"])
        client.update.assert_not_called()

    @patch("graph_store.add_node", side_effect=RuntimeError("graph unavailable"))
    @patch("compaction.get_memory_client")
    @patch("compaction.read_inventory")
    def test_live_compaction_reports_graph_failure_as_partial(self, mock_inventory, mock_get_client, _mock_add_node):
        mock_inventory.return_value = [
            {"id": "1", "memory": "Docker service uses port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for service.", "status": "active"},
        ]
        client = MagicMock()
        client.add.return_value = [{"id": "synth_123"}]
        mock_get_client.return_value = client

        result = compaction.compact_scope(scope="devops", dry_run=False)

        synthesis = result["syntheses"][0]
        self.assertEqual(synthesis["status"], "partial")
        self.assertEqual(synthesis["graph_error"], "graph unavailable")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["memories_compacted"], 2)

    @patch("compaction.get_memory_client", return_value=False)
    @patch("compaction.read_inventory", return_value=[])
    def test_live_compaction_rejects_missing_write_client(self, _mock_inventory, _mock_client):
        with self.assertRaisesRegex(compaction.MemoryReadError, "client is unavailable"):
            compaction.compact_scope(scope="devops", dry_run=False)

    @patch("compaction.get_memory_client")
    @patch("compaction.read_inventory")
    def test_live_sweep_reports_update_failures(self, mock_inventory, mock_get_client):
        mock_inventory.return_value = [
            {"id": "stale-1", "memory": "Refactor missing/legacy.py before release."},
        ]
        client = MagicMock()
        client.update.side_effect = RuntimeError("write failed")
        mock_get_client.return_value = client

        result = compaction.sweep_stale_memories(
            scope="devops", workspace_dir="/tmp", dry_run=False
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["candidates"][0]["update_error"], "write failed")

    @patch("compaction.get_memory_client", return_value=False)
    @patch("compaction.read_inventory", return_value=[])
    def test_live_sweep_rejects_missing_write_client(self, _mock_inventory, _mock_client):
        with self.assertRaisesRegex(compaction.MemoryReadError, "client is unavailable"):
            compaction.sweep_stale_memories(scope="devops", dry_run=False)

    @patch("compaction.get_memory_client")
    def test_live_compaction_rejects_caller_supplied_memories(self, mock_get_client):
        with self.assertRaisesRegex(ValueError, "must read its own owner-scoped inventory"):
            compaction.compact_scope(scope="devops", dry_run=False, memories=[
                {"id": "foreign", "memory": "untrusted", "run_id": "other"},
            ])
        mock_get_client.assert_not_called()

    @patch("compaction.get_memory_client")
    def test_live_sweep_rejects_caller_supplied_memories(self, mock_get_client):
        with self.assertRaisesRegex(ValueError, "must read its own owner-scoped inventory"):
            compaction.sweep_stale_memories(scope="devops", dry_run=False, memories=[
                {"id": "foreign", "memory": "missing/foreign.py", "run_id": "other"},
            ])
        mock_get_client.assert_not_called()

    def test_sweep_stale_memories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            existing_file = tmppath / "src" / "active_service.py"
            existing_file.parent.mkdir(parents=True)
            existing_file.write_text("# Active code")

            memories = [
                {"id": "m_valid", "memory": "Modify src/active_service.py to add auth handler.", "status": "active"},
                {"id": "m_stale", "memory": "Refactor legacy/deleted_handler.py before release.", "status": "active"},
            ]

            # Dry run
            sweep_dry = compaction.sweep_stale_memories(
                scope="proj",
                workspace_dir=tmpdir,
                dry_run=True,
                memories=memories,
            )
            self.assertEqual(sweep_dry["stale_count"], 1)
            self.assertEqual(sweep_dry["candidates"][0]["id"], "m_stale")
            self.assertIn("legacy/deleted_handler.py", sweep_dry["candidates"][0]["missing_files"])


if __name__ == "__main__":
    unittest.main()
