#!/usr/bin/env python3
"""Unit tests for ide_bridge.py (Universal IDE Bridge & Memory Health)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ide_bridge
import memory_store


class TestIDEBridge(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @patch.dict("os.environ", {"MEM0_OWNER": "test-owner"})
    @patch("ide_bridge.read_inventory")
    def test_health_raises_when_memory_read_fails(self, mock_inventory):
        from unittest.mock import MagicMock

        mock_inventory.side_effect = memory_store.MemoryReadError("Qdrant unavailable")
        with self.assertRaises(memory_store.MemoryReadError) as raised:
            ide_bridge.evaluate_health_score(scope="target", workspace_dir=str(self.ws))

        self.assertIn("Qdrant unavailable", str(raised.exception))
        mock_inventory.assert_called_once_with(owner="test-owner", scope="target",
                                               include_superseded=True)

    @patch("rules.list_rules")
    def test_sync_cursor_rules(self, mock_rules):
        mock_rules.return_value = [
            {
                "id": "await-async-services",
                "instruction": "All service calls must use await.",
                "paths": ["src/services/**", "src/api/**"],
            }
        ]
        files = ide_bridge.sync_cursor_rules(str(self.ws), scope="test_proj")
        self.assertEqual(len(files), 2)

        master_rule = self.ws / ".cursor" / "rules" / "000-mem0-memory-first.mdc"
        self.assertTrue(master_rule.is_file())
        self.assertIn("alwaysApply: true", master_rule.read_text())

        rule_path = self.ws / ".cursor" / "rules" / "await-async-services.mdc"
        self.assertTrue(rule_path.is_file())
        content = rule_path.read_text()
        self.assertIn("globs: src/services/**, src/api/**", content)
        self.assertIn("# Rule: await-async-services", content)
        self.assertIn("All service calls must use await.", content)


    def test_sync_claude_instructions_new_and_idempotent(self):
        # 1. New file
        claude_file = ide_bridge.sync_claude_instructions(str(self.ws), scope="backend_app")
        self.assertTrue(Path(claude_file).is_file())
        content1 = Path(claude_file).read_text()
        self.assertIn("<!-- MEM0:START -->", content1)
        self.assertIn("<!-- MEM0:END -->", content1)
        self.assertIn("`backend_app`", content1)

        # 2. Update with new scope (idempotent replacement)
        ide_bridge.sync_claude_instructions(str(self.ws), scope="frontend_app")
        content2 = Path(claude_file).read_text()
        self.assertEqual(content2.count("<!-- MEM0:START -->"), 1)
        self.assertEqual(content2.count("<!-- MEM0:END -->"), 1)
        self.assertIn("`frontend_app`", content2)
        self.assertNotIn("`backend_app`", content2)

    def test_generate_mcp_config(self):
        # VS Code
        vscode_file = ide_bridge.generate_mcp_config(str(self.ws), target="vscode")
        self.assertEqual(vscode_file.name, "mcp.json")
        self.assertTrue(vscode_file.is_file())
        data_vscode = json.loads(vscode_file.read_text())
        self.assertIn("mem0-local", data_vscode["mcpServers"])

        # Cline
        cline_file = ide_bridge.generate_mcp_config(str(self.ws), target="cline")
        self.assertTrue(cline_file.is_file())
        data_cline = json.loads(cline_file.read_text())
        self.assertIn("mem0-local", data_cline["mcpServers"])

    @patch("rules.list_rules")
    def test_evaluate_health_score(self, mock_rules):
        mock_rules.return_value = [{"id": "r1", "instruction": "Test rule"}]

        # Healthy setup
        memories = [
            {"id": "m1", "memory": "General architectural decision", "status": "active"},
        ]
        res_healthy = ide_bridge.evaluate_health_score(
            scope="proj",
            workspace_dir=str(self.ws),
            memories=memories,
        )
        self.assertGreaterEqual(res_healthy["score"], 90)
        self.assertEqual(res_healthy["status"], "EXCELLENT")

        # Stale reference setup
        memories_stale = [
            {"id": "m1", "memory": "Check deleted/legacy_db.py before migrating", "status": "active"},
        ]
        res_stale = ide_bridge.evaluate_health_score(
            scope="proj",
            workspace_dir=str(self.ws),
            memories=memories_stale,
        )
        self.assertLess(res_stale["score"], res_healthy["score"])
        self.assertEqual(res_stale["metrics"]["stale_memories"], 1)
        self.assertTrue(any("sweep" in r for r in res_stale["recommendations"]))

    def test_sync_antigravity(self):
        res = ide_bridge.sync_antigravity(str(self.ws), scope="test_scope", configure_global_mcp=False)
        self.assertEqual(res["status"], "configured")
        self.assertEqual(res["target"], "antigravity")

        agents_md = self.ws / "AGENTS.md"
        gemini_md = self.ws / "GEMINI.md"
        self.assertTrue(agents_md.is_file())
        self.assertTrue(gemini_md.is_file())
        self.assertIn("<!-- MEM0:START -->", agents_md.read_text())
        self.assertIn("`test_scope`", gemini_md.read_text())

    @patch("harness.assemble_context")
    def test_generate_universal_prompt(self, mock_assemble):
        mock_assemble.return_value = {
            "task": "Any task",
            "scope": "my_scope",
            "memories": "- Gotcha: use async handlers",
            "rules": "## Rules\n- No raw SQL",
            "skills": "",
            "graph": [],
        }
        prompt = ide_bridge.generate_universal_prompt(scope="my_scope", task="Any task")
        self.assertIn("# [SHARED BRAIN] Project Context: my_scope", prompt)
        self.assertIn("No raw SQL", prompt)
        self.assertIn("Gotcha: use async handlers", prompt)


if __name__ == "__main__":
    unittest.main()

