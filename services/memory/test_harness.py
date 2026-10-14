"""Unit tests for harness.py (Agent Execution & Evaluation Harness)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import harness


class TestHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_trajectory_logger(self):
        logger = harness.TrajectoryLogger(run_id="test-run-1", trajectories_dir=self.tmp_path)
        step1 = logger.log_step("tool_call", {"tool": "Bash", "command": "ls -la"})
        self.assertEqual(step1["step"], 1)
        self.assertEqual(step1["action"], "tool_call")

        step2 = logger.log_step("tool_result", {"exit_code": 0})
        self.assertEqual(step2["step"], 2)

        # Verify file content
        self.assertTrue(logger.path.is_file())
        lines = [json.loads(l) for l in logger.path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["command"], "ls -la")
        self.assertEqual(lines[1]["exit_code"], 0)

    def test_assemble_context(self):
        ctx = harness.assemble_context(
            task="Refactor user database schema with Prisma",
            scope="personal",
        )
        self.assertEqual(ctx["task"], "Refactor user database schema with Prisma")
        self.assertEqual(ctx["scope"], "personal")
        self.assertIn("skills", ctx)

    @patch("harness.run_command")
    def test_run_harness_task_does_not_execute_verifier_or_claim_success(self, mock_run_cmd):
        mock_run_cmd.return_value = (0, "checks passed")
        res = harness.run_harness_task(
            task="Implement the requested change",
            scope="personal",
            verify_command="touch /tmp/harness-verifier-was-run",
        )
        mock_run_cmd.assert_not_called()
        self.assertEqual(res["status"], "not_executed")
        self.assertFalse(res["execution_performed"])
        self.assertFalse(res["verified"])
        self.assertFalse(res["verification_passed"])
        self.assertEqual(res["verification_status"], "not_run")
        self.assertFalse(res["reflexion_triggered"])

        trajectory = [
            json.loads(line)
            for line in Path(res["trajectory_file"]).read_text().splitlines()
            if line.strip()
        ]
        task_end = next(step for step in trajectory if step["action"] == "task_end")
        self.assertEqual(task_end["status"], "not_executed")
        self.assertFalse(task_end["execution_performed"])
        self.assertFalse(task_end["verified"])
        self.assertEqual(task_end["verification_status"], "not_run")

    @patch("harness.run_command")
    def test_run_harness_task_without_verifier_does_not_claim_success(self, mock_run_cmd):
        res = harness.run_harness_task(task="Make a change", scope="personal")
        mock_run_cmd.assert_not_called()
        self.assertEqual(res["status"], "not_executed")
        self.assertFalse(res["execution_performed"])
        self.assertFalse(res["verified"])
        self.assertEqual(res["verification_status"], "not_run")

    def test_run_harness_task_rejects_oversized_prompt(self):
        with self.assertRaisesRegex(ValueError, "character limit"):
            harness.run_harness_task(task="x" * (harness.MAX_TASK_LENGTH + 1))

    def test_run_harness_task_rejects_oversized_scope(self):
        with self.assertRaisesRegex(ValueError, "character limit"):
            harness.run_harness_task(task="Task", scope="x" * (harness.MAX_SCOPE_LENGTH + 1))

    def test_run_command_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "no trusted executor"):
            harness.run_command("echo must not run")


if __name__ == "__main__":
    unittest.main()
