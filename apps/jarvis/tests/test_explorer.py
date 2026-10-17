"""M2.4A deterministic repository explorer and independent evaluation tests."""

import json
import sqlite3
from pathlib import Path

import pytest

from arda.core import Database
from arda.explorer import ExplorerEvaluation, ExplorerStore, LexicalExplorer, evaluate_run


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def _fixture_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "arda").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "arda" / "queue.py").write_text(
        "class JobQueue:\n    def claim_job(self):\n        return 'lease'\n", encoding="utf-8"
    )
    (root / "arda" / "unrelated.py").write_text(
        "class Greeting:\n    def hello(self):\n        return 'hello'\n", encoding="utf-8"
    )
    (root / ".git" / "ignored.py").write_text("claim_job", encoding="utf-8")
    return root


def test_same_snapshot_and_config_produce_byte_identical_canonical_result(tmp_path):
    root = _fixture_repo(tmp_path)
    explorer = LexicalExplorer()
    first = explorer.run(root, "claim job lease", repository_commit="abc123")
    second = explorer.run(root, "claim job lease", repository_commit="abc123")

    assert first.canonical_hash == second.canonical_hash
    assert first.canonical_payload() == second.canonical_payload()
    assert first.run_id != second.run_id
    assert first.created_at != ""


def test_explorer_is_bounded_and_preserves_region_provenance(tmp_path):
    root = _fixture_repo(tmp_path)
    run = LexicalExplorer().run(root, "claim job", max_files=1, max_lines=2, max_region_lines=2)

    assert run.selected_files == ("arda/queue.py",)
    assert len(run.selected_regions) == 1
    region = run.selected_regions[0]
    assert region["path"] == "arda/queue.py"
    assert len(region["blob_hash"]) == 64
    assert region["start_line"] <= region["end_line"]
    assert "claim_job" in region["content"]
    assert run.context_budget["max_lines"] == 2
    assert run.resource_usage["regions_selected"] == 1


def test_explorer_does_not_mutate_repository_or_execute_shell(tmp_path):
    root = _fixture_repo(tmp_path)
    before = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))
    LexicalExplorer().run(root, "greeting")
    after = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))
    assert after == before


def test_run_and_evaluation_are_separate_immutable_records(tmp_path, db):
    root = _fixture_repo(tmp_path)
    store = ExplorerStore(db)
    run = LexicalExplorer().run(root, "claim job", store=store)
    evaluation = ExplorerEvaluation(
        evaluation_id="evaluation_1", explorer_run_id=run.run_id,
        evaluator_version="fixture-oracle-v1", oracle_source="fixture",
        file_recall_at_k=1.0, line_recall=1.0, context_efficiency=0.5,
        verification_status="complete",
    )
    store.save_evaluation(evaluation)
    assert db.scalar("SELECT COUNT(*) FROM explorer_runs") == 1
    assert db.scalar("SELECT COUNT(*) FROM explorer_evaluations") == 1
    assert json.loads(db.execute("SELECT canonical_result FROM explorer_runs").fetchone()[0])["selected_files"]
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE explorer_runs SET strategy_id='tampered' WHERE run_id=?", (run.run_id,))
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("DELETE FROM explorer_evaluations WHERE evaluation_id='evaluation_1'")


def test_fixture_oracle_evaluation_is_independent_and_persistable(db):
    fixture_root = Path(__file__).parent / "fixtures" / "m2_4a"
    tasks = json.loads((fixture_root / "tasks.json").read_text(encoding="utf-8"))
    store = ExplorerStore(db)
    explorer = LexicalExplorer()

    evaluations = []
    for task in tasks:
        run = explorer.run(fixture_root / "repo", task["objective"], store=store)
        evaluation = evaluate_run(run, task)
        store.save_evaluation(evaluation)
        evaluations.append(evaluation)

    assert len(evaluations) == 4
    assert all(e.verification_status == "complete" for e in evaluations)
    assert db.scalar("SELECT COUNT(*) FROM explorer_runs") == 4
    assert db.scalar("SELECT COUNT(*) FROM explorer_evaluations") == 4
    assert all(e.file_recall_at_k is not None for e in evaluations)
    assert all(e.line_recall is not None for e in evaluations)


def test_migrations_are_idempotent(db):
    assert db.migrate() == []
    assert db.scalar("SELECT MAX(version) FROM schema_migrations") == 23
