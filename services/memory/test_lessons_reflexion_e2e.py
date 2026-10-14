"""End-to-end scanner -> pipeline -> Reflexion test, isolated from optional deps."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

MEMORY_ROOT = Path(__file__).resolve().parent


CHILD_DRIVER = r"""
import json
import os
import sys
import types

# The test replaces persistence and the LLM. Stub optional import-time SDKs only
# when they are absent; all detector, pipeline, and Reflexion code stays real.
try:
    import mem0
except ImportError:
    mem0 = types.ModuleType("mem0")
    mem0.Memory = object
    sys.modules["mem0"] = mem0
try:
    import qdrant_client
except ImportError:
    qdrant_client = types.ModuleType("qdrant_client")
    qdrant_client.QdrantClient = object
    qdrant_http = types.ModuleType("qdrant_client.http")
    qdrant_models = types.ModuleType("qdrant_client.http.models")
    qdrant_models.PayloadSchemaType = types.SimpleNamespace(KEYWORD="keyword")
    qdrant_client.http = qdrant_http
    qdrant_http.models = qdrant_models
    sys.modules["qdrant_client"] = qdrant_client
    sys.modules["qdrant_client.http"] = qdrant_http
    sys.modules["qdrant_client.http.models"] = qdrant_models

import lessons_detect
import pipeline
import reflexion_engine

transcript, pending_log, verified_log, durable_path, result_path = sys.argv[1:]
lessons_detect.PENDING_LOG = pending_log
lessons_detect.VERIFIED_LOG = verified_log
reflexion_engine.VERIFIED_LOG = verified_log
reflexion_engine.query_ollama_reflexion = lambda command, files, project: (
    "Preserve the authentication guard when editing the route."
)

class MemoryStore:
    def add(self, memory, **kwargs):
        with open(durable_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"memory": memory, **kwargs}, ensure_ascii=False) + "\n")

reflexion_engine.get_memory = lambda: MemoryStore()
detected = lessons_detect.scan(
    transcript, "sample-app", "2026-10-14T09:03:00Z", dry_run=False
)
stage = pipeline.stage_reflexion(dry_run=False)
with open(result_path, "w", encoding="utf-8") as f:
    json.dump({"detected": detected, "stage": stage}, f, ensure_ascii=False)
"""


def _event(timestamp, tool_id, tool_name, tool_input, result=None):
    content = [{"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}]
    if result is not None:
        content.append({"type": "tool_result", "tool_use_id": tool_id, **result})
    return {"timestamp": timestamp, "message": {"content": content}}


def _write_transcript(path, include_edit=True):
    events = [
        _event("2026-10-14T09:00:00Z", "test-fail", "Bash", {"command": "pytest tests/test_auth.py"},
               {"is_error": True, "content": "AssertionError: expected authenticated response"}),
    ]
    if include_edit:
        events.append(_event("2026-10-14T09:01:00Z", "fix", "Edit", {"file_path": "src/auth.py"},
                             {"is_error": False, "content": "updated"}))
    events.append(_event("2026-10-14T09:02:00Z", "test-pass", "Bash", {"command": "pytest tests/test_auth.py"},
                         {"is_error": False, "content": "1 passed"}))
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def _run_chain(tmp, transcript):
    paths = {
        name: os.path.join(tmp, name)
        for name in ("pending.jsonl", "verified.jsonl", "memory-store.jsonl", "result.json")
    }
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MEMORY_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", CHILD_DRIVER, transcript, paths["pending.jsonl"],
         paths["verified.jsonl"], paths["memory-store.jsonl"], paths["result.json"]],
        check=False, capture_output=True, text=True, env=env, timeout=20,
    )
    if completed.returncode:
        raise AssertionError(f"E2E child failed: {completed.stderr}\n{completed.stdout}")
    with open(paths["result.json"], encoding="utf-8") as f:
        result = json.load(f)
    return paths, result


class TestLessonsReflexionE2E(unittest.TestCase):
    def test_failure_fix_transcript_becomes_durable_lesson_through_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = os.path.join(tmp, "session-abc.jsonl")
            _write_transcript(transcript)

            paths, result = _run_chain(tmp, transcript)

            self.assertEqual(len(result["detected"]["verified"]), 1)
            self.assertEqual(result["detected"]["verified"][0]["edited_files"], ["src/auth.py"])
            self.assertEqual(result["stage"]["status"], "ok")
            self.assertEqual(result["stage"]["gotchas_created"], 1)
            with open(paths["memory-store.jsonl"], encoding="utf-8") as f:
                stored = [json.loads(line) for line in f]
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["memory"],
                             "[GOTCHA] Preserve the authentication guard when editing the route.")
            self.assertEqual(stored[0]["run_id"], "sample-app")
            self.assertEqual(stored[0]["agent_id"], "reflexion")
            self.assertFalse(stored[0]["infer"])
            with open(paths["verified.jsonl"], encoding="utf-8") as f:
                verified = [json.loads(line) for line in f]
            self.assertTrue(verified[0]["processed"])
            self.assertIn("Preserve the authentication guard", verified[0]["lesson"])

    def test_failure_then_pass_without_edit_does_not_create_or_store_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = os.path.join(tmp, "session-flaky.jsonl")
            _write_transcript(transcript, include_edit=False)

            paths, result = _run_chain(tmp, transcript)

            self.assertEqual(result["detected"]["verified"], [])
            self.assertFalse(os.path.exists(paths["verified.jsonl"]))
            self.assertEqual(result["stage"]["status"], "ok")
            self.assertEqual(result["stage"]["gotchas_created"], 0)
            self.assertFalse(os.path.exists(paths["memory-store.jsonl"]))


if __name__ == "__main__":
    unittest.main()
