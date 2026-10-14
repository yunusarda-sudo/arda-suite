#!/usr/bin/env python3
"""
Shared Brain Control Panel — local, self-hosted dashboard.

Backend uses ONLY the Python standard library plus the local `mem0` install.
No Flask/FastAPI, no pip additions. Run with the mem0-local venv python:

    $HOME/mem0-local/.venv/bin/python server.py

Serves a self-contained (offline) dashboard on http://localhost:8899
"""

import os
import json
import re
import sys
import time
import shutil
import threading
import traceback
import urllib.request
import urllib.error
import subprocess
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# --- Local imports (assets + mem0 config) -----------------------------------
HERE = Path(__file__).resolve().parent
MEM0_ROOT = HERE.parent
sys.path.insert(0, str(HERE))          # for assets.py
sys.path.insert(0, str(MEM0_ROOT))     # for config.py

from assets import PAGE_HTML  # noqa: E402
from arch_page import ARCH_PAGE_HTML  # noqa: E402  — one architecture, its own URL
import architectures  # noqa: E402  — the only module that knows the arch store
import runner  # noqa: E402  — background agent runs

# --- Configuration ----------------------------------------------------------
PORT = int(os.environ.get("MEM0_DASHBOARD_PORT", 8899))
HOST = os.environ.get("MEM0_DASHBOARD_HOST", "127.0.0.1")
USER_ID = os.environ.get("MEM0_OWNER", "default")
SHARED_SCOPE = "personal"  # the global "shared brain" tier

HOME = Path.home()
CLAUDE_HOME = HOME / ".claude"
CODEX_HOME = HOME / ".codex"

SKILL_ROOTS = [
    CLAUDE_HOME / "skills",
    CLAUDE_HOME / "plugins",
    CODEX_HOME / "skills",
    CODEX_HOME / "memories" / "skills",
    CODEX_HOME / "plugins",
    CODEX_HOME / "vendor_imports" / "skills",
]
AGENT_DIRS = [CLAUDE_HOME / "agents", CODEX_HOME / "agents"]
# Plugins ship their own sub-agents; the Agent tool dispatches them as
# "<plugin>:<agent>". Both directory names are in the wild (codex-advisor uses
# claude-agents/ so the same plugin can also carry Codex-flavoured agents).
AGENT_PLUGIN_ROOTS = [CLAUDE_HOME / "plugins", CODEX_HOME / "plugins"]
AGENT_DIR_NAMES = ("agents", "claude-agents")
RULE_DIRS = [CLAUDE_HOME / "rules", CODEX_HOME / "rules"]
CLAUDE_JSON = Path.home() / ".claude.json"
CODEX_CONFIG = CODEX_HOME / "config.toml"
MCP_JSON_CONFIGS = [
    ("Claude", CLAUDE_JSON, "mcpServers"),
    ("Cursor", HOME / ".cursor" / "mcp.json", "mcpServers"),
    ("VS Code", HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json", "servers"),
    (
        "Cline",
        HOME
        / "Library"
        / "Application Support"
        / "Code"
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev"
        / "settings"
        / "cline_mcp_settings.json",
        "mcpServers",
    ),
]

OLLAMA_URL = "http://localhost:11434/api/tags"
QDRANT_URL = "http://localhost:6333/collections/mem0_local"

# --- Projects tab -----------------------------------------------------------
PROJECTS_ROOT = HOME / "Projeler"
ACTIVITY_LOG = MEM0_ROOT / "projects-activity.jsonl"   # written by the git hook
USAGE_LOG = MEM0_ROOT / "usage.jsonl"                   # written by the usage hook
INSTRUCTION_FILES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md"}  # editable instruction bridges
SETTINGS_FILES = [                                           # Claude settings holding hooks
    ("global", CLAUDE_HOME / "settings.json"),
    ("global-local", CLAUDE_HOME / "settings.local.json"),
]
CLAUDE_PROJECTS = CLAUDE_HOME / "projects"             # per-project session data
PROJECTS_TTL = 30                                       # seconds to cache the scan
_projects_cache = {"ts": 0.0, "data": None}
_US = "\x1f"                                            # unit separator for git --pretty

# --- Code map tab (graphify --code-only output) ------------------------------
EXTRA_REPOS = (MEM0_ROOT,)                 # repos to expose that live outside PROJECTS_ROOT
GRAPH_REL = Path("graphify-out") / "graph.json"
ANALYSIS_REL = Path("graphify-out") / ".graphify_analysis.json"
BUILD_CMD = "graphify extract . --code-only"

# Clicking a symbol in the Code Map opens its file in a desktop editor via a URL
# scheme. Which editor is a machine preference rather than a repo fact, so it
# comes from the environment; keeping the templates in one dict makes adding an
# editor a single line. {path} is absolute, {line} a bare number.
EDITOR_URLS = {
    "vscode": "vscode://file{path}:{line}",
    "code": "vscode://file{path}:{line}",
    "cursor": "cursor://file{path}:{line}",
    "windsurf": "windsurf://file{path}:{line}",
    "zed": "zed://file{path}:{line}",
    "idea": "idea://open?file={path}&line={line}",
    "sublime": "subl://open?url=file://{path}&line={line}",
}
DEFAULT_EDITOR = "vscode"
_SHA_RE = re.compile(r"\A[0-9a-f]{7,40}\Z")
_codemap_cache = {}                        # resolved repo path -> (key tuple, payload)

MAX_SCAN_DEPTH = 10  # plugin skills nest deep (marketplace/plugin/skills/name/SKILL.md)
EDIT_ROOTS = [
    CLAUDE_HOME,
    CODEX_HOME,
    MEM0_ROOT / "mcp-snippets",
]
EDIT_FILES = [
    CLAUDE_JSON,
    CODEX_CONFIG,
    HOME / ".cursor" / "mcp.json",
    HOME / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
    HOME
    / "Library"
    / "Application Support"
    / "Code"
    / "User"
    / "globalStorage"
    / "saoudrizwan.claude-dev"
    / "settings"
    / "cline_mcp_settings.json",
]

# --- mem0 lazy singleton ----------------------------------------------------
# mem0 init spins up Ollama connections and can be slow; build it once, lazily,
# so /api/health stays fast and independent of memory backend availability.
_memory = None
_memory_lock = threading.Lock()


def get_memory():
    global _memory
    if _memory is None:
        with _memory_lock:
            if _memory is None:
                import config
                _memory = config.get_memory()
    return _memory


# --- Small helpers ----------------------------------------------------------
def _http_json(url, timeout=3):
    """GET a URL and return parsed JSON, or raise."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_frontmatter(path):
    """Minimal YAML frontmatter reader — extracts name + description only."""
    out = {"name": None, "description": None}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    if not text.startswith("---"):
        return out
    lines = text.splitlines()
    # find closing fence
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return out
    for line in lines[1:end]:
        for key in ("name", "description"):
            prefix = key + ":"
            if line.startswith(prefix):
                val = line[len(prefix):].strip()
                if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                    val = val[1:-1]
                out[key] = val
    return out


def _normalize_memories(raw):
    items = []
    if isinstance(raw, dict):
        items = raw.get("results", [])
    elif isinstance(raw, list):
        items = raw
    for it in items:
        if isinstance(it, dict) and "status" not in it:
            it["status"] = (it.get("metadata") or {}).get("status") or "active"
    return items


# Roots under which the dashboard may CREATE / DELETE files (narrower than the
# edit roots — only the AI-context surfaces the UI manages: rules/skills/agents).
CREATE_ROOTS = [
    CLAUDE_HOME / "rules",
    CLAUDE_HOME / "skills",
    CLAUDE_HOME / "agents",
    CODEX_HOME / "rules",
    CODEX_HOME / "skills",
    CODEX_HOME / "agents",
]


def _within(path, roots):
    return any(
        path.is_relative_to(r.expanduser().resolve())
        for r in roots if r.exists()
    )


def _allowed_path(path_value, must_exist=True):
    if not path_value:
        raise ValueError("path is required")
    path = Path(path_value).expanduser().resolve()
    allowed = any(path == p.expanduser().resolve() for p in EDIT_FILES if p.exists())
    allowed = allowed or _within(path, EDIT_ROOTS)
    # instruction bridges (CLAUDE.md / AGENTS.md) inside a project under ~/Projeler
    if not allowed and path.name in INSTRUCTION_FILES:
        try:
            allowed = path.is_relative_to(PROJECTS_ROOT.resolve())
        except Exception:
            allowed = False
    if not allowed:
        raise PermissionError(f"path is outside editable AI context roots: {path}")
    if must_exist and (not path.exists() or not path.is_file()):
        raise FileNotFoundError(str(path))
    return path


def read_content(path_value):
    path = _allowed_path(path_value)
    return {
        "path": str(path),
        "name": path.name,
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "editable": True,
    }


def write_content(path_value, content):
    path = _allowed_path(path_value)
    if content is None:
        raise ValueError("content is required")
    path.write_text(str(content), encoding="utf-8")
    return {"ok": True, "path": str(path), "bytes": path.stat().st_size}


def create_item(kind, name, folder, content):
    """Create a new rule/agent/skill under the managed Claude create-roots.

    kind='rule'  -> ~/.claude/rules/<folder or 'custom'>/<name>.md
    kind='agent' -> ~/.claude/agents/<name>.md
    kind='skill' -> ~/.claude/skills/<name>/SKILL.md
    """
    name = (name or "").strip().strip("/")
    if not name:
        raise ValueError("name is required")
    if "/" in name or ".." in name or name.startswith("."):
        raise ValueError("name must be a simple name (no slashes)")
    if kind == "rule":
        sub = (folder or "").strip().strip("/")
        leaf = name if name.endswith(".md") else name + ".md"
        if sub in ("", "(root)"):
            path = CLAUDE_HOME / "rules" / leaf
        else:
            if ".." in sub:
                raise ValueError("invalid folder")
            path = CLAUDE_HOME / "rules" / sub / leaf
    elif kind == "agent":
        leaf = name if name.endswith(".md") else name + ".md"
        path = CLAUDE_HOME / "agents" / leaf
    elif kind == "skill":
        path = CLAUDE_HOME / "skills" / name / "SKILL.md"
    else:
        raise ValueError(f"unknown kind: {kind}")
    path = path.resolve()
    if not _within(path, CREATE_ROOTS):
        raise PermissionError(f"outside create roots: {path}")
    if path.exists():
        raise FileExistsError(f"already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or "", encoding="utf-8")
    return {"ok": True, "path": str(path), "kind": kind}


def delete_path(path_value):
    """Delete a rule/agent file, or a whole skill directory, under create-roots."""
    if not path_value:
        raise ValueError("path is required")
    path = Path(path_value).expanduser().resolve()
    if not _within(path, CREATE_ROOTS):
        raise PermissionError(f"can only delete under rules/skills/agents: {path}")
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return {"ok": True, "path": str(path)}


def add_mcp_server(name, command, args, env):
    """Add a stdio MCP server to the GLOBAL Claude config (~/.claude.json)."""
    name = (name or "").strip()
    command = (command or "").strip()
    if not name:
        raise ValueError("server name is required")
    if not command:
        raise ValueError("command is required")
    if not CLAUDE_JSON.exists():
        raise FileNotFoundError(str(CLAUDE_JSON))
    data = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
    servers = data.setdefault("mcpServers", {})
    if name in servers:
        raise FileExistsError(f"MCP server '{name}' already exists")
    if isinstance(args, str):
        args = args.split()
    entry = {"type": "stdio", "command": command, "args": args or [], "env": env or {}}
    servers[name] = entry
    CLAUDE_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "name": name, "restart_note": "restart the AI tool to load it"}


def remove_mcp_server(name, config_path):
    """Remove an MCP server by name from a given config file (Claude/Cursor/etc.)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("server name is required")
    path = Path(config_path).expanduser().resolve() if config_path else CLAUDE_JSON
    valid = {p.expanduser().resolve() for _, p, _ in MCP_JSON_CONFIGS}
    if path not in valid:
        raise PermissionError(f"not a known MCP config: {path}")
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    key = next((k for _, p, k in MCP_JSON_CONFIGS if p.expanduser().resolve() == path), "mcpServers")
    removed = False
    if name in (data.get(key) or {}):
        del data[key][name]
        removed = True
    # also sweep Claude project-scoped servers
    for proj in (data.get("projects") or {}).values():
        if name in (proj.get("mcpServers") or {}):
            del proj["mcpServers"][name]
            removed = True
    if not removed:
        raise FileNotFoundError(f"MCP server '{name}' not found in {path.name}")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "name": name, "restart_note": "restart the AI tool to apply"}


# --- Data readers -----------------------------------------------------------
def vector_store_transport(qdrant, docker):
    """Reconcile the two things that both claim to describe the vector store.

    Qdrant used to run as the `mem0-qdrant` container; it now runs from the
    vendored binary in `bin/`. The container check outlived the container, so a
    healthy machine reported a stopped Docker service as a fault on every load.

    A permanent red light nobody can act on is worse than no light — it teaches
    you to skim the row it sits in, including the day something in it is real.
    So: if Qdrant answers, how it is served is a fact, not a failure. If Qdrant
    does *not* answer, the container's real state is the first useful clue and
    keeps its own status.

    Pure on purpose — the probes stay in read_health, the judgement is testable.
    """
    if qdrant.get("status") != "up":
        return qdrant, docker
    if docker.get("status") == "up":
        return dict(qdrant, transport="docker"), docker
    return (dict(qdrant, transport="native"),
            {"status": "unused",
             "detail": "not in use — Qdrant is running natively from bin/qdrant"})


def read_health():
    health = {}

    # Ollama
    try:
        data = _http_json(OLLAMA_URL, timeout=3)
        models = [m.get("name") for m in data.get("models", [])]
        health["ollama"] = {"status": "up", "models": models, "count": len(models)}
    except Exception as e:
        health["ollama"] = {"status": "down", "error": str(e)}

    # Qdrant
    try:
        data = _http_json(QDRANT_URL, timeout=3)
        result = data.get("result", {})
        health["qdrant"] = {
            "status": "up",
            "points": result.get("points_count"),
            "state": result.get("status"),
        }
    except Exception as e:
        health["qdrant"] = {"status": "down", "error": str(e)}

    # Docker container
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", "name=mem0-qdrant", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        status = proc.stdout.strip()
        restart_proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", "mem0-qdrant"],
            capture_output=True, text=True, timeout=5,
        )
        health["docker"] = {
            "status": "up" if status else "down",
            "detail": status or "container not running",
            "restart": restart_proc.stdout.strip() or "unknown",
        }
    except Exception as e:
        health["docker"] = {"status": "unknown", "error": str(e)}

    health["qdrant"], health["docker"] = vector_store_transport(
        health["qdrant"], health["docker"])

    # Aggregate stats (cheap counts, no mem0 init unless already built)
    try:
        skills = read_skills()
        health["skills_count"] = len(skills)
    except Exception:
        health["skills_count"] = None
    try:
        health["agents_count"] = len(read_agents())
    except Exception:
        health["agents_count"] = None
    try:
        health["rules_count"] = read_rules().get("count")
    except Exception:
        health["rules_count"] = None
    try:
        health["mcp_count"] = read_mcp().get("count")
    except Exception:
        health["mcp_count"] = None
    try:
        health["architectures_count"] = architectures.count_architectures()
    except Exception:
        health["architectures_count"] = None

    return health


# get_all()'s cap is the keyword-only `top_k` (default 20). Passing `limit=` instead
# lands in **kwargs and is silently ignored — which is why the panel used to show 20
# of 45 memories and the scope/agent facet counts were wrong too.
MEMORY_LIST_MAX = 1000


def read_memories(scope=None, agent=None):
    m = get_memory()
    raw = m.get_all(filters={"user_id": USER_ID}, top_k=MEMORY_LIST_MAX)
    items = _normalize_memories(raw)

    scopes = {}
    agents = {}
    for it in items:
        s = it.get("run_id") or "(none)"
        a = it.get("agent_id") or "(none)"
        scopes[s] = scopes.get(s, 0) + 1
        agents[a] = agents.get(a, 0) + 1

    if scope:
        items = [it for it in items if (it.get("run_id") or "(none)") == scope]
    if agent:
        items = [it for it in items if (it.get("agent_id") or "(none)") == agent]

    # newest first
    items.sort(key=lambda it: it.get("created_at") or "", reverse=True)

    return {
        "results": items,
        "scopes": scopes,
        "agents": agents,
        "shared_scope": SHARED_SCOPE,
        "total": sum(scopes.values()),
    }


def _merge_hits(batches, top_k, include_superseded=False):
    best = {}
    for hits in batches:
        for item in hits:
            status = item.get("status") or (item.get("metadata") or {}).get("status") or "active"
            if not include_superseded and status == "superseded":
                continue
            key = item.get("id") or item.get("memory")
            prev = best.get(key)
            if prev is None or (item.get("score") or 0) > (prev.get("score") or 0):
                best[key] = item
    merged = list(best.values())
    merged.sort(key=lambda item: item.get("score") or 0, reverse=True)
    return merged[:top_k]


def search_memories(query, scope=None, agent=None, top_k=20, include_superseded=False):
    m = get_memory()
    scopes = [None] if not scope else [scope]
    if scope and scope != SHARED_SCOPE:
        scopes.append(SHARED_SCOPE)

    batches = []
    for scoped in scopes:
        filters = {"user_id": USER_ID}
        if scoped:
            filters["run_id"] = scoped
        if agent:
            filters["agent_id"] = agent
        try:
            raw = m.search(query, filters=filters, top_k=top_k)
        except TypeError:
            raw = m.search(query, filters=filters, limit=top_k)
        batches.append(_normalize_memories(raw))

    return {
        "results": _merge_hits(batches, top_k, include_superseded=include_superseded),
        "query": query,
        "shared_scope": SHARED_SCOPE,
        "layered": bool(scope and scope != SHARED_SCOPE),
    }


def add_memory(text, scope, agent):
    m = get_memory()
    scope = scope or SHARED_SCOPE
    agent = agent or "claude-code"
    result = m.add(text, user_id=USER_ID, run_id=scope, agent_id=agent)
    return {"ok": True, "result": result, "scope": scope, "agent": agent}


def delete_memory(memory_id):
    m = get_memory()
    m.delete(memory_id)
    return {"ok": True, "id": memory_id}


def update_memory(memory_id, text=None, status=None):
    if not memory_id:
        raise ValueError("id is required")
    if text is None and status is None:
        raise ValueError("text or status is required")
    m = get_memory()
    kwargs = {}
    if text is not None and str(text).strip():
        kwargs["text"] = str(text).strip()
    if status is not None:
        kwargs["metadata"] = {"status": status}
    result = m.update(memory_id, **kwargs)
    return {"ok": True, "id": memory_id, "result": result}


def supersede_memory_item(old_id, new_text=None, scope=None, agent=None):
    if not old_id:
        raise ValueError("id is required")
    m = get_memory()
    scope = scope or SHARED_SCOPE
    agent = agent or "claude-code"
    if new_text:
        res = m.add(new_text, user_id=USER_ID, run_id=scope, agent_id=agent,
                    metadata={"status": "active", "supersedes": old_id})
        new_id = (res.get("results") or [{}])[0].get("id", "")
        m.update(old_id, metadata={"status": "superseded", "superseded_by": new_id})
        return {"ok": True, "old_id": old_id, "new_id": new_id, "result": res}
    else:
        result = m.update(old_id, metadata={"status": "superseded"})
        return {"ok": True, "id": old_id, "result": result}


# --- Harness Studio helpers --------------------------------------------------
def read_harness_context(task: str, scope: str = "personal"):
    import harness
    import graph_store
    ctx = harness.assemble_context(task or "", scope=scope or SHARED_SCOPE)
    hubs = graph_store.get_centrality_nodes(top_k=8, scope=scope)
    return {
        "task": task,
        "scope": scope,
        "context": ctx,
        "hubs": hubs,
    }


def run_harness_execution(task: str, scope: str = "personal", verify_command: str = None, cwd: str = None):
    import harness
    exec_cwd = cwd or str(MEM0_ROOT)
    result = harness.run_harness_task(
        task=task,
        scope=scope or SHARED_SCOPE,
        verify_command=verify_command,
        cwd=exec_cwd,
    )
    return result


def list_harness_trajectories():
    import harness
    trajectories_dir = harness.TRAJECTORIES_DIR
    if not trajectories_dir.exists():
        trajectories_dir = Path(MEM0_ROOT) / ".trajectories"
    if not trajectories_dir.exists():
        return {"trajectories": []}

    runs = []
    for p in sorted(trajectories_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:30]:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if not lines:
                continue
            first = json.loads(lines[0])
            last = json.loads(lines[-1])
            runs.append({
                "run_id": first.get("run_id", p.stem),
                "task": first.get("task", "(unknown task)"),
                "scope": first.get("scope", "personal"),
                "status": last.get("status", "unknown"),
                "step_count": len(lines),
                "created_at": first.get("timestamp", ""),
                "file": str(p),
            })
        except Exception:
            continue
    return {"trajectories": runs}


def read_harness_trajectory_detail(run_id: str):
    import harness
    trajectories_dir = harness.TRAJECTORIES_DIR
    target = trajectories_dir / f"{run_id}.jsonl"
    if not target.exists():
        target = Path(MEM0_ROOT) / ".trajectories" / f"{run_id}.jsonl"
    if not target.exists():
        raise FileNotFoundError(f"Trajectory '{run_id}' not found")

    steps = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            steps.append(json.loads(line))
    return {"run_id": run_id, "steps": steps}


STALE_DIR_SUFFIXES = (".bak", ".backup", ".old", ".disabled")
_VERSION_PART = re.compile(r"^(v?\d+[\w.\-+]*|unknown|latest)$", re.I)


def _is_stale_path(path):
    """Backup/disabled copies of a marketplace or plugin dir are not real installs."""
    return any(p.endswith(STALE_DIR_SUFFIXES) for p in path.parts)


def _owning_plugin(path, marker="skills"):
    """Plugin a skill belongs to, or '' for a standalone ~/.claude/skills entry.

    `marker` is the container directory to walk left from — "skills" for skills,
    "agents"/"claude-agents" for sub-agents, which nest identically.

    Layouts seen in the wild:
      plugins/marketplaces/<mkt>/plugins/<plugin>/skills/<name>/SKILL.md
      plugins/marketplaces/<mkt>/external_plugins/<plugin>/skills/<name>/SKILL.md
      plugins/marketplaces/<mkt>/skills/<name>/SKILL.md          (mkt IS the plugin)
      plugins/cache/<mkt>/<plugin>/<version>/skills/<name>/SKILL.md
    """
    parts = path.parts
    if "plugins" not in parts:
        return ""
    try:
        i = len(parts) - 1 - parts[::-1].index(marker)
    except ValueError:
        return ""
    # Walk left past version-ish components to the plugin directory itself.
    while i > 0 and (i == len(parts) - 1 or _VERSION_PART.match(parts[i - 1])):
        i -= 1
        if not _VERSION_PART.match(parts[i]):
            break
    owner = parts[i - 1] if i > 0 else ""
    return "" if owner in ("plugins", "external_plugins", "cache", "marketplaces") else owner


def _version_key(path):
    """Sortable version tuple from a cache path like .../<plugin>/6.2.0/skills/...

    Returns (0,) when no version-looking component is present, so unversioned
    copies never outrank a real release.
    """
    best = (0,)
    for part in path.parts:
        m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", part)
        if m:
            best = max(best, tuple(int(x) for x in m.group(1).split(".")))
    return best


def read_skills(query=None):
    # Dedup on identity (root, plugin, skill name) — NOT on path. The same skill
    # is present under both plugins/marketplaces/<mkt> (the checkout) and
    # plugins/cache/<mkt>/<plugin>/<version> (the installed copy), which used to
    # list it 2-3x. Same-named skills in *different* plugins (imessage/telegram/
    # discord all ship "configure") stay separate — the plugin is part of the key.
    best = {}
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        root_depth = len(root.parts)
        for path in root.rglob("SKILL.md"):
            if len(path.parts) - root_depth > MAX_SCAN_DEPTH:
                continue
            if _is_stale_path(path):
                continue
            fm = parse_frontmatter(path)
            name = fm["name"] or path.parent.name
            root_label = "codex" if str(path).startswith(str(CODEX_HOME)) else "claude"
            plugin = _owning_plugin(path)
            key = (root_label, plugin, name.lower())
            # The installed cache copy is what actually runs; prefer it. Several
            # versions of a plugin can sit in the cache at once (an upgrade leaves
            # the old tree behind), so rank by version too — otherwise which copy
            # wins depends on directory walk order.
            rank = (1 if "cache" in path.parts else 0, _version_key(path))
            if key in best and best[key][0] >= rank:
                continue
            source = root_label + (f"/plugins:{plugin}" if plugin else "/skills")
            best[key] = (rank, {
                "name": name,
                "description": fm["description"] or "",
                "source": source,
                "path": str(path),
            })
    skills = [v[1] for v in best.values()]
    skills.sort(key=lambda s: (s["name"].lower(), s["source"]))
    if query:
        q = query.lower()
        skills = [s for s in skills
                  if q in s["name"].lower() or q in s["description"].lower()]
    return skills


def _agent_key(name):
    """Fold the spellings of one agent onto a single counter.

    The usage log records whatever the caller typed: a plugin agent shows up
    both bare ("codex-advisor") and qualified ("codex-advisor:codex-advisor").
    Same agent, one badge. Case is preserved on purpose — agent ids are
    case-sensitive, and the built-in "Explore" is not the plugin "explore".
    """
    return (name or "").split(":")[-1].strip()


def _agent_files():
    """(path, plugin) for every sub-agent definition on disk."""
    for root in AGENT_DIRS:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md")):
            yield path, ""
    for root in AGENT_PLUGIN_ROOTS:
        if not root.exists():
            continue
        root_depth = len(root.parts)
        for path in sorted(root.rglob("*.md")):
            if path.parent.name not in AGENT_DIR_NAMES:
                continue
            if len(path.parts) - root_depth > MAX_SCAN_DEPTH:
                continue
            if _is_stale_path(path):
                continue
            yield path, _owning_plugin(path, path.parent.name)


def read_agents():
    """Every sub-agent the Agent tool can dispatch.

    Three origins, only the first of which is editable from the UI:
    managed files under ~/.claude/agents, plugin-shipped definitions, and
    built-ins (general-purpose, Explore, …) that have no file anywhere and are
    therefore only visible through the usage log.
    """
    # Dedup on identity, not path — a plugin sits in the marketplace checkout
    # and in the installed cache copy at once (same reasoning as read_skills).
    best = {}
    for path, plugin in _agent_files():
        fm = parse_frontmatter(path)
        base = fm["name"] or path.stem
        root_label = "codex" if str(path).startswith(str(CODEX_HOME)) else "claude"
        key = (root_label, plugin, base.lower())
        rank = (1 if "cache" in path.parts else 0, _version_key(path))
        if key in best and best[key][0] >= rank:
            continue
        best[key] = (rank, {
            # Qualified name is what the Agent tool actually accepts for a
            # plugin agent, so show it that way.
            "name": f"{plugin}:{base}" if plugin else base,
            "key": _agent_key(base),
            "description": fm["description"] or "",
            "source": root_label + (f"/plugins:{plugin}" if plugin else "/agents"),
            "path": str(path),
            "count": 0,
        })
    agents = [v[1] for v in best.values()]

    # Attach each usage count to exactly ONE card, so a name several plugins
    # ship (code-reviewer) doesn't stamp the same 13× on all of them.
    claims = {}
    for a in agents:
        claims.setdefault(a["key"], []).append(a)
    usage = read_usage()
    for key, count in usage["agents"].items():
        cards = claims.get(key, [])
        managed = [c for c in cards if c["source"].endswith("/agents")]
        if managed:
            target = managed[0]
        elif len(cards) == 1:
            target = cards[0]
        elif cards:
            # Ambiguous: several plugins ship this name and the log doesn't say
            # which ran. Leave it to the "most used" line rather than guessing.
            continue
        else:
            target = {
                "name": usage["agent_labels"].get(key, key),
                "key": key,
                "description": "Built-in agent — no definition file on disk.",
                "source": "built-in",
                "path": "",
                "count": 0,
            }
            agents.append(target)
        target["count"] = target.get("count", 0) + count

    agents.sort(key=lambda a: (a["name"].lower(), a["source"]))
    return agents


def _server_command(cfg):
    cmd = cfg.get("command", "")
    args = cfg.get("args", [])
    if isinstance(args, str):
        args = [args]
    return (cmd + " " + " ".join(str(a) for a in args)).strip() if cmd else cfg.get("type", "")


def read_mcp():
    servers = []

    def collect(mcp_dict, origin, config_path=None):
        for name, cfg in (mcp_dict or {}).items():
            servers.append({
                "name": name,
                "origin": origin,
                "command": _server_command(cfg),
                "type": cfg.get("type", "stdio"),
                "path": str(config_path) if config_path else "",
            })

    errors = []
    for label, path, key in MCP_JSON_CONFIGS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue
        collect(data.get(key), label, path)
        if label == "Claude":
            for proj_path, proj in (data.get("projects") or {}).items():
                pm = proj.get("mcpServers")
                if pm:
                    collect(pm, "Claude/" + (Path(proj_path).name or proj_path), path)

    if CODEX_CONFIG.exists():
        try:
            import tomllib
            data = tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8"))
            collect(data.get("mcp_servers"), "Codex", CODEX_CONFIG)
        except Exception as e:
            errors.append(f"Codex: {e}")

    # de-dup by (name, origin)
    uniq = {}
    for s in servers:
        uniq[(s["name"], s["origin"])] = s
    out = sorted(uniq.values(), key=lambda s: (s["origin"] != "global", s["name"].lower()))
    return {"servers": out, "count": len(out), "errors": errors}


def read_usage():
    """Aggregate skill/agent invocation counts from the usage log."""
    skills, agents = {}, {}
    sessions = set()
    last = {}
    labels = {}  # normalized agent key -> shortest spelling seen, for display
    if USAGE_LOG.exists():
        try:
            for line in USAGE_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                name, typ = e.get("name"), e.get("type")
                if not name:
                    continue
                bucket = skills if typ == "skill" else agents if typ == "agent" else None
                if bucket is None:
                    continue
                if typ == "agent":
                    # Agents fold onto one key; skills keep their plugin
                    # namespace, where "plugin:skill" is the real identity.
                    key = _agent_key(name)
                    labels[key] = min(labels.get(key, name), name, key=len)
                    name = key
                bucket[name] = bucket.get(name, 0) + 1
                last[(typ, name)] = max(last.get((typ, name), ""), e.get("ts", ""))
                if e.get("session"):
                    sessions.add(e["session"])
        except Exception:
            pass

    def rank(d, typ):
        return sorted(
            ({"name": labels.get(k, k) if typ == "agent" else k,
              "count": v,
              "last": last.get((typ, k), "")} for k, v in d.items()),
            key=lambda x: (-x["count"], x["name"].lower()),
        )

    return {
        "skills": skills,
        "agents": agents,
        "agent_labels": labels,
        "top_skills": rank(skills, "skill"),
        "top_agents": rank(agents, "agent"),
        "total": sum(skills.values()) + sum(agents.values()),
        "sessions": len(sessions),
    }


def read_hooks():
    """Collect configured hooks from Claude settings (global + per-project)."""
    entries = []

    def collect(source, path, data):
        hooks = (data or {}).get("hooks") or {}
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                continue
            for g in groups:
                matcher = g.get("matcher", "")
                for h in (g.get("hooks") or []):
                    entries.append({
                        "event": event,
                        "matcher": matcher,
                        "type": h.get("type", "command"),
                        "command": h.get("command") or h.get("prompt") or h.get("url") or "",
                        "async": bool(h.get("async") or h.get("asyncRewake")),
                        "timeout": h.get("timeout"),
                        "source": source,
                        "path": str(path),
                    })

    def load(source, path):
        if path.exists():
            try:
                collect(source, path, json.loads(path.read_text(encoding="utf-8")))
            except Exception as e:
                entries.append({"event": "(parse error)", "matcher": "", "command": str(e),
                                "source": source, "path": str(path)})

    for source, p in SETTINGS_FILES:
        load(source, p)
    if PROJECTS_ROOT.exists():
        try:
            for d in sorted(PROJECTS_ROOT.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                for fn in ("settings.json", "settings.local.json"):
                    load(d.name, d / ".claude" / fn)
        except OSError:
            pass

    # group by event, preserving a sensible order
    order = ["PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop",
             "UserPromptSubmit", "PreCompact", "PostCompact", "Notification"]
    by_event = {}
    for e in entries:
        by_event.setdefault(e["event"], []).append(e)
    events = [k for k in order if k in by_event] + [k for k in by_event if k not in order]
    return {"groups": {k: by_event[k] for k in events}, "count": len(entries)}


def read_instructions():
    """List editable CLAUDE.md / AGENTS.md bridges: global + per-project."""
    items = []
    for scope, p in (("global", CLAUDE_HOME / "CLAUDE.md"),
                     ("codex", CODEX_HOME / "AGENTS.md")):
        if p.exists():
            items.append({"name": p.name, "scope": scope, "path": str(p)})
    if PROJECTS_ROOT.exists():
        try:
            for d in sorted(PROJECTS_ROOT.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                for fn in ("CLAUDE.md", "AGENTS.md"):
                    fp = d / fn
                    if fp.exists():
                        items.append({"name": d.name + "/" + fn, "scope": d.name, "path": str(fp)})
        except OSError:
            pass
    return {"items": items, "count": len(items)}


def read_rules():
    groups = {}
    for root in RULE_DIRS:
        if not root.exists():
            continue
        label = "codex" if str(root).startswith(str(CODEX_HOME)) else "claude"
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root)
            folder = rel.parts[0] if len(rel.parts) > 1 else "(root)"
            groups.setdefault(label + "/" + folder, []).append({
                "name": path.name,
                "path": str(path),
            })
    return {"groups": groups, "count": sum(len(v) for v in groups.values())}


# --- Projects data ----------------------------------------------------------
def _git_run(repo, *args, timeout=5):
    """Run a read-only git command. Returns the CompletedProcess, or None on failure.

    Callers pass a fixed argument list — never a shell string, and never a value
    taken from a file or query string without validating it first.
    """
    try:
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def _git(repo, *args, timeout=5):
    """stdout of a successful git command, or "" on any failure."""
    p = _git_run(repo, *args, timeout=timeout)
    return p.stdout.strip() if p is not None and p.returncode == 0 else ""


def _git_ok(repo, *args, timeout=5):
    """Whether a git command succeeded — for commands run purely for their exit code."""
    p = _git_run(repo, *args, timeout=timeout)
    return p is not None and p.returncode == 0


def _load_activity():
    """Read the append-only activity log, grouped by project name."""
    by_project = {}
    if not ACTIVITY_LOG.exists():
        return by_project
    try:
        for line in ACTIVITY_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            by_project.setdefault(e.get("project", "?"), []).append(e)
    except Exception:
        pass
    return by_project


def _last_session_time(project_path):
    """Newest Claude session-transcript mtime for this project (epoch or None)."""
    d = CLAUDE_PROJECTS / str(project_path).replace("/", "-")
    if not d.exists():
        return None
    latest = 0.0
    try:
        for f in d.rglob("*.jsonl"):
            m = f.stat().st_mtime
            if m > latest:
                latest = m
    except Exception:
        pass
    return latest or None


def _project_info(repo, activity):
    name = repo.name
    is_git = (repo / ".git").exists()
    info = {"name": name, "path": str(repo), "is_git": is_git}
    if is_git:
        info["branch"] = _git(repo, "branch", "--show-current")
        last = _git(repo, "log", "-1", "--pretty=%h" + _US + "%an" + _US + "%cI" + _US + "%ar" + _US + "%s")
        if last:
            p = last.split(_US)
            info["last_commit"] = {
                "hash": p[0] if len(p) > 0 else "",
                "author": p[1] if len(p) > 1 else "",
                "iso": p[2] if len(p) > 2 else "",
                "rel": p[3] if len(p) > 3 else "",
                "subject": p[4] if len(p) > 4 else "",
            }
        info["dirty"] = len([l for l in _git(repo, "status", "--porcelain").splitlines() if l])
        info["remote"] = _git(repo, "remote", "get-url", "origin")
        counts = _git(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if counts:
            try:
                behind, ahead = counts.split()
                info["behind"], info["ahead"] = int(behind), int(ahead)
            except Exception:
                pass
    acts = activity.get(name, [])
    if acts:
        info["last_activity"] = acts[-1]
        info["activity_count"] = len(acts)
    st = _last_session_time(repo)
    if st:
        info["last_session"] = st
    return info


def _recency_key(p):
    keys = []
    la = p.get("last_activity")
    if la:
        keys.append(la.get("ts", ""))
    lc = p.get("last_commit")
    if lc:
        keys.append(lc.get("iso", ""))
    if p.get("last_session"):
        keys.append("s" + str(p["last_session"]))
    return max(keys) if keys else ""


def _allowed_roots():
    """Roots a caller may reach. Computed per call so tests can patch the globals."""
    roots = []
    for root in (PROJECTS_ROOT, *EXTRA_REPOS):
        try:
            roots.append(root.resolve())
        except OSError:
            continue
    return roots


def iter_repo_candidates(*, include_extra=True):
    """Every repo the dashboard exposes: PROJECTS_ROOT children plus EXTRA_REPOS.

    Paths are resolved and de-duplicated, so a repo reachable through both a
    symlink under PROJECTS_ROOT and its real location appears exactly once.

    `include_extra=False` restricts discovery to PROJECTS_ROOT children only —
    the Projects tab's original scope, before EXTRA_REPOS existed for the Code
    Map tab. The Code Map tab (and _resolve_repo) keep the default True.
    """
    seen, found = set(), []

    def add(path):
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.is_dir() or resolved in seen:
            return
        seen.add(resolved)
        found.append(resolved)

    if PROJECTS_ROOT.is_dir():
        try:
            for child in sorted(PROJECTS_ROOT.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    add(child)
        except OSError:
            pass
    if include_extra:
        for extra in EXTRA_REPOS:
            add(extra)
    return found


def _resolve_repo(path_value):
    """Resolve a caller-supplied repo path, or refuse it.

    strict=True so a non-existent path raises instead of resolving to a
    fictional location, and resolution happens before the containment test so a
    symlink inside an allowed root that points outside it is rejected.
    """
    if not path_value:
        raise ValueError("path is required")
    repo = Path(path_value).expanduser().resolve(strict=True)
    if not repo.is_dir():
        raise ValueError("not a directory: %s" % repo)
    if not any(repo == root or repo.is_relative_to(root) for root in _allowed_roots()):
        raise PermissionError("outside allowed roots: %s" % repo)
    if repo not in iter_repo_candidates():
        raise PermissionError("not a known repo: %s" % repo)
    return repo


def _editor_url_template():
    """URL template for the configured editor, falling back to VS Code's.

    An unknown DASHBOARD_EDITOR value falls back rather than raising: a typo in
    a shell profile should cost you the editor you wanted, not the whole panel.
    """
    name = (os.environ.get("DASHBOARD_EDITOR") or DEFAULT_EDITOR).strip().lower()
    return EDITOR_URLS.get(name, EDITOR_URLS[DEFAULT_EDITOR])


def _codemap_freshness(repo, built):
    """Compare the graph's built_at_commit against HEAD.

    Only the `stale` branch reports a distance — the number is meaningless once
    history has diverged, so `diverged` deliberately carries none.
    """
    if not (repo / ".git").exists() or not built:
        return {"state": "unknown"}
    built = str(built)
    if not _SHA_RE.match(built):
        return {"state": "unknown"}
    head = _git(repo, "rev-parse", "HEAD")
    if not head:
        return {"state": "unknown"}
    if not _git_ok(repo, "cat-file", "-e", built + "^{commit}"):
        return {"state": "unknown_commit", "built": built}
    if head.startswith(built) or built.startswith(head):
        return {"state": "fresh", "built": built, "head": head}
    if _git_ok(repo, "merge-base", "--is-ancestor", built, "HEAD"):
        out = {"state": "stale", "built": built, "head": head}
        try:
            out["behind"] = int(_git(repo, "rev-list", "--count", built + "..HEAD"))
        except ValueError:
            pass
        return out
    return {"state": "diverged", "built": built, "head": head}


def _stat_key(path):
    """(mtime_ns, size), or None when absent — so appearing/disappearing counts."""
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read_json_file(path):
    """Return (data, error). A missing file is (None, None) — absence is not an error."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, None
    except json.JSONDecodeError as e:
        return None, "invalid JSON in %s: %s" % (path.name, e)
    except (OSError, UnicodeDecodeError) as e:
        return None, "cannot read %s: %s" % (path.name, e)


# Types safe to use as dict keys (community/god-degree bucketing) without a
# collision surprise or a TypeError from an unhashable JSON array/object.
_HASHABLE_SCALARS = (str, int, float, bool, type(None))


def _is_hashable_scalar(value):
    return isinstance(value, _HASHABLE_SCALARS)


def _valid_graph(graph):
    if not isinstance(graph, dict):
        return False
    nodes, links = graph.get("nodes"), graph.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        return False
    for node in nodes:
        if not isinstance(node, dict) or "label" not in node:
            return False
        if not _is_hashable_scalar(node.get("id")) or not _is_hashable_scalar(node.get("community")):
            return False
    for link in links:
        if isinstance(link, dict):
            if not _is_hashable_scalar(link.get("source")) or not _is_hashable_scalar(link.get("target")):
                return False
    return True


def _nodes_by_id(graph):
    """id -> node. First occurrence wins on collision, so attribution is stable."""
    by_id = {}
    for node in graph["nodes"]:
        if isinstance(node, dict):
            by_id.setdefault(node.get("id"), node)
    return by_id


def _degrees(graph):
    """id -> number of link endpoints touching it."""
    degree = {}
    for link in graph["links"]:
        if not isinstance(link, dict):
            continue
        for end in ("source", "target"):
            node_id = link.get(end)
            if node_id is not None:
                degree[node_id] = degree.get(node_id, 0) + 1
    return degree


def _enrich_gods(gods, by_id, degree):
    """Give analysis-sourced gods the location they lack.

    `.graphify_analysis.json` records gods as {id, label, degree} only. The
    matching node in graph.json carries source_file/source_location, so join on
    id rather than dropping the location the UI needs.
    """
    out = []
    for entry in gods:
        node = by_id.get(entry.get("id"), {})
        out.append({
            "id": entry.get("id"),
            "label": entry.get("label") or node.get("label", ""),
            "degree": entry.get("degree", degree.get(entry.get("id"), 0)),
            "source_file": entry.get("source_file") or node.get("source_file", ""),
            "source_location": entry.get("source_location") or node.get("source_location", ""),
            "community_name": node.get("community_name", ""),
        })
    return out


def _gods_from_degree(graph, top=10):
    """Fallback ranking when .graphify_analysis.json is absent."""
    degree = _degrees(graph)
    by_id = _nodes_by_id(graph)
    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], str(kv[0])))[:top]
    return _enrich_gods(
        [{"id": node_id, "label": by_id.get(node_id, {}).get("label", str(node_id)),
          "degree": deg} for node_id, deg in ranked],
        by_id, degree)


def _communities_with_members(graph):
    """Group nodes by community, naming each from the nodes' own community_name.

    graphify writes the LLM-assigned name onto every node as `community_name`;
    `.graphify_analysis.json`'s "communities" is a dict of id -> member ids and
    carries no names at all. Reading the nodes is what makes a community read
    "Local Memory" instead of "Community 4".
    """
    buckets, names = {}, {}
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            continue
        cid = node.get("community")
        buckets.setdefault(cid, []).append({
            "id": node.get("id"),
            "label": node.get("label", ""),
            "source_file": node.get("source_file", ""),
            "source_location": node.get("source_location", ""),
        })
        name = node.get("community_name")
        if name and cid not in names and not str(name).startswith("Community "):
            names[cid] = name
    out = []
    for cid, members in buckets.items():
        out.append({
            "id": cid,
            "name": names.get(cid, "Community %s" % cid),
            "named": cid in names,
            "size": len(members),
            "members": members,
        })
    out.sort(key=lambda c: (-c["size"], str(c["id"])))
    return out


def _graph_payload(graph, communities, degree, max_nodes=600):
    """Trimmed node/link lists for drawing, plus what got dropped.

    The client draws this; sending 50k nodes would be a multi-megabyte payload
    for a picture nobody can read. Keep the highest-degree nodes and the links
    between survivors.
    """
    order = {c["id"]: i for i, c in enumerate(communities)}
    nodes = [n for n in graph["nodes"] if isinstance(n, dict)]
    nodes.sort(key=lambda n: -degree.get(n.get("id"), 0))
    kept = nodes[:max_nodes]
    kept_ids = {n.get("id") for n in kept}
    out_nodes = [{
        "id": n.get("id"),
        "label": n.get("label", ""),
        "group": order.get(n.get("community"), 0),
        "degree": degree.get(n.get("id"), 0),
        "source_file": n.get("source_file", ""),
        "source_location": n.get("source_location", ""),
    } for n in kept]
    out_links = [{"source": l.get("source"), "target": l.get("target"),
                  "relation": l.get("relation", "")}
                 for l in graph["links"]
                 if isinstance(l, dict)
                 and l.get("source") in kept_ids and l.get("target") in kept_ids]
    return {
        "nodes": out_nodes,
        "links": out_links,
        "truncated": max(0, len(nodes) - len(kept)),
    }


def _build_codemap(repo, graph_path, analysis_path):
    base = {"repo": str(repo), "name": repo.name}
    if not graph_path.is_file():
        return dict(base, missing=True, cmd=BUILD_CMD)

    graph, err = _read_json_file(graph_path)
    if err:
        return dict(base, invalid=True, error=err)
    if not _valid_graph(graph):
        return dict(base, invalid=True,
                    error="unexpected shape in graph.json: need dict with list "
                          "'nodes' (each a dict with a 'label', and scalar 'id'/"
                          "'community') and list 'links' (each dict's 'source'/"
                          "'target' scalar)")

    analysis, err = _read_json_file(analysis_path)
    if err:
        return dict(base, invalid=True, error=err)
    if analysis is not None and not isinstance(analysis, dict):
        return dict(base, invalid=True,
                    error="unexpected shape in .graphify_analysis.json: need a dict")

    def listy(key):
        value = (analysis or {}).get(key)
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

    degree = _degrees(graph)
    by_id = _nodes_by_id(graph)

    gods = listy("gods")
    if analysis is not None and gods:
        gods, gods_source = _enrich_gods(gods, by_id, degree), "analysis"
    else:
        gods, gods_source = _gods_from_degree(graph), "degree"

    communities = _communities_with_members(graph)
    unnamed = [c for c in communities if not c["named"]]

    warning = None
    if analysis is None:
        warning = ("No .graphify_analysis.json — god nodes are ranked by degree and "
                   "surprising connections are unavailable. Run `%s` to regenerate."
                   % BUILD_CMD)
    elif unnamed:
        warning = ("%d of %d clusters have no name. Naming needs an LLM pass: run "
                   "`graphify label . --backend ollama --model qwen2.5:7b-instruct "
                   "--max-concurrency 1`." % (len(unnamed), len(communities)))

    return dict(
        base,
        counts={"nodes": len(graph["nodes"]), "links": len(graph["links"]),
                "communities": len(communities)},
        freshness=_codemap_freshness(repo, graph.get("built_at_commit")),
        gods=gods,
        gods_source=gods_source,
        communities=communities,
        surprises=listy("surprises"),
        graph=_graph_payload(graph, communities, degree),
        editor_url=_editor_url_template(),
        analysis=analysis is not None,
        analysis_warning=warning,
    )


def read_codemap(path_value):
    """Read a repo's graphify graph. Never builds one — see BUILD_CMD."""
    repo = _resolve_repo(path_value)
    graph_path = repo / GRAPH_REL
    analysis_path = repo / ANALYSIS_REL
    head = _git(repo, "rev-parse", "HEAD") if (repo / ".git").exists() else ""
    key = (_stat_key(graph_path), _stat_key(analysis_path), head)
    cached = _codemap_cache.get(str(repo))
    if cached and cached[0] == key:
        return cached[1]
    data = _build_codemap(repo, graph_path, analysis_path)
    _codemap_cache[str(repo)] = (key, data)
    return data


def read_codemaps():
    """The repo picker. Existence check only — no JSON parsing, no git calls.

    With N repos those would be N parses and N subprocesses on every page load;
    counts and freshness arrive from /api/codemap once a repo is selected.
    """
    repos = [{"name": repo.name, "path": str(repo),
              "has_graph": (repo / GRAPH_REL).is_file()}
             for repo in iter_repo_candidates()]
    repos.sort(key=lambda r: (not r["has_graph"], r["name"].lower()))
    return {"repos": repos, "count": len(repos)}


def read_projects():
    now = time.time()
    if _projects_cache["data"] is not None and now - _projects_cache["ts"] < PROJECTS_TTL:
        return _projects_cache["data"]
    dirs = iter_repo_candidates(include_extra=False)
    if not dirs:
        return {"projects": [], "root": str(PROJECTS_ROOT),
                "error": "no repos found in ~/Projeler or EXTRA_REPOS"}
    activity = _load_activity()
    with ThreadPoolExecutor(max_workers=8) as ex:
        projects = list(ex.map(lambda d: _project_info(d, activity), dirs))
    projects.sort(key=_recency_key, reverse=True)
    out = {"projects": projects, "root": str(PROJECTS_ROOT), "count": len(projects)}
    _projects_cache.update(ts=now, data=out)
    return out


def read_project_detail(path_value):
    if not path_value:
        raise ValueError("path is required")
    repo = Path(path_value).expanduser().resolve()
    if repo not in iter_repo_candidates(include_extra=False):
        raise PermissionError("outside Projeler root")
    if not repo.exists():
        raise FileNotFoundError(str(repo))
    activity = _load_activity()
    info = _project_info(repo, activity)
    if info.get("is_git"):
        log = _git(repo, "log", "-15", "--pretty=%h" + _US + "%an" + _US + "%ar" + _US + "%s")
        commits = []
        for line in log.splitlines():
            p = line.split(_US)
            if len(p) >= 4:
                commits.append({"hash": p[0], "author": p[1], "rel": p[2], "subject": p[3]})
        info["commits"] = commits
    info["activity"] = activity.get(repo.name, [])[-40:][::-1]  # newest first
    return info


# --- Architectures ----------------------------------------------------------
# Storage, validation and the phase catalog live in architectures.py; this file
# only routes. The repo guard is injected rather than duplicated, so the picker
# (/api/projects, /api/codemaps) and the writer accept exactly the same repos.
ARCH_PATCH_FIELDS = ("title", "summary", "status", "current_phase", "phase")
TASK_PATCH_FIELDS = ("title", "status", "phase", "priority", "notes")


def _patch_fields(body, allowed):
    """Forward only the keys the client actually sent — architectures.py reads a
    missing key as 'leave alone' and a present one as 'set to this'."""
    return {k: body[k] for k in allowed if k in body}


def read_architecture_with_run(arch_id):
    """The architecture plus its run state.

    Composed here rather than inside architectures.py: the store knows nothing
    about runs, and keeping it that way is what lets runner.py depend on it
    instead of the other way round.
    """
    doc = architectures.read_architecture(arch_id)
    try:
        doc["run"] = runner.latest(arch_id)
        doc["runs"] = runner.history(arch_id)
    except Exception:                      # run bookkeeping must not hide the work
        doc["run"], doc["runs"] = None, []
    return doc


def delete_architecture_with_runs(arch_id):
    """Delete the architecture and the runs it accumulated.

    Composed here for the same reason the read is: architectures.py does not
    know runs exist. The confirmation dialog has always said "the run history
    goes with it" and it did not — deleted architectures left their run
    directories behind, reachable by no page and counted by nothing.

    The runs go first, but their failure is not the delete's failure: the
    architecture is the record that matters, and a leftover directory is untidy
    rather than a reason to refuse.
    """
    try:
        runner.forget_runs(arch_id)
    except Exception:
        pass
    return architectures.delete_architecture(arch_id)


def list_architectures_with_runner(repo, status, q):
    data = architectures.list_architectures(repo, status, q)
    data["runner_available"] = runner.runner_available()
    for row in data["results"]:
        try:
            row["run_status"] = (runner.latest(row["id"]) or {}).get("status")
        except Exception:
            row["run_status"] = None
    return data


# --- HTTP handler -----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "SharedBrain/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))

    # -- response helpers --
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The page is built from assets.py at request time; a cached copy would
        # keep showing an old panel after the module changes.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, exc, status=500):
        traceback.print_exc()
        self._send_json({"error": str(exc), "type": type(exc).__name__}, status=status)

    def _read_body(self, max_bytes=1_048_576):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        if length > max_bytes:
            raise ValueError(f"request body exceeds the {max_bytes} byte limit")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # -- routing --
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)

        def arg(name):
            v = q.get(name, [None])[0]
            return v if v else None

        try:
            if path == "/" or path == "/index.html":
                return self._send_html(PAGE_HTML)
            # One architecture, its own URL — keepable, reloadable, linkable.
            if path == "/architecture" or path == "/architecture.html":
                return self._send_html(ARCH_PAGE_HTML)
            if path == "/api/health":
                return self._send_json(read_health())
            if path == "/api/memories":
                return self._send_json(read_memories(arg("scope"), arg("agent")))
            if path == "/api/search":
                query = arg("q") or ""
                if not query:
                    return self._send_json({"results": [], "query": ""})
                include_superseded = (arg("all") == "1" or arg("include_superseded") == "1")
                return self._send_json(search_memories(query, arg("scope"), arg("agent"), include_superseded=include_superseded))
            if path == "/api/skills":
                return self._send_json({"results": read_skills(arg("q"))})
            if path == "/api/agents":
                return self._send_json({"results": read_agents()})
            if path == "/api/mcp":
                return self._send_json(read_mcp())
            if path == "/api/rules":
                return self._send_json(read_rules())
            if path == "/api/graph":
                import graph_store
                node_id = arg("id") or ""
                hops = int(arg("hops") or 2)
                if not node_id:
                    return self._send_json({"nodes": [], "edges": []})
                return self._send_json(graph_store.export_subgraph(node_id, max_hops=hops))
            if path == "/api/instructions":
                return self._send_json(read_instructions())
            if path == "/api/hooks":
                return self._send_json(read_hooks())
            if path == "/api/usage":
                return self._send_json(read_usage())
            if path == "/api/projects":
                return self._send_json(read_projects())
            if path == "/api/project":
                return self._send_json(read_project_detail(arg("path")))
            if path == "/api/codemaps":
                return self._send_json(read_codemaps())
            if path == "/api/codemap":
                data = read_codemap(arg("path"))
                return self._send_json(data, status=422 if data.get("invalid") else 200)
            if path == "/api/content":
                return self._send_json(read_content(arg("path")))
            if path == "/api/architectures":
                return self._send_json(list_architectures_with_runner(
                    arg("repo"), arg("status"), arg("q")))
            if path == "/api/architecture":
                return self._send_json(read_architecture_with_run(arg("id")))
            if path == "/api/architecture/run/log":
                return self._send_json(runner.get_log(
                    arg("id"), arg("run"), arg("offset") or 0,
                    raw=(arg("raw") == "1")))
            if path == "/api/architecture/templates":
                return self._send_json(architectures.templates())
            if path == "/api/architecture/template":
                return self._send_json(architectures.get_template(arg("id")))
            if path == "/api/harness/context":
                return self._send_json(read_harness_context(arg("task") or "", arg("scope") or SHARED_SCOPE))
            if path == "/api/harness/trajectories":
                return self._send_json(list_harness_trajectories())
            if path == "/api/harness/trajectory":
                rid = arg("id") or arg("run_id")
                if not rid:
                    return self._send_json({"error": "id is required"}, status=400)
                return self._send_json(read_harness_trajectory_detail(rid))
            if path == "/api/graph/hubs":
                import graph_store
                top_k = int(arg("top_k") or 15)
                scope = arg("scope")
                kind = arg("kind")
                return self._send_json({"hubs": graph_store.get_centrality_nodes(top_k=top_k, kind=kind, scope=scope)})
            return self._send_json({"error": "not found", "path": path}, status=404)
        except PermissionError as e:
            return self._send_json({"error": str(e), "type": "PermissionError"}, status=403)
        except FileNotFoundError as e:
            return self._send_json({"error": str(e), "type": "FileNotFoundError"}, status=404)
        except ValueError as e:
            return self._send_json({"error": str(e), "type": "ValueError"}, status=400)
        except Exception as e:
            return self._err(e)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/memory":
                body = self._read_body()
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send_json({"error": "text is required"}, status=400)
                return self._send_json(
                    add_memory(text, body.get("scope"), body.get("agent"))
                )
            if parsed.path == "/api/memory/supersede":
                body = self._read_body()
                old_id = (body.get("id") or body.get("old_id") or "").strip()
                if not old_id:
                    return self._send_json({"error": "id is required"}, status=400)
                return self._send_json(
                    supersede_memory_item(old_id, body.get("new_text") or body.get("text"),
                                          scope=body.get("scope"), agent=body.get("agent"))
                )
            if parsed.path == "/api/assistant/chat":
                body = self._read_body()
                q = (body.get("question") or body.get("message") or "").strip()
                if not q:
                    return self._send_json({"error": "question/message is required"}, status=400)
                import assistant
                res = assistant.ask_brain(
                    question=q,
                    scope=body.get("scope") or "personal",
                    history=body.get("history") or [],
                    top_k=int(body.get("top_k") or 5),
                )
                if body.get("plain") or "text/plain" in self.headers.get("Accept", ""):
                    return self._send_text(res.get("answer", ""))
                return self._send_json(res)
            if parsed.path == "/api/assistant/remember":
                body = self._read_body()
                text = (body.get("text") or body.get("memory") or "").strip()
                if not text:
                    return self._send_json({"error": "text is required"}, status=400)
                scope = body.get("scope") or "personal"
                try:
                    import config
                    mem = config.get_memory()
                    user_id_owner = os.environ.get("MEM0_OWNER", "default")
                    mem.add(text, user_id=user_id_owner, run_id=scope, metadata={"agent_id": "webhook", "source": "assistant_api"})
                    if body.get("plain") or "text/plain" in self.headers.get("Accept", ""):
                        return self._send_text(f"Recorded into {scope}: {text}")
                    return self._send_json({"success": True, "saved": text, "scope": scope})
                except Exception as ex:
                    return self._send_json({"error": str(ex)}, status=500)
            if parsed.path == "/api/content":
                body = self._read_body()
                return self._send_json(write_content(body.get("path"), body.get("content")))
            if parsed.path == "/api/create":
                body = self._read_body()
                return self._send_json(create_item(
                    body.get("kind"), body.get("name"),
                    body.get("folder"), body.get("content"),
                ))
            if parsed.path == "/api/mcp":
                body = self._read_body()
                return self._send_json(add_mcp_server(
                    body.get("name"), body.get("command"),
                    body.get("args"), body.get("env"),
                ))
            if parsed.path == "/api/architecture":
                body = self._read_body()
                return self._send_json(architectures.create_architecture(
                    body.get("title"), body.get("repo"), body.get("summary") or "",
                    resolve_repo=_resolve_repo,
                    template_id=body.get("template_id") or architectures.DEFAULT_TEMPLATE_ID,
                ), status=201)
            if parsed.path == "/api/architecture/template":
                return self._send_json(
                    architectures.create_template(self._read_body()), status=201)
            if parsed.path == "/api/architecture/run":
                body = self._read_body()
                return self._send_json(
                    runner.start_run(body.get("id"), api_base=f"http://{HOST}:{PORT}"),
                    status=202)
            if parsed.path == "/api/architecture/run/kill":
                return self._send_json(runner.kill_run(self._read_body().get("id")))
            if parsed.path == "/api/architecture/pause-auto":
                return self._send_json(architectures.set_auto_advance(
                    self._read_body().get("id"), False))
            if parsed.path == "/api/architecture/resume-auto":
                return self._send_json(architectures.set_auto_advance(
                    self._read_body().get("id"), True))
            if parsed.path == "/api/architecture/chain":
                body = self._read_body()
                return self._send_json(architectures.set_auto_chain(
                    body.get("id"), bool(body.get("on"))))
            if parsed.path == "/api/architecture/task":
                body = self._read_body()
                return self._send_json(architectures.add_task(
                    body.get("arch"), body.get("phase"), body.get("title"),
                    priority=body.get("priority", architectures.TASK_PRIORITY_DEFAULT),
                ), status=201)
            if parsed.path == "/api/harness/run":
                body = self._read_body(max_bytes=100_000)
                task = (body.get("task") or "").strip()
                if not task:
                    return self._send_json({"error": "task is required"}, status=400)
                if len(task) > 20_000:
                    return self._send_json({"error": "task exceeds the 20000 character limit"}, status=413)
                scope = body.get("scope") or SHARED_SCOPE
                if len(scope) > 256:
                    return self._send_json({"error": "scope exceeds the 256 character limit"}, status=413)
                verify = body.get("verify_command") or body.get("verify")
                res = run_harness_execution(task, scope=scope, verify_command=verify, cwd=body.get("cwd"))
                # Preparation status is returned in the payload; no task is executed here.
                return self._send_json(res, status=200)
            return self._send_json({"error": "not found"}, status=404)
        except PermissionError as e:
            return self._send_json({"error": str(e), "type": "PermissionError"}, status=403)
        except FileNotFoundError as e:
            return self._send_json({"error": str(e), "type": "FileNotFoundError"}, status=404)
        except FileExistsError as e:
            return self._send_json({"error": str(e), "type": "FileExistsError"}, status=409)
        except RuntimeError as e:      # nothing to launch — the box, not the request
            return self._send_json({"error": str(e), "type": "RuntimeError"}, status=503)
        except ValueError as e:
            return self._send_json({"error": str(e), "type": "ValueError"}, status=400)
        except Exception as e:
            return self._err(e)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/memory":
                mid = q.get("id", [None])[0]
                if not mid:
                    return self._send_json({"error": "id is required"}, status=400)
                return self._send_json(delete_memory(mid))
            if parsed.path == "/api/content":
                return self._send_json(delete_path(q.get("path", [None])[0]))
            if parsed.path == "/api/mcp":
                return self._send_json(remove_mcp_server(
                    q.get("name", [None])[0], q.get("path", [None])[0]
                ))
            if parsed.path == "/api/architecture":
                return self._send_json(delete_architecture_with_runs(
                    q.get("id", [None])[0]))
            if parsed.path == "/api/architecture/task":
                return self._send_json(architectures.delete_task(
                    q.get("arch", [None])[0], q.get("id", [None])[0]))
            if parsed.path == "/api/architecture/template":
                return self._send_json(architectures.delete_template(
                    q.get("id", [None])[0]))
            return self._send_json({"error": "not found"}, status=404)
        except PermissionError as e:
            return self._send_json({"error": str(e), "type": "PermissionError"}, status=403)
        except FileNotFoundError as e:
            return self._send_json({"error": str(e), "type": "FileNotFoundError"}, status=404)
        except ValueError as e:
            return self._send_json({"error": str(e), "type": "ValueError"}, status=400)
        except Exception as e:
            return self._err(e)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/memory":
                body = self._read_body()
                return self._send_json(update_memory(body.get("id"), body.get("text"), status=body.get("status")))
            if parsed.path == "/api/architecture":
                body = self._read_body()
                return self._send_json(architectures.update_architecture(
                    body.get("id"), **_patch_fields(body, ARCH_PATCH_FIELDS)))
            if parsed.path == "/api/architecture/task":
                body = self._read_body()
                return self._send_json(architectures.update_task(
                    body.get("arch"), body.get("id"),
                    **_patch_fields(body, TASK_PATCH_FIELDS)))
            return self._send_json({"error": "not found"}, status=404)
        except PermissionError as e:
            return self._send_json({"error": str(e), "type": "PermissionError"}, status=403)
        except FileNotFoundError as e:
            return self._send_json({"error": str(e), "type": "FileNotFoundError"}, status=404)
        except ValueError as e:
            return self._send_json({"error": str(e), "type": "ValueError"}, status=400)
        except Exception as e:
            return self._err(e)

    def do_PUT(self):
        parsed = urlparse(self.path)
        try:
            # One atomic multi-phase move. The phase modal shifts more than one
            # phase at a time, and N sequential PATCHes would race.
            if parsed.path == "/api/architecture/workflow":
                body = self._read_body()
                return self._send_json(architectures.bulk_update_workflow(
                    body.get("id"), body.get("phases")))
            return self._send_json({"error": "not found", "path": parsed.path}, status=404)
        except PermissionError as e:
            return self._send_json({"error": str(e), "type": "PermissionError"}, status=403)
        except FileNotFoundError as e:
            return self._send_json({"error": str(e), "type": "FileNotFoundError"}, status=404)
        except ValueError as e:
            return self._send_json({"error": str(e), "type": "ValueError"}, status=400)
        except Exception as e:
            return self._err(e)


def main():
    # A run's supervisor thread dies with the process that owned it, so anything
    # still marked running belongs to a previous life of this server.
    try:
        stale = runner.reconcile_on_startup()
        if stale:
            # flush: stdout is block-buffered when the server is redirected to a
            # log file, and a startup line nobody sees until the first request
            # is not a startup line.
            print(f"marked {len(stale)} interrupted run(s) from a previous session",
                  flush=True)
    except Exception as e:
        print(f"could not reconcile previous runs: {e}", file=sys.stderr)

    # Picks a chain back up once a quota clears. Daemon: it must never be the
    # reason the server will not shut down.
    try:
        runner.start_resume_watcher(api_base=f"http://{HOST}:{PORT}")
    except Exception as e:
        print(f"could not start the resume watcher: {e}", file=sys.stderr)

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Shared Brain Control Panel → http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
