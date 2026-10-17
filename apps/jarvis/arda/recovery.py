"""Offline Core and evidence backup/restore helpers.

Backups are immutable directory bundles. Restore always targets a new directory;
this module intentionally has no operation that replaces a live database.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


FORMAT_VERSION = 1
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MANIFEST = "manifest.json"
_DATABASE = "core.sqlite3"


class RecoveryError(ValueError):
    """The backup is unsafe, incomplete, or cannot be restored safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            result = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise RecoveryError(f"SQLite database cannot be opened: {exc}") from exc
    if not result or result[0] != "ok":
        raise RecoveryError(f"SQLite integrity check failed: {result[0] if result else 'no result'}")


def _safe_regular_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RecoveryError(f"expected a regular file, refusing special file or symlink: {path}")
    return info


def _checked_roots(roots: dict[str, str | Path], output: Path) -> list[tuple[str, Path]]:
    checked: list[tuple[str, Path]] = []
    for label, value in sorted(roots.items()):
        if not _LABEL_RE.fullmatch(label):
            raise RecoveryError(f"invalid artifact root label: {label!r}")
        supplied_root = Path(value).expanduser().absolute()
        if supplied_root.is_symlink() or not supplied_root.is_dir():
            raise RecoveryError(f"artifact root must be a real directory: {value}")
        root = supplied_root.resolve(strict=True)
        if output == root or root in output.parents:
            raise RecoveryError(f"backup destination cannot be inside artifact root {label!r}")
        checked.append((label, root))
    return checked


def create_backup(
    database: str | Path,
    artifact_roots: dict[str, str | Path],
    destination: str | Path,
    *,
    writers_stopped: bool = False,
) -> dict[str, Any]:
    """Create an atomic offline backup; callers must stop all Core/artifact writers."""
    if not writers_stopped:
        raise RecoveryError("stop Core, workers, and artifact writers before creating a backup")
    supplied_db = Path(database).expanduser().absolute()
    if supplied_db.is_symlink():
        raise RecoveryError(f"refusing symlink database: {supplied_db}")
    source_db = supplied_db.resolve(strict=True)
    _safe_regular_file(source_db)
    target = Path(destination).expanduser().resolve(strict=False)
    if target.exists() or target.is_symlink():
        raise RecoveryError(f"backup destination already exists: {target}")
    roots = _checked_roots(artifact_roots, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A staging directory beside the destination makes the final rename atomic.
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        database_copy = staging / _DATABASE
        with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(database_copy)) as output:
            source.backup(output)
            output.execute("PRAGMA journal_mode=DELETE")
        os.chmod(database_copy, 0o600)
        _sqlite_integrity(database_copy)

        entries: list[dict[str, Any]] = [{
            "path": _DATABASE, "sha256": _sha256(database_copy), "mode": 0o600,
        }]
        artifact_dir = staging / "artifacts"
        artifact_dir.mkdir(mode=0o700)
        for label, root in roots:
            for current, dirnames, filenames in os.walk(root, followlinks=False):
                current_path = Path(current)
                for name in list(dirnames):
                    child = current_path / name
                    info = child.lstat()
                    if not stat.S_ISDIR(info.st_mode):
                        raise RecoveryError(f"refusing symlink or special directory entry: {child}")
                for name in filenames:
                    source = current_path / name
                    info = _safe_regular_file(source)
                    relative = source.relative_to(root).as_posix()
                    rel = PurePosixPath(relative)
                    if rel.is_absolute() or ".." in rel.parts:
                        raise RecoveryError(f"unsafe artifact path: {relative}")
                    destination_path = artifact_dir / label / Path(*rel.parts)
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    before = _sha256(source)
                    shutil.copyfile(source, destination_path)
                    mode = stat.S_IMODE(info.st_mode) & 0o777
                    os.chmod(destination_path, mode)
                    copied = _sha256(destination_path)
                    after_info = _safe_regular_file(source)
                    after = _sha256(source)
                    if before != copied or copied != after or info.st_size != after_info.st_size \
                            or info.st_mtime_ns != after_info.st_mtime_ns:
                        raise RecoveryError(f"artifact changed while backup was being created: {source}")
                    entries.append({
                        "path": f"artifacts/{label}/{relative}",
                        "sha256": copied,
                        "mode": mode,
                    })

        manifest = {
            "format_version": FORMAT_VERSION,
            "database": _DATABASE,
            "artifact_roots": [label for label, _ in roots],
            "files": sorted(entries, key=lambda item: item["path"]),
        }
        manifest_path = staging / _MANIFEST
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o600)
        if target.exists() or target.is_symlink():
            raise RecoveryError(f"backup destination appeared while creating backup: {target}")
        os.replace(staging, target)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_backup(backup: str | Path) -> dict[str, Any]:
    """Verify manifest paths, complete file set, hashes, and SQLite integrity."""
    supplied_root = Path(backup).expanduser().absolute()
    if supplied_root.is_symlink() or not supplied_root.is_dir():
        raise RecoveryError("backup must be a real directory")
    root = supplied_root.resolve(strict=True)
    manifest_path = root / _MANIFEST
    _safe_regular_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"invalid backup manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION \
            or manifest.get("database") != _DATABASE:
        raise RecoveryError("unsupported backup manifest format")
    files = manifest.get("files")
    labels = manifest.get("artifact_roots")
    if not isinstance(files, list) or not isinstance(labels, list) \
            or any(not isinstance(label, str) or not _LABEL_RE.fullmatch(label) for label in labels) \
            or len(set(labels)) != len(labels):
        raise RecoveryError("invalid artifact roots or file list in manifest")
    expected = {_MANIFEST}
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RecoveryError("invalid file entry in manifest")
        name = item["path"]
        rel = PurePosixPath(name)
        if rel.is_absolute() or not rel.parts or ".." in rel.parts or "\\" in name:
            raise RecoveryError(f"unsafe manifest path: {name!r}")
        if name in seen:
            raise RecoveryError(f"duplicate manifest path: {name}")
        seen.add(name)
        if name != _DATABASE and not (
            len(rel.parts) >= 3 and rel.parts[0] == "artifacts" and rel.parts[1] in labels
        ):
            raise RecoveryError(f"manifest path outside database/artifact roots: {name}")
        digest = item.get("sha256")
        mode = item.get("mode")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) \
                or not isinstance(mode, int) or mode < 0 or mode > 0o777:
            raise RecoveryError(f"invalid hash or mode for {name}")
        file_path = root.joinpath(*rel.parts)
        _safe_regular_file(file_path)
        if _sha256(file_path) != digest:
            raise RecoveryError(f"hash mismatch: {name}")
        expected.add(name)
    if _DATABASE not in seen:
        raise RecoveryError("manifest does not contain the Core database")
    actual: set[str] = set()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        for dirname in dirnames:
            if (Path(current) / dirname).is_symlink():
                raise RecoveryError(f"symlink in backup: {Path(current) / dirname}")
        for filename in filenames:
            file_path = Path(current) / filename
            _safe_regular_file(file_path)
            actual.add(file_path.relative_to(root).as_posix())
    if actual != expected:
        missing, extra = sorted(expected - actual), sorted(actual - expected)
        raise RecoveryError(f"backup file set mismatch; missing={missing}, extra={extra}")
    _sqlite_integrity(root / _DATABASE)
    return manifest


def restore_backup(backup: str | Path, destination: str | Path) -> Path:
    """Verify then restore to a new, isolated directory without replacing live state."""
    manifest = verify_backup(backup)
    source = Path(backup).expanduser().absolute().resolve(strict=True)
    target = Path(destination).expanduser().resolve(strict=False)
    if target.exists() or target.is_symlink():
        raise RecoveryError(f"restore destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
    try:
        shutil.copyfile(source / _DATABASE, staging / _DATABASE)
        os.chmod(staging / _DATABASE, 0o600)
        for entry in manifest["files"]:
            name = entry["path"]
            if name == _DATABASE:
                continue
            relative = PurePosixPath(name)
            destination_file = staging.joinpath(*relative.parts)
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source.joinpath(*relative.parts), destination_file)
            os.chmod(destination_file, entry["mode"])
        _sqlite_integrity(staging / _DATABASE)
        # Verify copied files against manifest before publishing the restore.
        for entry in manifest["files"]:
            copy_path = staging.joinpath(*PurePosixPath(entry["path"]).parts)
            if _sha256(copy_path) != entry["sha256"]:
                raise RecoveryError(f"restored file hash mismatch: {entry['path']}")
        if target.exists() or target.is_symlink():
            raise RecoveryError(f"restore destination appeared during restore: {target}")
        os.replace(staging, target)
        return target
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parse_roots(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("artifact roots must be LABEL=PATH")
        label, path = value.split("=", 1)
        if not _LABEL_RE.fullmatch(label) or not path or label in result:
            raise argparse.ArgumentTypeError(f"invalid or duplicate artifact root: {value!r}")
        result[label] = Path(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and verify offline ARDA recovery bundles")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a backup after stopping all writers")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--artifact-root", action="append", default=[], metavar="LABEL=PATH")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--writers-stopped", action="store_true",
                        help="assert Core, workers, and artifact writers have been stopped")
    verify = commands.add_parser("verify", help="verify backup hashes and SQLite integrity")
    verify.add_argument("backup", type=Path)
    restore = commands.add_parser("restore", help="restore to a new isolated directory")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_backup(args.db, _parse_roots(args.artifact_root), args.output,
                                     writers_stopped=args.writers_stopped)
            print(json.dumps({"status": "created", "path": str(args.output),
                              "files": len(manifest["files"])}, sort_keys=True))
        elif args.command == "verify":
            manifest = verify_backup(args.backup)
            print(json.dumps({"status": "verified", "files": len(manifest["files"])}, sort_keys=True))
        else:
            restored = restore_backup(args.backup, args.destination)
            print(json.dumps({"status": "restored_isolated", "path": str(restored)}, sort_keys=True))
    except (OSError, sqlite3.Error, RecoveryError, argparse.ArgumentTypeError) as exc:
        parser.exit(2, f"arda recovery: {exc}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI exercised by subprocess tests
    raise SystemExit(main())
