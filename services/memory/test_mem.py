"""Unit tests for mem.py CLI helper functions and commands."""

import unittest
from unittest.mock import MagicMock, patch
import mem


class TestMemCLI(unittest.TestCase):
    def test_filters(self):
        f = mem._filters("solveny", "architect")
        self.assertEqual(f["user_id"], mem.OWNER)
        self.assertEqual(f["run_id"], "solveny")
        self.assertEqual(f["agent_id"], "architect")

        f_empty = mem._filters("", "")
        self.assertEqual(f_empty, {"user_id": mem.OWNER})

    def test_globs(self):
        self.assertEqual(mem._globs("src/**, test/*.py, "), ["src/**", "test/*.py"])
        self.assertEqual(mem._globs(""), [])
        self.assertEqual(mem._globs(None), [])

    def test_attr_formatting(self):
        item_active = {
            "created_at": "2026-09-20T12:34:56.789Z",
            "run_id": "projA",
            "agent_id": "architect",
            "status": "active",
            "memory": "Uses SQLite for storage",
        }
        res = mem._attr(item_active)
        self.assertIn("[2026-09-20 12:34] (projA/architect)", res)
        self.assertIn("Uses SQLite for storage", res)
        self.assertNotIn("[SUPERSEDED]", res)

        item_superseded = {
            "created_at": "2026-09-20T12:34:56.789Z",
            "run_id": "projA",
            "agent_id": "architect",
            "status": "superseded",
            "memory": "Uses Postgres for storage",
        }
        res_sup = mem._attr(item_superseded)
        self.assertIn("[SUPERSEDED]", res_sup)

        # with score
        item_active["score"] = 0.8765
        res_score = mem._attr(item_active, score=True)
        self.assertIn("0.876", res_score)

    @patch("mem.urllib.request.urlopen")
    def test_scroll_filtering(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"""{
            "result": {
                "points": [
                    {
                        "id": "11111111-0000-0000-0000-000000000001",
                        "payload": {
                            "user_id": "example-user",
                            "run_id": "projA",
                            "agent_id": "cli",
                            "data": "Active memory 1",
                            "status": "active",
                            "created_at": "2026-09-20T10:00:00Z"
                        }
                    },
                    {
                        "id": "11111111-0000-0000-0000-000000000002",
                        "payload": {
                            "user_id": "example-user",
                            "run_id": "projA",
                            "agent_id": "cli",
                            "data": "Superseded memory 2",
                            "status": "superseded",
                            "created_at": "2026-09-20T09:00:00Z"
                        }
                    },
                    {
                        "id": "11111111-0000-0000-0000-000000000003",
                        "payload": {
                            "user_id": "other_user",
                            "run_id": "projA",
                            "data": "Other user memory",
                            "status": "active"
                        }
                    }
                ]
            }
        }"""
        mock_urlopen.return_value = mock_response

        # Default: hide superseded
        active = mem._scroll(scope="projA", include_superseded=False)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "11111111-0000-0000-0000-000000000001")

        # include_superseded=True: show superseded
        all_pts = mem._scroll(scope="projA", include_superseded=True)
        self.assertEqual(len(all_pts), 2)
        ids = {p["id"] for p in all_pts}
        self.assertIn("11111111-0000-0000-0000-000000000001", ids)
        self.assertIn("11111111-0000-0000-0000-000000000002", ids)

    @patch("mem._scroll")
    @patch("config.get_memory")
    @patch("sys.argv", ["mem.py", "supersede", "11111111", "--new", "Replaced with FastAPI"])
    def test_main_supersede_with_new(self, mock_get_memory, mock_scroll):
        mock_scroll.return_value = [
            {"id": "11111111-0000-0000-0000-000000000001", "memory": "Old Flask memory", "status": "active"}
        ]
        mock_mem = MagicMock()
        mock_get_memory.return_value = mock_mem
        mock_mem.add.return_value = {
            "results": [{"id": "22222222-0000-0000-0000-000000000002", "memory": "Replaced with FastAPI", "event": "ADD"}]
        }

        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_mem.add.assert_called_once()
        self.assertEqual(mock_mem.add.call_args[1]["metadata"]["supersedes"], "11111111-0000-0000-0000-000000000001")
        mock_mem.update.assert_called_once_with(
            "11111111-0000-0000-0000-000000000001",
            metadata={"status": "superseded", "superseded_by": "22222222-0000-0000-0000-000000000002"}
        )

    @patch("mem._scroll")
    @patch("config.get_memory")
    @patch("sys.argv", ["mem.py", "supersede", "11111111"])
    def test_main_supersede_mark_only(self, mock_get_memory, mock_scroll):
        mock_scroll.return_value = [
            {"id": "11111111-0000-0000-0000-000000000001", "memory": "Old Flask memory", "status": "active"}
        ]
        mock_mem = MagicMock()
        mock_get_memory.return_value = mock_mem

        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_mem.update.assert_called_once_with(
            "11111111-0000-0000-0000-000000000001",
            metadata={"status": "superseded"}
        )

    @patch("tokens.generate_token")
    @patch("sys.argv", ["mem.py", "tokens", "add", "default"])
    def test_tokens_add_command(self, mock_gen):
        mock_gen.return_value = ("tok12345678901234567890123456789012", {"user_id": "default", "role": "developer"})
        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_gen.assert_called_once()

    @patch("tokens.generate_token")
    @patch("sys.argv", ["mem.py", "tokens", "add", "another-owner"])
    def test_tokens_add_rejects_a_different_owner(self, mock_gen):
        ret = mem.main()
        self.assertEqual(ret, 1)
        mock_gen.assert_not_called()

    @patch("graph_store.get_neighbors")
    @patch("sys.argv", ["mem.py", "graph", "neighbors", "mem:123"])
    def test_graph_neighbors_command(self, mock_nbrs):
        mock_nbrs.return_value = [
            {"neighbor_id": "tech:postgres", "kind": "entity", "label": "PostgreSQL",
             "relation": "uses", "direction": "outgoing"}
        ]
        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_nbrs.assert_called_once()

    @patch("compaction.compact_scope")

    @patch("mem._scroll")
    @patch("sys.argv", ["mem.py", "compact", "--scope", "projA", "--dry-run"])
    def test_compact_command(self, mock_scroll, mock_compact):
        mock_scroll.return_value = [
            {"id": "1", "memory": "Use Postgres", "status": "active"}
        ]
        mock_compact.return_value = {
            "scope": "projA",
            "dry_run": True,
            "clusters_found": 1,
            "memories_compacted": 1,
            "syntheses": [
                {
                    "synthesis": "Consolidated: Use Postgres",
                    "superseded_ids": ["1"],
                    "count": 1,
                }
            ],
        }
        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_compact.assert_called_once()

    @patch("compaction.sweep_stale_memories")
    @patch("mem._scroll")
    @patch("sys.argv", ["mem.py", "sweep", "--scope", "projA", "--dry-run"])
    def test_sweep_command(self, mock_scroll, mock_sweep):
        mock_scroll.return_value = []
        mock_sweep.return_value = {
            "scope": "projA",
            "workspace_dir": "/tmp",
            "dry_run": True,
            "stale_count": 0,
            "candidates": [],
        }
        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_sweep.assert_called_once()

    @patch("ide_bridge.sync_cursor_rules")
    @patch("ide_bridge.generate_mcp_config")
    @patch("ide_bridge.sync_claude_instructions")
    @patch("sys.argv", ["mem.py", "ide", "setup", "--target", "cursor", "--scope", "projA"])
    def test_ide_command(self, mock_claude, mock_mcp, mock_cursor):
        mock_cursor.return_value = ["rule.mdc"]
        mock_mcp.return_value = ".cursor/mcp.json"
        ret = mem.main()

        self.assertEqual(ret, 0)
        mock_cursor.assert_called_once()

    @patch("ide_bridge.evaluate_health_score")
    @patch("mem._scroll")
    @patch("sys.argv", ["mem.py", "health", "--scope", "projA"])
    def test_health_command(self, mock_scroll, mock_health):
        mock_scroll.return_value = []
        mock_health.return_value = {
            "scope": "projA",
            "score": 88,
            "status": "GOOD",
            "metrics": {
                "total_memories": 5,
                "active_memories": 4,
                "superseded_memories": 1,
                "stale_memories": 0,
                "rules_count": 2,
                "graph_nodes": 4,
            },
            "penalties": [],
            "recommendations": ["No issues"],
        }
        ret = mem.main()
        self.assertEqual(ret, 0)
        mock_health.assert_called_once()


if __name__ == "__main__":
    unittest.main()


