#!/usr/bin/env python3
"""
Universal IDE Bridge & Memory Health Score Engine for mem0-local.
Synchronizes memory rules with Cursor (.cursor/rules/*.mdc),
injects instructions into Claude Code (CLAUDE.md), generates MCP configs
for VS Code & Cline, and calculates quantitative memory health scores.
Stdlib-first: zero external dependencies.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from memory_store import read_inventory

MEM0_ROOT = Path(__file__).resolve().parent


def sync_cursor_rules(workspace_dir: str, scope: str = "personal") -> list[str]:
    """Exports mem0 rules for a given scope into Cursor .cursor/rules/<id>.mdc files."""
    ws = Path(workspace_dir).resolve()
    cursor_rules_dir = ws / ".cursor" / "rules"
    cursor_rules_dir.mkdir(parents=True, exist_ok=True)

    try:
        import rules as rules_store
        scope_rules = rules_store.list_rules(scope, include_disabled=False)
    except Exception:
        scope_rules = []

    written_files = []

    # 1. Master Always-Apply Memory-First Rule
    master_rule_path = cursor_rules_dir / "000-mem0-memory-first.mdc"
    master_content = (
        "---\n"
        'description: "MANDATORY: Always check mem0 memories and rules before modifying code"\n'
        "globs: *\n"
        "alwaysApply: true\n"
        "---\n\n"
        "# MEM0 SHARED BRAIN PROTOCOL (MANDATORY STEP 0)\n\n"
        "You are connected to the central mem0-local memory and rules store.\n"
        "You MUST ALWAYS follow this protocol automatically without waiting for user reminders:\n"
        "1. **Recall Before Coding**: Before planning or implementing any change, call `search_memory` (or run `./mem.py search \"<task>\" --scope <scope>`) to retrieve established decisions, gotchas, and constraints.\n"
        "2. **Check File Rules**: Before modifying a file, check its binding rules with `./mem.py rules check \"<path>\"`.\n"
        "3. **Record Durable Decisions**: Whenever an architectural decision or gotcha is established, call `add_memory`.\n"
        "4. **Safety & Harness**: Verify critical tasks using `execute_with_harness`.\n"
    )
    master_rule_path.write_text(master_content, encoding="utf-8")
    written_files.append(str(master_rule_path))

    # 2. Scope-specific individual rules
    for r in scope_rules:
        rid = r.get("id", "rule")
        instruction = r.get("instruction", "").strip()
        paths = r.get("paths", [])
        globs_str = ", ".join(paths) if paths else "*.*"
        desc = instruction[:80].replace("\n", " ")

        content = (
            "---\n"
            f"description: {desc}\n"
            f"globs: {globs_str}\n"
            "alwaysApply: false\n"
            "---\n\n"
            f"# Rule: {rid}\n\n"
            f"{instruction}\n"
        )
        target = cursor_rules_dir / f"{rid}.mdc"
        target.write_text(content, encoding="utf-8")
        written_files.append(str(target))

    return written_files



def sync_claude_instructions(workspace_dir: str, scope: str = "personal") -> str:
    """Idempotently injects mem0 instructions and recall directives into workspace CLAUDE.md."""
    ws = Path(workspace_dir).resolve()
    claude_md = ws / "CLAUDE.md"

    start_tag = "<!-- MEM0:START -->"
    end_tag = "<!-- MEM0:END -->"

    block = (
        f"{start_tag}\n"
        "## Shared Brain & Memory Guidelines (MANDATORY STEP 0)\n"
        "You MUST proactively and automatically follow these guidelines without waiting for user reminders:\n"
        f"- **Project Scope**: `{scope}`\n"
        f"- **Step 0 - Recall First**: ALWAYS query mem0 memories before planning or modifying code (`search_memory` tool or `./mem.py search \"<task>\" --scope {scope}`). Do not make assumptions when past decisions exist.\n"
        f"- **Durable Decisions**: ALWAYS record new architectural choices, resolved gotchas, and user preferences with `add_memory` or `./mem.py add \"<fact>\" --scope {scope}`.\n"
        f"- **Architectural Rules**: ALWAYS check file bindings before editing with `./mem.py rules check \"<path>\" --scope {scope}`.\n"
        "- **Safety & Harness**: ALWAYS verify critical or evaluation tasks via `execute_with_harness`.\n"
        f"{end_tag}"
    )

    if claude_md.is_file():
        text = claude_md.read_text(encoding="utf-8")
        if start_tag in text and end_tag in text:
            # Replace existing block
            pattern = re.compile(f"{re.escape(start_tag)}.*?{re.escape(end_tag)}", re.DOTALL)
            new_text = pattern.sub(block, text)
        else:
            # Append block
            new_text = text.rstrip() + "\n\n" + block + "\n"
    else:
        new_text = block + "\n"

    claude_md.write_text(new_text, encoding="utf-8")
    return str(claude_md)


def generate_mcp_config(
    workspace_dir: str,
    target: str = "vscode",
    python_path: Optional[str] = None,
) -> Path:
    """Generates standard MCP client configuration for VS Code, Cline, or Cursor."""
    ws = Path(workspace_dir).resolve()
    py = python_path or str(MEM0_ROOT / "bin" / "mem0-python")
    server_script = str(MEM0_ROOT / "mcp_server.py")

    config = {
        "mcpServers": {
            "mem0-local": {
                "command": py,
                "args": [server_script],
                "env": {
                    "MEM0_BACKEND": "local"
                }
            }
        }
    }

    target_clean = target.lower().strip()
    if target_clean in ("vscode", "code"):
        out_dir = ws / ".vscode"
        out_file = out_dir / "mcp.json"
    elif target_clean in ("cline", "roocode"):
        out_dir = ws / ".cline"
        out_file = out_dir / "mcp.json"
    elif target_clean == "cursor":
        out_dir = ws / ".cursor"
        out_file = out_dir / "mcp.json"
    else:
        out_dir = ws
        out_file = out_dir / "mcp.json"

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return out_file


def sync_antigravity(workspace_dir: str, scope: str = "personal", configure_global_mcp: bool = True) -> dict:
    """Configures Google Antigravity to utilize mem0-local memories, rules, skills, and MCP tools."""
    ws = Path(workspace_dir).resolve()
    actions = []

    # 1. Project Rules (GEMINI.md & AGENTS.md)
    start_tag = "<!-- MEM0:START -->"
    end_tag = "<!-- MEM0:END -->"
    block = (
        f"{start_tag}\n"
        "## Shared Brain & Memory Guidelines (MANDATORY STEP 0)\n"
        "You MUST proactively and automatically follow these guidelines without waiting for user reminders:\n"
        f"- **Project Scope**: `{scope}`\n"
        f"- **Step 0 - Recall First**: ALWAYS query mem0 memories before planning or modifying code (`search_memory` tool or `./mem.py search \"<task>\" --scope {scope}`). Do not make assumptions when past decisions exist.\n"
        f"- **Durable Decisions**: ALWAYS record new architectural choices, resolved gotchas, and user preferences with `add_memory` or `./mem.py add \"<fact>\" --scope {scope}`.\n"
        f"- **Architectural Rules**: ALWAYS check file bindings before editing with `./mem.py rules check \"<path>\" --scope {scope}`.\n"
        "- **Safety & Harness**: ALWAYS verify critical or evaluation tasks via `execute_with_harness`.\n"
        f"{end_tag}"
    )


    for filename in ["AGENTS.md", "GEMINI.md"]:
        fpath = ws / filename
        if fpath.is_file():
            text = fpath.read_text(encoding="utf-8")
            if start_tag in text and end_tag in text:
                pattern = re.compile(f"{re.escape(start_tag)}.*?{re.escape(end_tag)}", re.DOTALL)
                new_text = pattern.sub(block, text)
            else:
                new_text = text.rstrip() + "\n\n" + block + "\n"
        else:
            new_text = block + "\n"
        fpath.write_text(new_text, encoding="utf-8")
        actions.append(f"Injected memory guidelines into {fpath.name}")

    # 2. Sync rules to .agents/rules/
    agents_rules_dir = ws / ".agents" / "rules"
    agents_rules_dir.mkdir(parents=True, exist_ok=True)
    try:
        import rules as rules_store
        scope_rules = rules_store.list_rules(scope, include_disabled=False)
        for r in scope_rules:
            rid = r.get("id", "rule")
            inst = r.get("instruction", "")
            (agents_rules_dir / f"{rid}.md").write_text(f"# {rid}\n\n{inst}\n", encoding="utf-8")
        if scope_rules:
            actions.append(f"Synced {len(scope_rules)} rules to .agents/rules/")
    except Exception:
        pass

    # 3. Sync skills to .agents/skills/
    agents_skills_dir = ws / ".agents" / "skills"
    agents_skills_dir.mkdir(parents=True, exist_ok=True)
    try:
        import skills_engine
        skills = skills_engine.discover_skills()
        for s in skills:
            s_name = s.get("name")
            s_dir = agents_skills_dir / s_name
            s_dir.mkdir(parents=True, exist_ok=True)
            skill_md = s_dir / "SKILL.md"
            skill_md.write_text(s.get("raw_content") or "", encoding="utf-8")
        if skills:
            actions.append(f"Synced {len(skills)} skills to .agents/skills/")
    except Exception:
        pass

    # 4. Global MCP Server registration (~/.gemini/config/mcp_config.json)
    if configure_global_mcp:
        gemini_mcp_config = Path(os.path.expanduser("~/.gemini/config/mcp_config.json"))
        try:
            gemini_mcp_config.parent.mkdir(parents=True, exist_ok=True)
            curr = {}
            if gemini_mcp_config.is_file():
                try:
                    curr = json.loads(gemini_mcp_config.read_text(encoding="utf-8")) or {}
                except Exception:
                    curr = {}
            if "mcpServers" not in curr:
                curr["mcpServers"] = {}

            py = str(MEM0_ROOT / "bin" / "mem0-python")
            server_script = str(MEM0_ROOT / "mcp_server.py")
            curr["mcpServers"]["mem0-local"] = {
                "command": py,
                "args": [server_script],
                "env": {
                    "MEM0_BACKEND": "local"
                }
            }
            gemini_mcp_config.write_text(json.dumps(curr, indent=2) + "\n", encoding="utf-8")
            actions.append("Registered mem0-local in ~/.gemini/config/mcp_config.json")
        except Exception as ex:
            actions.append(f"Notice: Could not write ~/.gemini/config/mcp_config.json ({ex})")

    return {
        "status": "configured",
        "target": "antigravity",
        "workspace": str(ws),
        "actions": actions,
    }


def generate_universal_prompt(scope: str = "personal", task: str = "", file_paths: list[str] = None) -> str:
    """Generates an authoritative system prompt context block for ANY model (GPT-4o, Gemini, Claude, DeepSeek)."""
    import mem0_client
    cli = mem0_client.Mem0Client(scope=scope)
    return cli.get_system_prompt(task=task, file_paths=file_paths)



def evaluate_health_score(
    scope: str = "personal",
    workspace_dir: Optional[str] = None,
    memories: Optional[list[dict]] = None,
) -> dict:
    """Evaluates the structural and freshness health of the memory system for a scope."""
    ws = Path(workspace_dir).resolve() if workspace_dir else Path(".").resolve()

    if memories is None:
        owner = os.environ.get("MEM0_OWNER", "default")
        all_mem = read_inventory(owner=owner, scope=scope or None,
                                 include_superseded=True)
    else:
        all_mem = memories

    total_memories = len(all_mem)
    superseded_memories = [
        m for m in all_mem
        if (m.get("metadata") or {}).get("status") == "superseded" or m.get("status") == "superseded"
    ]
    active_memories = [
        m for m in all_mem
        if m not in superseded_memories
    ]

    # Check stale memories referencing nonexistent files
    import compaction
    sweep_res = compaction.sweep_stale_memories(
        scope=scope,
        workspace_dir=str(ws),
        dry_run=True,
        memories=all_mem,
    )
    stale_count = sweep_res.get("stale_count", 0)

    # Check active rules
    try:
        import rules as rules_store
        scope_rules = rules_store.list_rules(scope, include_disabled=False)
    except Exception:
        scope_rules = []

    # Check knowledge graph connectivity
    graph_node_count = 0
    graph_edge_count = 0
    try:
        import graph_store
        stats = graph_store.get_stats()
        graph_node_count = stats.get("total_nodes", 0)
        graph_edge_count = stats.get("total_edges", 0)
    except Exception:
        pass

    # Quantitative scoring formula (0 - 100)
    score = 100
    penalties = []
    recommendations = []

    if stale_count > 0:
        penalty = min(30, stale_count * 10)
        score -= penalty
        penalties.append(f"-{penalty} pts: {stale_count} memories reference deleted/renamed files")
        recommendations.append(f"Run `./mem.py sweep --scope {scope}` to archive stale file references.")

    if total_memories > 0:
        superseded_ratio = len(superseded_memories) / total_memories
        if superseded_ratio > 0.60:
            score -= 10
            penalties.append(f"-10 pts: High superseded ratio ({superseded_ratio:.0%})")
            recommendations.append(f"Consider running a purge or compacting older superseded memories.")

    if len(active_memories) >= 4:
        # Check if clustering finds consolidation opportunities
        clusters = compaction.cluster_memories(active_memories, min_cluster_size=2)
        if clusters:
            recommendations.append(
                f"Found {len(clusters)} clusters of related memories. Run `./mem.py compact --scope {scope}` to synthesize them."
            )

    if not scope_rules:
        score -= 10
        penalties.append("-10 pts: No active architectural rules defined for this scope")
        recommendations.append(f"Define binding constraints for code style or migrations using `./mem.py rules add`.")

    if graph_node_count > 0 and graph_edge_count == 0:
        score -= 10
        penalties.append("-10 pts: Knowledge graph has nodes but zero relational edges")
        recommendations.append("Link related memories and architectural components using `./mem.py graph link`.")

    score = max(0, min(100, score))

    # Determine status label
    if score >= 90:
        status_label = "EXCELLENT"
    elif score >= 75:
        status_label = "GOOD"
    elif score >= 50:
        status_label = "NEEDS_ATTENTION"
    else:
        status_label = "DEGRADED"

    return {
        "scope": scope,
        "score": score,
        "status": status_label,
        "metrics": {
            "total_memories": total_memories,
            "active_memories": len(active_memories),
            "superseded_memories": len(superseded_memories),
            "stale_memories": stale_count,
            "rules_count": len(scope_rules),
            "graph_nodes": graph_node_count,
            "graph_edges": graph_edge_count,
        },
        "penalties": penalties,
        "recommendations": recommendations,
    }
