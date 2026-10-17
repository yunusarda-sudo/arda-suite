"""M1.5 read-only diagnostics tests."""

import json
from datetime import datetime, timezone

from arda.__main__ import main
from arda.core import Database, EventLog
from arda.episodes import EpisodeStore
from arda.task_intake import TaskIntakeRequest


def _db(path):
    db = Database(path)
    db.migrate()
    db.execute(
        """INSERT INTO projects(id, name, status, created_at, updated_at)
           VALUES ('sample-project', 'Opvia', 'active', ?, ?)""",
        ("2026-10-01T08:00:00+00:00", "2026-10-01T08:00:00+00:00"),
    )
    EventLog(db).append("scheduler.tick.completed", source="scheduler")
    EventLog(db).append(
        "github.sync.completed", project_id="sample-project", source="github",
        source_id="acme/sample-project", payload={"repo": "acme/sample-project"},
    )
    db.close()


def test_status_projects_jobs_events_are_read_only(tmp_path, capsys):
    path = tmp_path / "arda.sqlite3"
    _db(path)
    before = path.stat().st_mtime_ns

    main(["status", "--db", str(path)])
    main(["projects", "--db", str(path)])
    main(["jobs", "--db", str(path)])
    main(["events", "--db", str(path), "--limit", "3"])

    output = capsys.readouterr().out
    assert "projects: 1" in output
    assert "sample-project" in output
    assert "scheduler.tick.completed" in output
    assert path.stat().st_mtime_ns == before


def test_github_status_reports_observed_sync_state(tmp_path, capsys, monkeypatch):
    path = tmp_path / "arda.sqlite3"
    _db(path)
    monkeypatch.setenv("GITHUB_TOKEN", "configured-only-for-diagnostics")

    main(["github", "status", "--db", str(path), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["provider"] == "github"
    assert result["configured"] is True
    assert result["mapped_projects"] == 1
    assert result["stale"] is False


def test_doctor_is_read_only_and_reports_unknown_backup(tmp_path, capsys):
    path = tmp_path / "arda.sqlite3"
    _db(path)
    before_db = Database(path)
    before_events = before_db.scalar("SELECT COUNT(*) FROM events")
    before_db.close()

    main(["doctor", "--db", str(path), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert result["sqlite"]["ok"] is True
    assert result["migrations"]["current"] == 21
    assert result["event_log"]["ok"] is True
    assert result["backup"]["status"] == "unknown"
    check = Database(path)
    assert check.scalar("SELECT COUNT(*) FROM events") == before_events
    check.close()


def test_diagnostics_do_not_create_missing_database(tmp_path, capsys):
    path = tmp_path / "missing.sqlite3"

    main(["doctor", "--db", str(path)])

    assert "SQLite erişilemiyor" in capsys.readouterr().out
    assert not path.exists()


def test_episode_diagnostics_are_read_only(tmp_path, capsys):
    path = tmp_path / "episodes.sqlite3"
    db = Database(path)
    db.migrate()
    request = TaskIntakeRequest(
        intake_id="intake-diagnostics",
        human_goal="Inspect project",
        task_type="diagnostic",
        objective="Inspect project state",
        risk="low",
        privacy="private",
        source_ref="test:diagnostics",
        acceptance_criteria=("report state",),
    )
    EpisodeStore(db).create(
        request, task_id="task-diagnostics", episode_id="episode-diagnostics",
        revision_id="revision-diagnostics",
    )
    db.close()
    before = path.stat().st_mtime_ns

    main(["episodes", "--db", str(path), "--json"])
    episodes_output = capsys.readouterr().out
    main(["episode", "episode-diagnostics", "--db", str(path), "--json"])
    episode_output = capsys.readouterr().out

    episodes = json.loads(episodes_output)
    episode = json.loads(episode_output)
    assert episodes[0]["episode_id"] == "episode-diagnostics"
    assert episode["events"][0]["event_type"] == "episode.created"
    assert path.stat().st_mtime_ns == before


def test_capability_and_provider_diagnostics_are_read_only(tmp_path, capsys):
    from arda.capabilities import CapabilityDescriptor, ProviderDescriptor, SQLiteCapabilityStore

    path = tmp_path / "registry.sqlite3"
    db = Database(path)
    db.migrate()
    store = SQLiteCapabilityStore(db)
    store.append_capability(CapabilityDescriptor(
        capability_id="verification.alignment",
        version="0.1",
        description="alignment evidence",
        input_contract="AlignmentInput-v0.1",
        output_contract="AlignmentEvaluation-v0.1",
    ))
    store.append_provider(ProviderDescriptor(
        provider_id="local-qwen",
        provider_kind="local_model",
        identity={"model": "qwen3:1.7b"},
        capabilities=("verification.alignment",),
    ))
    db.close()

    main(["capabilities", "--db", str(path), "--json"])
    capabilities = json.loads(capsys.readouterr().out)
    main(["providers", "--db", str(path), "--json"])
    providers = json.loads(capsys.readouterr().out)
    assert capabilities[0]["capability_id"] == "verification.alignment"
    assert providers[0]["provider_id"] == "local-qwen"
    assert providers[0]["evaluation_count"] == 0
