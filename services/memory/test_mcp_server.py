#!/usr/bin/env python3
"""Unit tests for mcp_server recency decay, superseded lifecycle, and ranking."""

import datetime
import unittest
from unittest.mock import MagicMock, patch

import mcp_server as ms



class TestRecencyFactor(unittest.TestCase):

    def test_missing_or_empty_timestamp_returns_full_weight(self):
        self.assertEqual(ms.recency_factor(""), 1.0)
        self.assertEqual(ms.recency_factor(None), 1.0)

    def test_invalid_timestamp_returns_full_weight(self):
        self.assertEqual(ms.recency_factor("not-a-date"), 1.0)

    def test_fresh_timestamp_returns_full_weight(self):
        now = datetime.datetime(2026, 9, 20, 12, 0, 0)
        ts = "2026-09-20T12:00:00"
        self.assertAlmostEqual(ms.recency_factor(ts, now=now), 1.0, places=4)

    def test_90_days_decay(self):
        now = datetime.datetime(2026, 9, 20, 12, 0, 0)
        ts = "2026-06-22T12:00:00"  # 90 days earlier
        # 0.6 + 0.4 * 0.5 = 0.8
        self.assertAlmostEqual(ms.recency_factor(ts, now=now, half_life_days=90.0), 0.8, places=2)

    def test_very_old_timestamp_hits_floor(self):
        now = datetime.datetime(2026, 9, 20, 12, 0, 0)
        ts = "2000-01-01T00:00:00"
        factor = ms.recency_factor(ts, now=now, half_life_days=90.0)
        self.assertGreaterEqual(factor, 0.6)
        self.assertLess(factor, 0.601)


class TestAttrLine(unittest.TestCase):

    def test_active_memory_rendering(self):
        item = {
            "created_at": "2026-09-20T14:30:00",
            "run_id": "proj",
            "agent_id": "claude",
            "memory": "Uses SQLite",
            "status": "active",
        }
        line = ms._attr_line(item)
        self.assertIn("[2026-09-20 14:30]", line)
        self.assertIn("(proj/claude)", line)
        self.assertNotIn("[SUPERSEDED]", line)
        self.assertIn("Uses SQLite", line)

    def test_superseded_memory_rendering(self):
        item = {
            "created_at": "2026-09-20T14:30:00",
            "run_id": "proj",
            "agent_id": "claude",
            "memory": "Uses SQLite",
            "status": "superseded",
        }
        line = ms._attr_line(item)
        self.assertIn("[SUPERSEDED]", line)


class TestMergeHits(unittest.TestCase):

    def test_excludes_superseded_by_default(self):
        hits = [
            {"id": "1", "memory": "Old fact", "score": 0.95, "status": "superseded", "created_at": "2026-09-20T10:00:00"},
            {"id": "2", "memory": "New fact", "score": 0.85, "status": "active", "created_at": "2026-09-20T12:00:00"},
        ]
        merged = ms._merge_hits([hits], top_k=5, include_superseded=False)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["id"], "2")

    def test_includes_superseded_when_requested(self):
        hits = [
            {"id": "1", "memory": "Old fact", "score": 0.95, "status": "superseded", "created_at": "2026-09-20T10:00:00"},
            {"id": "2", "memory": "New fact", "score": 0.85, "status": "active", "created_at": "2026-09-20T12:00:00"},
        ]
        merged = ms._merge_hits([hits], top_k=5, include_superseded=True)
        self.assertEqual(len(merged), 2)

    def test_recency_decay_boosts_fresh_memory(self):
        # Memory 1 is 180 days old with raw score 0.90 -> effective score = 0.90 * 0.70 = 0.63
        # Memory 2 is today with raw score 0.80 -> effective score = 0.80 * 1.00 = 0.80
        # Without recency decay, Memory 1 would win. With decay, Memory 2 wins!
        hits = [
            {"id": "1", "memory": "Old fact", "score": 0.90, "status": "active", "created_at": "2026-03-24T12:00:00"},
            {"id": "2", "memory": "Fresh fact", "score": 0.80, "status": "active", "created_at": "2026-09-20T12:00:00"},
        ]
        merged = ms._merge_hits([hits], top_k=2)
        self.assertEqual(merged[0]["id"], "2")
        self.assertEqual(merged[1]["id"], "1")

    def test_multi_factor_authority_boost(self):
        # Memory 1 is ordinary note with score 0.75
        # Memory 2 has lower similarity (0.70) but is an authoritative architectural decision (pinned)
        hits = [
            {"id": "1", "memory": "Casual note", "score": 0.75, "status": "active", "created_at": "2026-09-20T12:00:00", "metadata": {}},
            {"id": "2", "memory": "Authoritative DB architecture", "score": 0.70, "status": "active", "created_at": "2026-09-20T12:00:00", "metadata": {"authoritative": True}},
        ]
        # Memory 2 effective score: 0.70 * 1.0 * 1.25 = 0.875 > 0.75
        merged = ms._merge_hits([hits], top_k=2)
        self.assertEqual(merged[0]["id"], "2")
        self.assertEqual(merged[1]["id"], "1")

    def test_keyword_boost_exact_token_match(self):
        # Memory 1 has generic match (0.80), Memory 2 has exact technical token match "8812" (0.75 raw)
        hits = [
            {"id": "1", "memory": "Standard web port listening.", "score": 0.80, "status": "active", "created_at": "2026-09-20T12:00:00"},
            {"id": "2", "memory": "Bound to 8812 port for MCP.", "score": 0.75, "status": "active", "created_at": "2026-09-20T12:00:00"},
        ]
        # With query containing "8812", Memory 2 gets keyword boost (0.75 + 0.12 = 0.87 > 0.80)
        merged = ms._merge_hits([hits], top_k=2, query="which service on 8812?")
        self.assertEqual(merged[0]["id"], "2")
        self.assertEqual(merged[1]["id"], "1")

    @patch("graph_store.multi_hop_query")
    @patch("graph_store.get_node")
    def test_graph_expansion_includes_linked_neighbor(self, mock_get_node, mock_multi_hop):
        mock_multi_hop.return_value = [
            {"node_id": "mem_linked", "kind": "memory", "reached_via": "seed_1", "relation": "links", "hop": 1}
        ]
        mock_get_node.return_value = {
            "id": "mem_linked",
            "kind": "memory",
            "label": "Token located in ~/.mem0/mcp-token",
            "properties": {"text": "Token located in ~/.mem0/mcp-token", "scope": "personal"},
            "updated_at": "2026-09-20T12:00:00",
        }
        hits = [
            {"id": "seed_1", "memory": "Auth requires Bearer tokens.", "score": 0.90, "status": "active", "created_at": "2026-09-20T12:00:00"}
        ]
        merged = ms._merge_hits([hits], top_k=5, query="auth setup")
        merged_ids = [m["id"] for m in merged]
        self.assertIn("seed_1", merged_ids)
        self.assertIn("mem_linked", merged_ids)
        # Verify graph-expanded metadata
        linked = next(m for m in merged if m["id"] == "mem_linked")
        self.assertTrue(linked["metadata"]["graph_expanded"])
        self.assertEqual(linked["metadata"]["via"], "seed_1")


class TestGraphTools(unittest.TestCase):

    def setUp(self):
        import tempfile
        import os
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = os.environ.get("MEM0_GRAPH_DB")
        os.environ["MEM0_GRAPH_DB"] = os.path.join(self._tmp.name, "graph.db")

    def tearDown(self):
        import os
        if self._orig_db is not None:
            os.environ["MEM0_GRAPH_DB"] = self._orig_db
        else:
            os.environ.pop("MEM0_GRAPH_DB", None)
        self._tmp.cleanup()

    def test_link_and_get_memory_graph(self):
        res_link = ms.link_memories("mem:user", "tech:postgres", relation="stores_in")
        self.assertIn("Linked", res_link)

        graph_text = ms.get_memory_graph("mem:user", depth=1)
        self.assertIn("Knowledge Subgraph", graph_text)
        self.assertIn("mem:user", graph_text)
        self.assertIn("tech:postgres", graph_text)
        self.assertIn("stores_in", graph_text)

    def test_get_memory_graph_hubs(self):
        ms.link_memories("mem:1", "tech:fastapi", relation="uses")
        ms.link_memories("mem:2", "tech:fastapi", relation="uses")
        ms.link_memories("mem:3", "tech:fastapi", relation="uses")

        hubs_text = ms.get_memory_graph("hubs")
        self.assertIn("Top Architectural Hubs", hubs_text)
        self.assertIn("tech:fastapi", hubs_text)


class TestSkillsAndHarnessTools(unittest.TestCase):

    def test_list_available_skills(self):
        import skills_engine
        with patch.object(skills_engine, "discover_skills", return_value=[{
            "name": "git-worktree-sandbox", "scope": "repo", "description": "Use an isolated git worktree"
        }]):
            output = ms.list_available_skills()
        self.assertIn("Discovered Skills", output)
        self.assertIn("git-worktree-sandbox", output)

    def test_list_skills_with_matching(self):
        import skills_engine
        with patch.object(skills_engine, "discover_skills", return_value=[{
            "name": "ast-codebase-navigator", "scope": "repo",
            "description": "Navigate Python codebases by parsing their AST"
        }]):
            output = ms.list_available_skills(task_context="ast parse python codebase")
        self.assertIn("ast-codebase-navigator", output)

    def test_execute_with_harness_safe(self):
        output = ms.execute_with_harness(
            task="Perform routine code inspection",
            scope="test_proj",
            verify_command="echo 'checks passed'",
        )
        self.assertIn("Harness Execution", output)
        self.assertIn("[NOT_EXECUTED]", output)
        self.assertIn("Safety Review: NOT PERFORMED", output)
        self.assertNotIn("Verification (echo 'checks passed'): PASS", output)
        self.assertNotIn("Self-correction memory recorded", output)

    def test_execute_with_harness_does_not_claim_safety_scan(self):
        output = ms.execute_with_harness(
            task="Delete everything rm -rf /",
            scope="test_proj",
        )
        self.assertIn("[NOT_EXECUTED]", output)
        self.assertIn("Safety Review: NOT PERFORMED", output)
        self.assertNotIn("Safety Violation", output)


class TestCompactionTools(unittest.TestCase):

    @patch("compaction.compact_scope")
    def test_compact_memories_delegates_scope_to_safe_inventory_consumer(self, mock_compact):
        mock_compact.return_value = {
            "scope": "test_scope", "dry_run": True, "clusters_found": 0,
            "memories_compacted": 0, "syntheses": [],
        }

        ms.compact_memories(scope="test_scope", dry_run=True)

        mock_compact.assert_called_once_with(scope="test_scope", dry_run=True)

    @patch("compaction.read_inventory")
    def test_compact_memories_propagates_read_error_without_reporting_empty_success(self, mock_inventory):
        from memory_store import MemoryReadError
        mock_inventory.side_effect = MemoryReadError("Qdrant unavailable")

        with self.assertRaises(MemoryReadError) as raised:
            ms.compact_memories(scope="test_scope", dry_run=True)

        self.assertIn("Qdrant unavailable", str(raised.exception))
        mock_inventory.assert_called_once()

    @patch("ide_bridge.evaluate_health_score")
    @patch("mcp_server._mem")
    @patch("mcp_server.read_inventory")
    def test_health_propagates_read_error_without_reporting_empty_success(self, mock_inventory, mock_mem, mock_eval):
        mock_client = MagicMock()
        from memory_store import MemoryReadError
        mock_inventory.side_effect = MemoryReadError("Qdrant unavailable")
        mock_mem.return_value = mock_client

        with self.assertRaises(MemoryReadError) as raised:
            ms.get_memory_health(scope="test_scope")

        self.assertIn("Qdrant unavailable", str(raised.exception))
        mock_eval.assert_not_called()

    @patch("compaction.read_inventory")
    def test_sweep_propagates_read_error_without_reporting_empty_success(self, mock_inventory):
        from memory_store import MemoryReadError
        mock_inventory.side_effect = MemoryReadError("Qdrant unavailable")

        with self.assertRaises(MemoryReadError) as raised:
            ms.sweep_stale_memories(scope="test_scope", dry_run=True)

        self.assertIn("Qdrant unavailable", str(raised.exception))
        mock_inventory.assert_called_once()

    @patch("compaction.compact_scope")
    def test_compact_memories_tool(self, mock_compact):
        mock_compact.return_value = {
            "scope": "test_scope",
            "dry_run": True,
            "clusters_found": 1,
            "memories_compacted": 2,
            "syntheses": [
                {
                    "synthesis": "Consolidated Architectural Note",
                    "superseded_ids": ["m1", "m2"],
                    "count": 2,
                }
            ],
        }

        output = ms.compact_memories(scope="test_scope", dry_run=True)
        self.assertIn("Memory Compaction [DRY-RUN] for scope 'test_scope'", output)
        self.assertIn("Found 1 clusters (2 memories would be superseded)", output)
        self.assertIn("Consolidated Architectural Note", output)

    @patch("compaction.sweep_stale_memories")
    def test_sweep_stale_memories_tool(self, mock_sweep):
        mock_sweep.return_value = {
            "scope": "test_scope",
            "workspace_dir": "/tmp/test",
            "dry_run": True,
            "stale_count": 1,
            "candidates": [
                {"id": "m_stale_123", "memory": "Check old/deleted.py", "missing_files": ["old/deleted.py"]}
            ],
        }

        output = ms.sweep_stale_memories(scope="test_scope", dry_run=True)
        self.assertIn("Staleness Sweeper [DRY-RUN] for scope 'test_scope'", output)
        self.assertIn("Stale memories found: 1", output)
        self.assertIn("Missing file(s): old/deleted.py", output)


class TestIDEBridgeTools(unittest.TestCase):

    @patch("ide_bridge.evaluate_health_score")
    @patch("mcp_server._mem")
    def test_get_memory_health_tool(self, mock_mem, mock_eval):
        mock_client = MagicMock()
        mock_client.get_all.return_value = []
        mock_mem.return_value = mock_client
        mock_eval.return_value = {
            "scope": "test_proj",
            "score": 95,
            "status": "EXCELLENT",
            "metrics": {
                "total_memories": 10,
                "active_memories": 9,
                "superseded_memories": 1,
                "stale_memories": 0,
                "rules_count": 3,
                "graph_nodes": 5,
            },
            "penalties": [],
            "recommendations": ["Keep memories updated"],
        }

        output = ms.get_memory_health(scope="test_proj")
        self.assertIn("Memory Health Score: 95/100 [EXCELLENT]", output)
        self.assertIn("Keep memories updated", output)

    @patch("ide_bridge.sync_cursor_rules")
    @patch("ide_bridge.generate_mcp_config")
    @patch("ide_bridge.sync_claude_instructions")
    def test_configure_ide_bridge_tool(self, mock_claude, mock_mcp, mock_cursor):
        mock_cursor.return_value = ["rule1.mdc"]
        mock_mcp.return_value = ".cursor/mcp.json"
        mock_claude.return_value = "CLAUDE.md"


        output = ms.configure_ide_bridge(target="all", workspace_dir="/tmp/test", scope="test_proj")
        self.assertIn("IDE Bridge Configured for 'all'", output)
        self.assertIn("Cursor: Synced 1 rules", output)
        self.assertIn("Claude Code: Injected", output)


if __name__ == "__main__":
    unittest.main()



