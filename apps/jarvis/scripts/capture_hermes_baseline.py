"""Observe one Hermes process without adding ARDA tools or changing its argv."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Sequence


def _manifest_digest(manifest: list[dict[str, object]]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def workspace_manifest(workspace: Path) -> list[dict[str, object]]:
    root = Path(workspace).resolve(strict=True)
    entries: list[dict[str, object]] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        base = Path(current)
        for name in [*dirs, *files]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if path.is_symlink():
                target = os.readlink(path)
                entries.append({"path": relative, "type": "symlink",
                                "sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
                                "mode": info.st_mode & 0o777})
                if name in dirs:
                    dirs.remove(name)
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                entries.append({"path": relative, "type": "file", "sha256": digest.hexdigest(),
                                "size_bytes": info.st_size, "mode": info.st_mode & 0o777})
            elif path.is_dir():
                entries.append({"path": relative, "type": "directory", "mode": info.st_mode & 0o777})
            else:
                entries.append({"path": relative, "type": "special", "mode": info.st_mode & 0o777})
    return sorted(entries, key=lambda item: str(item["path"]))


def capture_run(command: Sequence[str], *, workspace: Path, prompt: bytes,
                timeout_seconds: float) -> dict[str, object]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("command must be a non-empty argv")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    workspace = Path(workspace).resolve(strict=True)
    before = workspace_manifest(workspace)
    started = time.monotonic()
    process = subprocess.Popen(list(command), cwd=workspace, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=(os.name == "posix"))
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
    after = workspace_manifest(workspace)
    return {
        "command": list(command), "timeout_seconds": timeout_seconds,
        "duration_seconds": time.monotonic() - started,
        "exit_status": None if timed_out else process.returncode, "timed_out": timed_out,
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "stdout": stdout[-65536:].decode("utf-8", "replace"),
        "stderr": stderr[-65536:].decode("utf-8", "replace"),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "workspace_before": before, "workspace_after": after,
        "workspace_before_hash": _manifest_digest(before),
        "workspace_after_hash": _manifest_digest(after),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    result = capture_run(command, workspace=args.workspace, prompt=args.prompt_file.read_bytes(),
                         timeout_seconds=args.timeout_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
