import json
import shutil
import sqlite3

import pytest

from arda.core import bootstrap_runtime_database
from arda.recovery import RecoveryError, create_backup, restore_backup, verify_backup


def _database(path):
    db = bootstrap_runtime_database(path)
    db.execute("INSERT INTO projects(id, name, created_at, updated_at) "
               "VALUES ('p1', 'Recovery test', 'now', 'now')")
    db.close()


def test_backup_restore_verifies_core_and_artifacts_without_touching_source(tmp_path):
    database = tmp_path / "live.sqlite3"
    artifact_root = tmp_path / "live-artifacts"
    artifact_root.mkdir()
    artifact = artifact_root / "episode" / "evidence.json"
    artifact.parent.mkdir()
    artifact.write_text('{"accepted": true}\n', encoding="utf-8")
    _database(database)
    backup = tmp_path / "backups" / "pilot-001"

    manifest = create_backup(database, {"harness": artifact_root}, backup, writers_stopped=True)
    assert len(manifest["files"]) == 2
    assert verify_backup(backup)["format_version"] == 1

    restored = restore_backup(backup, tmp_path / "restore-001")
    assert (restored / "artifacts/harness/episode/evidence.json").read_bytes() == artifact.read_bytes()
    with sqlite3.connect(restored / "core.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT name FROM projects WHERE id='p1'").fetchone()[0] == "Recovery test"
    assert database.is_file() and artifact.is_file()


def test_backup_requires_writers_stopped_and_never_overwrites_destination(tmp_path):
    database = tmp_path / "live.sqlite3"
    _database(database)
    output = tmp_path / "backup"
    with pytest.raises(RecoveryError, match="stop Core"):
        create_backup(database, {}, output)
    create_backup(database, {}, output, writers_stopped=True)
    with pytest.raises(RecoveryError, match="already exists"):
        create_backup(database, {}, output, writers_stopped=True)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(RecoveryError, match="already exists"):
        restore_backup(output, occupied)


def test_backup_verification_rejects_corruption_and_path_traversal(tmp_path):
    database = tmp_path / "live.sqlite3"
    _database(database)
    backup = tmp_path / "backup"
    create_backup(database, {}, backup, writers_stopped=True)
    (backup / "core.sqlite3").write_bytes(b"corrupt")
    with pytest.raises(RecoveryError, match="hash mismatch"):
        verify_backup(backup)

    # A hostile manifest cannot turn restore into a write outside the destination.
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../../outside"
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RecoveryError, match="unsafe manifest path"):
        restore_backup(backup, tmp_path / "restore")


def test_backup_rejects_symlink_artifacts(tmp_path):
    database = tmp_path / "live.sqlite3"
    _database(database)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (artifact_root / "linked.txt").symlink_to(outside)
    with pytest.raises(RecoveryError, match="refusing special file or symlink"):
        create_backup(database, {"harness": artifact_root}, tmp_path / "backup", writers_stopped=True)


def test_backup_verification_does_not_follow_manifest_parent_symlink(tmp_path):
    database = tmp_path / "live.sqlite3"
    _database(database)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "evidence.json").write_text("{}", encoding="utf-8")
    backup = tmp_path / "backup"
    create_backup(database, {"harness": artifact_root}, backup, writers_stopped=True)

    external = tmp_path / "outside"
    external.mkdir()
    (external / "evidence.json").write_text("outside data", encoding="utf-8")
    shutil.rmtree(backup / "artifacts" / "harness")
    (backup / "artifacts" / "harness").symlink_to(external, target_is_directory=True)
    with pytest.raises(RecoveryError, match="traverses symlink"):
        verify_backup(backup)
