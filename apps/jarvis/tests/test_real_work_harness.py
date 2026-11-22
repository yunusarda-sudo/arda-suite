from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from arda.autonomous_work_loop import AgentReport, EvaluationReport, WorkResult, WorkTask
from arda.real_work_harness import RealWorkHarness, TaskValidityReport


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, sha


class Memory:
    def __init__(self):
        self.recalled = []
        self.stored = []

    def recall(self, query, *, limit=5):
        self.recalled.append(query)
        return [{"memory": "verify final state after last mutation"}]

    def store(self, lesson):
        self.stored.append(lesson)
        return "memory-1"


class Agent:
    def __init__(self):
        self.contexts = []
        self.repairs = 0

    def run(self, task, workspace, *, context=""):
        self.contexts.append(context)
        (workspace / "source.txt").write_text("accepted\n", encoding="utf-8")
        return AgentReport("done", final_claim="done", tool_calls=1)

    def repair(self, task, workspace, failure, *, context=""):
        self.repairs += 1
        (workspace / "source.txt").write_text("accepted\n", encoding="utf-8")
        return AgentReport("done", final_claim="repaired", tool_calls=1)


class CountingAgent(Agent):
    def run(self, task, workspace, *, context=""):
        self.calls = getattr(self, "calls", 0) + 1
        return super().run(task, workspace, context=context)


class Evaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, task, workspace):
        self.calls += 1
        return EvaluationReport(self.calls > 1, error=None if self.calls > 1 else "first attempt failed")


def test_real_work_promotes_only_pass_and_stores_lesson(tmp_path):
    repo, sha = _repo(tmp_path)
    memory = Memory()
    agent = Agent()
    evaluator = Evaluator()
    harness = RealWorkHarness(repo=repo, accepted_ref="arda/harness-v1", workspace_root=tmp_path / "worktrees",
                              artifact_root=tmp_path / "artifacts", episode_db=tmp_path / "episodes.sqlite3",
                              agent=agent, evaluator=evaluator, memory=memory)
    subprocess.run(["git", "branch", "arda/harness-v1", sha], cwd=repo, check=True)
    task = WorkTask("task-001", str(repo), "main", sha, "change source", ("passes",), allowed_paths=("source.txt",),
                    task_type="bug-fix", affected_subsystem="source")

    outcome = harness.run([task])[0]

    assert outcome.result is WorkResult.PASS
    assert outcome.attempts == 2
    assert outcome.candidate_sha
    assert subprocess.check_output(["git", "rev-parse", "arda/harness-v1"], cwd=repo, text=True).strip() == sha
    assert memory.recalled and memory.stored == []
    assert list((tmp_path / "memory-jobs").glob("*.json"))
    import sqlite3
    with sqlite3.connect(tmp_path / "episodes.sqlite3") as db:
        row = db.execute("SELECT result, accepted_sha, memory_refs FROM episodes").fetchone()
    assert row[0] == "PASS"
    assert row[1] is None
    assert row[2] == "[]"
    assert Path(outcome.artifact_path).exists()
    assert not list((tmp_path / "worktrees").glob("task-001-*"))


def test_validity_gate_skips_already_satisfied_without_agent_or_baseline_advance(tmp_path):
    repo, sha = _repo(tmp_path)
    agent = CountingAgent()
    subprocess.run(["git", "branch", "arda/harness-v1", sha], cwd=repo, check=True)
    harness = RealWorkHarness(
        repo=repo, accepted_ref="arda/harness-v1", workspace_root=tmp_path / "worktrees",
        artifact_root=tmp_path / "artifacts", episode_db=tmp_path / "episodes.sqlite3",
        agent=agent, evaluator=Evaluator(),
        validity_checker=lambda task, workspace: TaskValidityReport(
            valid=False, already_satisfied=True,
            reason="baseline already satisfies the requested behavior",
            evidence={"probe": "source.txt", "observed": "accepted"}),
    )
    task = WorkTask("task-002", str(repo), "main", sha, "change source", ("source changes",),
                    allowed_paths=("source.txt",))

    outcome = harness.run([task])[0]

    assert outcome.result is WorkResult.SKIP_ALREADY_SATISFIED
    assert outcome.attempts == 0
    assert not hasattr(agent, "calls")
    assert subprocess.check_output(["git", "rev-parse", "arda/harness-v1"], cwd=repo, text=True).strip() == sha
    payload = json.loads(Path(outcome.artifact_path).read_text(encoding="utf-8"))
    assert payload["validity"]["already_satisfied"] is True
    assert payload["validity"]["evidence"]["probe"] == "source.txt"


def test_validity_gate_rejects_non_concrete_acceptance_without_agent(tmp_path):
    repo, sha = _repo(tmp_path)
    agent = CountingAgent()
    subprocess.run(["git", "branch", "arda/harness-v1", sha], cwd=repo, check=True)
    harness = RealWorkHarness(
        repo=repo, accepted_ref="arda/harness-v1", workspace_root=tmp_path / "worktrees",
        artifact_root=tmp_path / "artifacts", episode_db=tmp_path / "episodes.sqlite3",
        agent=agent, evaluator=Evaluator(),
    )
    task = WorkTask("task-003", str(repo), "main", sha, "change source", (" ",),
                    allowed_paths=("source.txt",))

    outcome = harness.run([task])[0]

    assert outcome.result is WorkResult.INVALID
    assert outcome.attempts == 0
    assert not hasattr(agent, "calls")


@pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
@pytest.mark.parametrize("passing_attempt", [1, 2], ids=["first-pass", "repair-pass"])
def test_evaluator_changes_outside_allowlist_cannot_be_promoted(tmp_path, tracked, passing_attempt):
    repo, sha = _repo(tmp_path)
    unexpected_path = "unexpected.txt"
    if tracked:
        (repo / unexpected_path).write_text("protected baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", unexpected_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "track protected file"], cwd=repo, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "branch", "arda/harness-v1", sha], cwd=repo, check=True)

    class MutatingEvaluator:
        def __init__(self):
            self.calls = 0

        def evaluate(self, task, workspace):
            self.calls += 1
            if self.calls < passing_attempt:
                return EvaluationReport(False, error="repair required")
            (workspace / unexpected_path).write_text("evaluator changed this\n", encoding="utf-8")
            return EvaluationReport(True)

    evaluator = MutatingEvaluator()
    agent = Agent()
    harness = RealWorkHarness(
        repo=repo, accepted_ref="arda/harness-v1", workspace_root=tmp_path / "worktrees",
        artifact_root=tmp_path / "artifacts", episode_db=tmp_path / "episodes.sqlite3",
        agent=agent, evaluator=evaluator,
    )
    task = WorkTask(
        "task-evaluator-scope", str(repo), "main", sha, "change source", ("source changes",),
        allowed_paths=("source.txt",),
    )

    outcome = harness.run([task])[0]

    assert evaluator.calls == passing_attempt
    assert agent.repairs == passing_attempt - 1
    assert outcome.result is WorkResult.FAIL
    assert outcome.candidate_sha is None
    assert subprocess.check_output(
        ["git", "rev-parse", "arda/harness-v1"], cwd=repo, text=True,
    ).strip() == sha
    evidence = json.loads(Path(outcome.artifact_path).read_text(encoding="utf-8"))
    assert unexpected_path in evidence["changed_paths"]
    assert evidence["accepted_sha"] is None
    assert evidence["candidate_sha"] is None


def test_cleanup_failure_cannot_leave_pass_evidence_or_episode(tmp_path, monkeypatch):
    import sqlite3
    from arda.autonomous_work_loop import GitWorktree

    repo, sha = _repo(tmp_path)
    subprocess.run(["git", "branch", "arda/harness-v1", sha], cwd=repo, check=True)
    harness = RealWorkHarness(
        repo=repo, accepted_ref="arda/harness-v1", workspace_root=tmp_path / "worktrees",
        artifact_root=tmp_path / "artifacts", episode_db=tmp_path / "episodes.sqlite3",
        agent=Agent(), evaluator=Evaluator(),
    )
    task = WorkTask("task-cleanup", str(repo), "main", sha, "change source", ("passes",),
                    allowed_paths=("source.txt",))

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("cleanup failed")

    monkeypatch.setattr(GitWorktree, "remove", fail_cleanup)
    outcome = harness.run([task])[0]

    evidence = json.loads(Path(outcome.artifact_path).read_text(encoding="utf-8"))
    with sqlite3.connect(tmp_path / "episodes.sqlite3") as db:
        row = db.execute("SELECT result, accepted_sha FROM episodes").fetchone()
    assert outcome.result is WorkResult.INVALID
    assert outcome.candidate_sha is None
    assert evidence["result"] == WorkResult.INVALID.value
    assert evidence["reason"] == "cleanup failed"
    assert evidence["candidate_sha"] is None
    assert row == (WorkResult.INVALID.value, None)
    assert subprocess.check_output(["git", "rev-parse", "arda/harness-v1"], cwd=repo, text=True).strip() == sha
