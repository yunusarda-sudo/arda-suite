"""Run every immediate fixture entry repeatedly and retain per-run evidence."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Sequence

from scripts.capture_hermes_baseline import capture_run


def run_batch(fixture_dir: Path, evidence_dir: Path, *, command: Sequence[str],
              repeats: int, timeout_seconds: float) -> list[dict[str, object]]:
    fixture_dir = Path(fixture_dir).resolve(strict=True)
    evidence_dir = Path(evidence_dir).resolve()
    if not fixture_dir.is_dir() or not command or repeats <= 0 or timeout_seconds <= 0:
        raise ValueError("fixture directory, command, repeats and timeout must be valid")
    fixture_entries = sorted(path for path in fixture_dir.iterdir() if path.is_file() or path.is_dir())
    if not fixture_entries:
        raise ValueError("fixture directory is empty")
    runs_dir = evidence_dir / "runs"
    workspaces_dir = evidence_dir / "workspaces"
    runs_dir.mkdir(parents=True, exist_ok=True)
    workspaces_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for fixture_index, fixture in enumerate(fixture_entries):
        for repeat in range(1, repeats + 1):
            run_id = f"{fixture_index:03d}-{fixture.name}-repeat-{repeat:02d}"
            workspace = workspaces_dir / run_id
            workspace.mkdir()
            if fixture.is_dir():
                for child in fixture.iterdir():
                    target = workspace / child.name
                    if child.is_dir():
                        shutil.copytree(child, target, symlinks=True)
                    elif child.is_symlink():
                        target.symlink_to(child.readlink())
                    else:
                        shutil.copy2(child, target)
                prompt = (workspace / "prompt.txt").read_bytes() if (workspace / "prompt.txt").is_file() else b""
            else:
                shutil.copy2(fixture, workspace / fixture.name)
                prompt = fixture.read_bytes()
            result = capture_run(command, workspace=workspace, prompt=prompt, timeout_seconds=timeout_seconds)
            result["run_id"] = run_id
            result["fixture"] = fixture.name
            results.append(result)
            (runs_dir / f"{run_id}.json").write_text(
                json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (evidence_dir / "summary.json").write_text(
        json.dumps({"run_count": len(results), "results": results}, ensure_ascii=False,
                   sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    run_batch(args.fixture_dir, args.evidence_dir, command=command, repeats=args.repeats,
              timeout_seconds=args.timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
