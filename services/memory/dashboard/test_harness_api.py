#!/usr/bin/env python3
"""Unit tests for Harness Studio backend endpoints in dashboard/server.py."""

import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEM0_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(MEM0_ROOT))

import server


class TestHarnessApi(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_harness_context(self):
        data = server.read_harness_context("Check database connections", scope="personal")
        self.assertIn("task", data)
        self.assertIn("scope", data)
        self.assertIn("context", data)
        self.assertIn("hubs", data)
        self.assertEqual(data["task"], "Check database connections")
        self.assertEqual(data["scope"], "personal")

    def test_run_harness_execution_without_executor_is_not_executed(self):
        result = server.run_harness_execution(
            task="Verify python version",
            scope="test_proj",
            verify_command="echo 'verification ok'",
            cwd=str(self.tmp_path),
        )
        self.assertEqual(result["status"], "not_executed")
        self.assertIsNone(result["safety_passed"])
        self.assertEqual(result["safety_status"], "not_applicable")
        self.assertFalse(result["execution_performed"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["verification_passed"])
        self.assertEqual(result["verification_status"], "not_run")
        self.assertEqual(result["verification_output"], "")

    def test_run_harness_execution_does_not_claim_safety_scan(self):
        result = server.run_harness_execution(
            task="rm -rf /",
            scope="test_proj",
            cwd=str(self.tmp_path),
        )
        self.assertEqual(result["status"], "not_executed")
        self.assertEqual(result["safety_status"], "not_applicable")
        self.assertFalse(result["execution_performed"])

    def test_list_and_read_harness_trajectories(self):
        # Run a small task to generate a trajectory
        result = server.run_harness_execution(
            task="Test trajectory generation",
            scope="test_scope",
            verify_command="echo 'done'",
            cwd=str(self.tmp_path),
        )
        run_id = result["run_id"]
        self.assertTrue(run_id)

        # List trajectories
        listing = server.list_harness_trajectories()
        self.assertIn("trajectories", listing)
        found = [t for t in listing["trajectories"] if t["run_id"] == run_id]
        self.assertTrue(len(found) >= 1)
        self.assertEqual(found[0]["status"], "not_executed")

        # Read trajectory detail
        detail = server.read_harness_trajectory_detail(run_id)
        self.assertEqual(detail["run_id"], run_id)
        self.assertTrue(len(detail["steps"]) >= 2)
        step_actions = [s["action"] for s in detail["steps"]]
        self.assertIn("task_start", step_actions)
        self.assertIn("task_end", step_actions)

    def test_assets_wiring_for_harness(self):
        html = server.PAGE_HTML
        self.assertIn('data-view="harness"', html)
        self.assertIn('id="view-harness"', html)
        self.assertIn('id="harness-task"', html)
        self.assertIn('id="harness-run-btn"', html)
        self.assertIn('id="harness-terminal"', html)
        self.assertIn("CONTEXT ASSEMBLY ATTEMPTED · TASK NOT EXECUTED", html)
        self.assertIn('async function loadHarness(', html)

    def test_request_body_size_is_checked_before_read(self):
        handler = object.__new__(server.Handler)
        handler.headers = {"Content-Length": "110000"}
        handler.rfile = BytesIO(b"x" * 110000)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            handler._read_body(max_bytes=100000)
        self.assertEqual(handler.rfile.tell(), 0)


if __name__ == "__main__":
    unittest.main()
