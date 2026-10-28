#!/usr/bin/env python3
"""Prepare scoped context and record Harness trajectories for mem0-local.

Task execution, command verification, and Reflexion persistence are disabled
until a trusted executor and evidence-backed verification path are implemented.
"""

import datetime
import json
import os
import time
import uuid
from pathlib import Path

TRAJECTORIES_DIR = Path(os.path.expanduser("~/.mem0/trajectories"))
MAX_TASK_LENGTH = 20_000
MAX_SCOPE_LENGTH = 256

def run_command(command: str, cwd: str = None, timeout: int = 60) -> tuple[int, str]:
    """Refuse command execution until a trusted, shell-free executor is available."""
    raise RuntimeError("Harness command execution is disabled; no trusted executor is configured.")


class TrajectoryLogger:
    """Appends structured execution steps to ~/.mem0/trajectories/<run_id>.jsonl."""

    def __init__(self, run_id: str = None, trajectories_dir: Path = None):
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
        self.dir = trajectories_dir or TRAJECTORIES_DIR
        try:
            self.dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.path = self.dir / f"{self.run_id}.jsonl"
        except OSError:
            # Fallback to current working directory
            self.dir = Path(".") / ".trajectories"
            self.dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.path = self.dir / f"{self.run_id}.jsonl"
        self.step_index = 0

    def log_step(self, action: str, details: dict) -> dict:
        self.step_index += 1
        entry = {
            "run_id": self.run_id,
            "step": self.step_index,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            **details,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def assemble_context(task: str, scope: str = "personal", file_paths: list[str] = None) -> dict:
    """Aggregates scoped memories, binding rules, matched skills, and knowledge graph relations."""
    context = {
        "task": task,
        "scope": scope,
        "memories": "",
        "rules": "",
        "skills": "",
        "graph": [],
        "source_errors": [],
    }

    # 1. Scoped memories via recall_inject
    try:
        import recall_inject
        rendered_mem, _ = recall_inject.collect(scope)
        context["memories"] = rendered_mem
    except Exception:
        context["source_errors"].append("memories")

    # 2. Binding rules
    try:
        import rules
        matched_rules = []
        for fp in (file_paths or [""]):
            matched_rules.extend(rules.match_rules_for_file(scope, fp))
        if matched_rules:
            # Dedup
            seen = set()
            unique_rules = []
            for r in matched_rules:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    unique_rules.append(r)
            context["rules"] = rules.render(unique_rules, scope)
    except Exception:
        context["source_errors"].append("rules")

    # 3. Active skills
    try:
        import skills_engine
        matched_skills = skills_engine.match_skills_for_task(task, file_paths)
        context["skills"] = skills_engine.render_skills_prompt(matched_skills)
    except Exception:
        context["source_errors"].append("skills")

    # 4. Graph multi-hop relations
    try:
        import graph_store
        entities = graph_store.extract_entities(task)
        start_nodes = [f"entity:{e}" for e in entities]
        if start_nodes:
            discovered = graph_store.multi_hop_query(start_nodes, max_hops=1)
            context["graph"] = discovered
    except Exception:
        context["source_errors"].append("graph")

    context["status"] = "partial" if context["source_errors"] else "complete"

    return context


def run_harness_task(
    task: str,
    scope: str = "personal",
    verify_command: str = None,
    cwd: str = None,
    file_paths: list[str] = None,
) -> dict:
    """Prepare context and record a trajectory without executing the requested task.

    ``verify_command`` and ``cwd`` remain accepted for API compatibility, but are
    intentionally never executed. A trusted task executor must exist before this
    harness can report execution or verification success.
    """
    if len(task or "") > MAX_TASK_LENGTH:
        raise ValueError(f"task exceeds the {MAX_TASK_LENGTH} character limit")
    if len(scope or "") > MAX_SCOPE_LENGTH:
        raise ValueError(f"scope exceeds the {MAX_SCOPE_LENGTH} character limit")
    logger = TrajectoryLogger()
    logger.log_step("task_start", {"task": task, "scope": scope})

    # Assemble context
    ctx = assemble_context(task, scope=scope, file_paths=file_paths)
    logger.log_step("context_assembled", {
        "has_memories": bool(ctx.get("memories")),
        "has_rules": bool(ctx.get("rules")),
        "has_skills": bool(ctx.get("skills")),
        "graph_entities_count": len(ctx.get("graph", [])),
        "context_status": ctx.get("status", "unknown"),
        "context_source_errors": ctx.get("source_errors", []),
    })

    status = "not_executed"
    verified = False
    verification_output = ""
    reflexion_triggered = False
    message = f"Harness attempted context assembly ({ctx.get('status', 'unknown')}) and recorded a trajectory; no task executor is configured, so neither task nor verifier was run."
    logger.log_step("task_end", {
        "status": status,
        "execution_performed": False,
        "verified": False,
        "verification_status": "not_run",
        "message": message,
        "context_status": ctx.get("status", "unknown"),
    })

    return {
        "status": status,
        "run_id": logger.run_id,
        "trajectory_file": str(logger.path),
        "safety_passed": None,
        "safety_status": "not_applicable",
        "safety_violation": "",
        "execution_performed": False,
        "verified": verified,
        "verification_passed": verified,
        "verification_status": "not_run",
        "verification_output": verification_output,
        "reflexion_triggered": reflexion_triggered,
        "message": message,
        "context": ctx,
    }

