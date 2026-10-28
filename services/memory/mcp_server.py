#!/usr/bin/env python
"""
MCP server exposing the fully-local mem0 memory to any MCP client
(Claude Code, Claude Desktop, ...). No API key — backed by Ollama + Qdrant.

Scoping model (so memories stay isolated + attributable):
  - user_id  = $MEM0_OWNER or "default" (configure explicitly for local data)
  - scope    -> run_id        ISOLATION: a project/context name. Memories in
                              scope "sample-panel" never mix with "otherapp".
                              Omit scope on search/list to see everything.
  - agent    -> agent_id      ATTRIBUTION: who wrote it (an agent name, or the
                              surface like "claude-desktop" / "claude-code").
  - created_at                WHEN: stored automatically, shown on list/search.

Tools:
  - add_memory(text, scope?, agent?)          store facts
  - search_memory(query, scope?, agent?)      semantic recall (optionally scoped)
  - list_memories(scope?, agent?)             chronological view: [when] (scope/agent) fact

Requirements at run time:
  - Ollama running (Ollama.app) with models qwen2.5:7b-instruct + nomic-embed-text
    (the LLM is config.LLM_MODEL; $MEM0_LLM_MODEL overrides it)
  - Qdrant server running (vendored bin/qdrant, started at login by the launchd
    agent com.mem0.qdrant, port 6333)
"""

import datetime
import math
import os
import re
import sys

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import get_memory  # noqa: E402
from mcp_transport import ConfigError, build_server, transport_from  # noqa: E402
from memory_store import read_inventory  # noqa: E402

# stdio unless told otherwise, so the existing local Claude Code registration
# keeps working untouched. See mcp_transport.py for the HTTP mode. A misconfigured
# HTTP mode (no token, non-loopback bind) refuses to start here rather than
# coming up unprotected.
try:
    mcp = build_server(FastMCP, "mem0-local")
except ConfigError as _ex:
    sys.exit("mcp_server: %s" % _ex)

OWNER = os.environ.get("MEM0_OWNER", "default")
# Surface writing memories by default (Claude Code overrides via the directive).
DEFAULT_AGENT = os.environ.get("MEM0_DEFAULT_AGENT", "claude")

# The GLOBAL / "shared brain" tier: cross-project, cross-tool durable knowledge
# (preferences, conventions, reusable workflows). Every agent reads this by
# default. Project scopes hold repo-specific facts and stay isolated from each
# other, but a project recall also sees the shared tier (layered recall).
GLOBAL_SCOPE = os.environ.get("MEM0_GLOBAL_SCOPE", "personal")

# Upper bound for list_memories. High enough to mean "everything" for a personal
# store; a real limit rather than unbounded so one call can't dump an arbitrarily
# large result into the model's context.
LIST_MAX = int(os.environ.get("MEM0_LIST_MAX", "1000"))

_memory = None


def _mem():
    """Lazily build Memory so the server starts even if Ollama/Qdrant aren't ready;
    the first tool call is what actually connects."""
    global _memory
    if _memory is None:
        _memory = get_memory()
    return _memory


def _scoped_filters(scope, agent):
    f = {"user_id": OWNER}
    if scope:
        f["run_id"] = scope
    if agent:
        f["agent_id"] = agent
    return f


def recency_factor(created_at_str: str, now: datetime.datetime = None, half_life_days: float = 90.0) -> float:
    """Calculate freshness decay multiplier.
    Fresh (0 days) = 1.0, 90 days = 0.8, 180 days = 0.7, Infinity = 0.6 (baseline floor).
    Ensures durable facts are never completely silenced while giving fresh decisions priority.
    """
    if not created_at_str:
        return 1.0
    try:
        ts_clean = created_at_str.replace("Z", "+00:00")
        if "T" in ts_clean:
            dt = datetime.datetime.fromisoformat(ts_clean)
        else:
            dt = datetime.datetime.fromisoformat(ts_clean.replace(" ", "T"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
    except Exception:
        return 1.0

    now = now or datetime.datetime.now()
    delta_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    decay = math.pow(0.5, delta_days / half_life_days)
    return 0.6 + 0.4 * decay


def _attr_line(item, show_score=False):
    """Render one memory with when / scope / who attribution and superseded tag."""
    ts = (item.get("created_at") or "")[:16].replace("T", " ")
    scope = item.get("run_id") or "-"
    agent = item.get("agent_id") or "-"
    status = item.get("status") or (item.get("metadata") or {}).get("status") or "active"
    status_tag = " [SUPERSEDED]" if status == "superseded" else ""
    prefix = f"[{ts}] ({scope}/{agent}){status_tag}"
    if show_score:
        score = item.get("score")
        score_str = f" {score:.3f}" if isinstance(score, (int, float)) else ""
        prefix += score_str
    return f"{prefix} {item.get('memory', '')}"


@mcp.tool()
def add_memory(
    text: str,
    scope: str = "personal",
    agent: str = DEFAULT_AGENT,
    verbatim: bool = False,
    supersedes_id: str = "",
) -> str:
    """MANDATORY MEMORY STORAGE: Call this tool whenever an architectural decision, library choice,
    resolved gotcha, or user preference is established. mem0 indexes and extracts salient facts automatically.


    Args:
        text: The information to remember (free text).
        scope: Isolation bucket — use the current project/context name (e.g. the
            repo folder name) so its memories don't mix with other projects.
            Use "personal" for cross-project personal facts.
        agent: Who is writing this — an agent name (e.g. "code-reviewer") or the
            surface ("claude-code" / "claude-desktop"). For attribution.
        verbatim: Skip LLM fact extraction and store `text` exactly as written.
            Use when the statement is already a single, precise, self-contained
            fact — extraction is a small local model and can distort or invent
            details when paraphrasing. Costs no LLM call.
        supersedes_id: Optional ID of an older memory that this new memory replaces
            or invalidates (e.g., when a stack or architecture choice changes).
    """
    metadata = {"status": "active"}
    if supersedes_id:
        metadata["supersedes"] = supersedes_id

    res = _mem().add(
        text,
        user_id=OWNER,
        run_id=scope,
        agent_id=agent,
        metadata=metadata,
        infer=not verbatim,
    )
    results = res.get("results", [])
    if not results:
        return "No new facts were extracted (nothing durable to store)."

    supersede_msg = ""
    if supersedes_id:
        try:
            new_id = str(results[0].get("id", ""))
            _mem().update(
                supersedes_id,
                metadata={"status": "superseded", "superseded_by": new_id},
            )
            supersede_msg = f"\n[SUPERSEDED] Memory {supersedes_id} marked as superseded by {new_id}."
        except Exception as ex:
            supersede_msg = f"\n[WARN] Failed to mark {supersedes_id} as superseded: {ex}"

    lines = [f"[{r.get('event', 'ADD')}] {r.get('memory', '')}" for r in results]

    try:
        import graph_store
        for r in results:
            mid = str(r.get("id") or "")
            mtext = str(r.get("memory") or text)
            graph_store.sync_memory_links(mid, mtext, metadata=metadata, scope=scope)
    except Exception:
        pass

    return f"Stored in scope '{scope}' as '{agent}':\n" + "\n".join(lines) + supersede_msg


@mcp.tool()
def supersede_memory(
    old_id: str,
    new_text: str = "",
    scope: str = "personal",
    agent: str = DEFAULT_AGENT,
) -> str:
    """Explicitly invalidate or replace an outdated memory.
    Marks the old memory as superseded so it will not appear in future searches.
    If new_text is provided, adds the replacement memory and links them.

    Args:
        old_id: The ID of the outdated memory to supersede.
        new_text: Optional replacement text/fact.
        scope: Project scope for the replacement memory.
        agent: Agent attribution for the replacement.
    """
    if not old_id:
        return "Error: old_id is required."

    if new_text:
        return add_memory(
            text=new_text,
            scope=scope,
            agent=agent,
            verbatim=True,
            supersedes_id=old_id,
        )

    try:
        _mem().update(old_id, metadata={"status": "superseded"})
        return f"Memory {old_id} successfully marked as [SUPERSEDED]."
    except Exception as ex:
        return f"Failed to supersede memory {old_id}: {ex}"


def _merge_hits(batches, top_k, include_superseded=False, half_life_days=90.0, query=""):
    """Merge several search result lists:
    1. Exact Token / Keyword Boost (Sparse-Dense Hybrid)
    2. Recency decay & Authority Multipliers
    3. Multi-Hop Knowledge Graph Expansion (A-MEM / GraphRAG)
    4. Deduplication & Final Top-K ranking
    """
    best = {}

    # Extract query keywords for exact token / lexical boost (sparse-dense hybrid)
    query_keywords = set()
    if query:
        raw_tokens = re.findall(r"[A-Za-z0-9_\-\.]{3,}", query.lower())
        stop_words = {"the", "and", "for", "with", "this", "that", "how", "what", "which", "where", "from", "are"}
        query_keywords = {t for t in raw_tokens if t not in stop_words}

    for hits in batches:
        for h in hits:
            status = h.get("status") or (h.get("metadata") or {}).get("status") or "active"
            if not include_superseded and status == "superseded":
                continue

            raw_score = h.get("score") or 0.0
            created_at = h.get("created_at") or ""
            r_factor = recency_factor(created_at, half_life_days=half_life_days)

            # Keyword precision boost for exact token matches (e.g. port numbers, config files)
            kw_boost = 0.0
            if query_keywords:
                mem_text = (h.get("memory") or "").lower()
                matches = sum(1 for kw in query_keywords if kw in mem_text)
                if matches:
                    kw_boost = 0.12 * (matches / len(query_keywords))

            # Multi-factor authority & priority boost
            meta = h.get("metadata") or {}
            authority_multiplier = 1.0
            if meta.get("pinned") or meta.get("authoritative") or h.get("pinned"):
                authority_multiplier = 1.25
            elif meta.get("synthesized") or meta.get("reflexion") or "Consolidated Architectural" in (h.get("memory") or ""):
                authority_multiplier = 1.15

            effective_score = (raw_score + kw_boost) * r_factor * authority_multiplier

            item = dict(h)
            item["score"] = effective_score
            item["raw_score"] = raw_score
            item["status"] = status

            key = item.get("id") or item.get("memory")
            prev = best.get(key)
            if prev is None or item["score"] > prev["score"]:
                best[key] = item

    # Multi-hop Knowledge Graph Expansion (A-MEM / GraphRAG)
    seeds = []
    for h in sorted(best.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[:3]:
        mid = str(h.get("id") or "")
        if mid:
            seeds.append((mid, h["score"]))

    if seeds:
        try:
            import graph_store
            seed_ids = [s[0] for s in seeds]
            seed_scores = dict(seeds)
            discovered = graph_store.multi_hop_query(start_ids=seed_ids, max_hops=1)
            for disc in discovered:
                nid = disc.get("node_id")
                if disc.get("kind") == "memory" and nid and nid not in best:
                    node = graph_store.get_node(nid)
                    if node:
                        props = node.get("properties") or {}
                        parent_score = seed_scores.get(disc.get("reached_via"), 0.5)
                        graph_score = parent_score * 0.85
                        status = props.get("status") or "active"
                        if not include_superseded and status == "superseded":
                            continue

                        best[nid] = {
                            "id": nid,
                            "memory": props.get("text") or node.get("label") or "",
                            "run_id": props.get("scope") or "personal",
                            "agent_id": props.get("agent_id") or "graph",
                            "status": status,
                            "created_at": node.get("updated_at") or "",
                            "score": graph_score,
                            "raw_score": graph_score,
                            "metadata": {
                                "graph_expanded": True,
                                "relation": disc.get("relation"),
                                "via": disc.get("reached_via"),
                            },
                        }
        except Exception:
            pass

    merged = list(best.values())
    merged.sort(key=lambda h: h.get("score") or 0.0, reverse=True)
    return merged[:top_k]


@mcp.tool()
def search_memory(
    query: str,
    scope: str = "",
    agent: str = "",
    top_k: int = 5,
    include_shared: bool = True,
    include_superseded: bool = False,
) -> str:
    """MANDATORY RECALL TOOL (STEP 0): Call this tool FIRST before answering complex queries,
    planning architecture, or modifying code in this project. Retrieves established architectural
    decisions, design patterns, constraints, past bug fixes, and user preferences from mem0.

    Layered recall (the "shared brain"): when you pass a project scope, this also
    searches the shared/global tier ('personal') and merges the results by
    relevance with recency decay — so an agent working in a project always sees
    both the project's own facts AND the cross-project shared knowledge, but never
    another project's.


    Args:
        query: What to look for, in natural language.
        scope: Restrict to one project/context. Leave empty to search across
            EVERYTHING (all scopes). Pass a project name for layered recall.
        agent: Restrict to memories written by one agent/surface. Leave empty for any.
        top_k: Max number of memories to return.
        include_shared: When a project scope is given, also fold in the shared
            'personal' tier (default True). Set False for strict project-only recall.
        include_superseded: Include historical superseded memories (default False).
    """
    # Which scopes to query. Empty scope = everything (single unfiltered search).
    if not scope:
        scopes = [None]
    elif include_shared and scope != GLOBAL_SCOPE:
        scopes = [scope, GLOBAL_SCOPE]
    else:
        scopes = [scope]

    batches = [
        _mem()
        .search(query, filters=_scoped_filters(s, agent or None), top_k=top_k * 2)
        .get("results", [])
        for s in scopes
    ]
    hits = _merge_hits(batches, top_k, include_superseded=include_superseded, query=query)
    if not hits:
        return "No relevant memories found."
    return "\n".join(_attr_line(h, show_score=True) for h in hits)


@mcp.tool()
def list_memories(scope: str = "", agent: str = "", include_superseded: bool = False) -> str:
    """List stored memories with attribution — who wrote each and when.
    Output lines look like: [YYYY-MM-DD HH:MM] (scope/agent) fact

    Args:
        scope: Restrict to one project/context. Leave empty to list every scope.
        agent: Restrict to one agent/surface. Leave empty for any.
        include_superseded: Include superseded historical memories (default False).
    """
    items = read_inventory(owner=OWNER, scope=scope or None, agent=agent or None,
                           include_superseded=True)
    if not items:
        where = f" in scope '{scope}'" if scope else ""
        return f"No memories stored{where}."

    if not include_superseded:
        items = [i for i in items if (i.get("status") or (i.get("metadata") or {}).get("status")) != "superseded"]

    if not items:
        where = f" in scope '{scope}'" if scope else ""
        return f"No active memories stored{where}."

    items.sort(key=lambda i: i.get("created_at") or "")
    shown = items[:LIST_MAX]
    lines = [_attr_line(item) for item in shown]
    if len(items) > LIST_MAX:
        lines.append(f"[TRUNCATED] Showing {LIST_MAX} of {len(items)} memories; narrow scope or agent to see fewer.")
    return "\n".join(lines)


@mcp.tool()
def get_active_rules(file_path: str = "", scope: str = "personal") -> str:
    """Get active scoped coding rules and conventions that apply to a specific file or project.
    Call this when opening, creating, or editing a file to inspect its binding conventions.

    Args:
        file_path: Relative path to the file being inspected/modified (e.g. 'src/services/user.ts').
        scope: Project name (or 'personal').
    """
    import rules as rules_store
    active = rules_store.match_rules_for_file(scope, file_path)
    if not active:
        return f"No active rules found for '{file_path}' in scope '{scope}'."
    return rules_store.render(active, scope)


@mcp.tool()
def get_memory_graph(memory_id: str = "", depth: int = 1) -> str:
    """Retrieve connected knowledge graph nodes, entities, and relations for a given memory or entity.
    If memory_id is empty or 'hubs', returns the top architectural hubs by degree centrality.
    Enables multi-hop associative navigation across connected concepts and architectural decisions.

    Args:
        memory_id: The ID of the memory, or 'entity:<name>', or 'scope:<name>', or 'hubs' for top hubs.
        depth: Graph traversal distance (default 1, max 3).
    """
    import graph_store
    if not memory_id or memory_id.lower() in ("hubs", "centrality", "top"):
        hubs = graph_store.get_centrality_nodes(top_k=10)
        if not hubs:
            return "Knowledge graph is currently empty."
        lines = ["Top Architectural Hubs (Degree Centrality):"]
        for h in hubs:
            lines.append(f"  - [{h['total_degree']} connections | in:{h['in_degree']} out:{h['out_degree']}] {h['id']} ({h['kind']}): {h['label']}")
        return "\n".join(lines)

    depth = max(1, min(int(depth), 3))
    subgraph = graph_store.export_subgraph(memory_id, max_hops=depth)
    nodes = subgraph.get("nodes", [])
    edges = subgraph.get("edges", [])
    if not nodes:
        return f"No graph node found for '{memory_id}'."

    lines = [f"Knowledge Subgraph for '{memory_id}' (depth={depth}):"]
    lines.append(f"  Nodes ({len(nodes)}):")
    for n in nodes:
        kind = n.get("kind", "unknown")
        label = n.get("label", n.get("id"))
        lines.append(f"    - [{kind}] {n.get('id')}: {label}")

    lines.append(f"  Relationships ({len(edges)}):")
    for e in edges:
        lines.append(f"    - {e.get('source')} --({e.get('relation')})--> {e.get('target')}")

    return "\n".join(lines)


@mcp.tool()
def link_memories(source_id: str, target_id: str, relation: str = "relates_to") -> str:
    """Explicitly link two memories, entities, or scopes in the knowledge graph.

    Args:
        source_id: ID of the source node.
        target_id: ID of the target node.
        relation: Semantic relationship ('relates_to', 'depends_on', 'part_of', 'uses', etc.).
    """
    import graph_store
    try:
        edge = graph_store.add_edge(source_id, target_id, relation=relation)
        return f"Linked {edge['source_id']} --({edge['relation']})--> {edge['target_id']}"
    except Exception as ex:
        return f"Failed to link nodes: {ex}"


@mcp.tool()
def list_available_skills(task_context: str = "") -> str:
    """List all available skills (local workspace + global ~/.mem0/skills).
    If task_context is provided, also shows which skills match the given task.

    Args:
        task_context: Optional task description to match relevant skills against.
    """
    import skills_engine
    skills = skills_engine.discover_skills()
    if not skills:
        return "No skills found. Create skills under ./skills/<skill-name>/SKILL.md or ~/.mem0/skills/<skill-name>/SKILL.md."

    lines = [f"Discovered Skills ({len(skills)}):"]
    for s in skills:
        scope = f"[{s.get('scope', 'global')}]"
        name = s.get('name', 'unnamed')
        desc = s.get('description', 'No description')
        lines.append(f"  - {scope} {name}: {desc}")

    if task_context:
        matched = skills_engine.match_skills(task_context, skills=skills)
        lines.append(f"\nSkills matched for task '{task_context}': {len(matched)}")
        for m in matched:
            lines.append(f"  * {m.get('name')} (match: {m.get('path')})")

    return "\n".join(lines)


@mcp.tool()
def execute_with_harness(task: str, scope: str = "personal", verify_command: str = "") -> str:
    """Prepare scoped context and record a trajectory without executing the task.

    Args:
        task: The task description or goal for the agent.
        scope: Memory and rule scope (default: 'personal').
        verify_command: Deprecated compatibility argument; never executed.
    """
    import harness
    result = harness.run_harness_task(
        task=task,
        scope=scope,
        verify_command=verify_command,
    )
    status = result.get("status", "unknown").upper()
    run_id = result.get("run_id", "")
    output = [
        f"Harness Execution [{status}] (run_id: {run_id})",
        "Safety Review: NOT PERFORMED (task execution is disabled)",
        "Task Execution: NOT PERFORMED (no trusted executor is configured)",
        "Verification: NOT RUN",
    ]

    if result.get("message"):
        output.append(result["message"])

    context = result.get("context", {})
    output.append(
        "Context availability: "
        f"memories={bool(context.get('memories'))}, "
        f"rules={bool(context.get('rules'))}, skills={bool(context.get('skills'))}, "
        f"graph_nodes={len(context.get('graph', []))}; "
        f"context_status={context.get('status', 'unknown')}"
    )

    return "\n".join(output)


@mcp.tool()
def compact_memories(scope: str = "personal", dry_run: bool = True) -> str:
    """Cluster related/fragmented memories in scope and synthesize them into authoritative notes.
    Consolidates multiple atomic facts, saves a synthesis memory, marks older ones as superseded,
    and updates the knowledge graph.

    Args:
        scope: Target project scope (default: 'personal').
        dry_run: If True, previews clusters and synthesis without modifying storage (default: True).
    """
    import compaction
    if not (scope or "").strip():
        raise ValueError("scope is required for memory compaction")
    res = compaction.compact_scope(scope=scope, dry_run=dry_run)
    mode = {
        "preview": "[DRY-RUN]",
        "completed": "[APPLIED]",
        "partial": "[PARTIAL]",
        "error": "[FAILED]",
        "no-op": "[NO-OP]",
    }.get(res.get("status"), "[DRY-RUN]" if res["dry_run"] else "[APPLIED]")
    lines = [
        f"Memory Compaction {mode} for scope '{scope}':",
        (f"Found {res['clusters_found']} clusters ({res.get('would_supersede', res['memories_compacted'])} memories would be superseded)."
         if res["dry_run"] else
         f"Found {res['clusters_found']} clusters ({res['memories_compacted']} memories superseded).")
    ]

    for i, s in enumerate(res.get("syntheses", []), 1):
        lines.append(f"\nCluster #{i} [{s.get('status', 'unknown')}] ({s.get('count', 0)} memories -> {', '.join(s.get('superseded_ids', []))}):")
        lines.append(s.get("synthesis", "").strip())
        if s.get("new_id"):
            lines.append(f"-> Created Synthesis Memory ID: {s['new_id']}")
        if s.get("error"):
            lines.append(f"-> Error: {s['error']}")
        if s.get("graph_error"):
            lines.append(f"-> Knowledge graph update failed: {s['graph_error']}")
        for failure in s.get("supersession_errors", []):
            lines.append(f"-> Failed to supersede {failure['id']}: {failure['error']}")

    return "\n".join(lines)


@mcp.tool()
def sweep_stale_memories(scope: str = "personal", workspace_dir: str = "", dry_run: bool = True) -> str:
    """Audit memories referencing specific source files and flag them if the files no longer exist.
    Helps prevent model hallucinations based on deleted/renamed source files.

    Args:
        scope: Target project scope (default: 'personal').
        workspace_dir: Absolute path to the workspace root to check files against (default: current directory).
        dry_run: If True, previews stale memories without modifying their status (default: True).
    """
    import compaction
    if not (scope or "").strip():
        raise ValueError("scope is required for stale memory sweeping")
    res = compaction.sweep_stale_memories(
        scope=scope,
        workspace_dir=workspace_dir or None,
        dry_run=dry_run,
    )
    mode = {
        "preview": "[DRY-RUN]", "completed": "[APPLIED]", "partial": "[PARTIAL]",
        "error": "[FAILED]", "no-op": "[NO-OP]",
    }.get(res.get("status"), "[DRY-RUN]" if res["dry_run"] else "[APPLIED]")
    lines = [
        f"Staleness Sweeper {mode} for scope '{scope}':",
        f"Audited against workspace: {res['workspace_dir']}",
        (f"Stale memories found: {res['stale_count']} (would update {res['stale_count']})."
         if res["dry_run"] else
         f"Stale memories found: {res['stale_count']} (updated {res.get('updated_count', 0)}, failed {res.get('failed_count', 0)})."),
    ]

    for c in res.get("candidates", []):
        mid = (c.get("id") or "")[:8]
        missing = ", ".join(c.get("missing_files", []))
        lines.append(f"- [{mid}] Missing file(s): {missing}")
        lines.append(f"  Snippet: {c.get('memory', '')[:80]}...")
        if "update_error" in c:
            lines.append(f"  Update failed: {c['update_error']}")

    return "\n".join(lines)


@mcp.tool()
def get_memory_health(scope: str = "personal", workspace_dir: str = "") -> str:
    """Evaluate structural health, staleness, rule coverage, and quality score for a memory scope.
    Returns quantitative health score (0-100), key metrics, penalties, and actionable recommendations.

    Args:
        scope: Target project scope (default: 'personal').
        workspace_dir: Absolute path to workspace root (default: current directory).
    """
    import ide_bridge
    owner = os.environ.get("MEM0_OWNER", "default")
    all_mem = read_inventory(owner=owner, scope=scope or None, include_superseded=True)

    res = ide_bridge.evaluate_health_score(
        scope=scope,
        workspace_dir=workspace_dir or None,
        memories=all_mem,
    )

    m = res["metrics"]
    lines = [
        f"Memory Health Score: {res['score']}/100 [{res['status']}] (scope: '{scope}')",
        f"  Total: {m['total_memories']} ({m['active_memories']} active, {m['superseded_memories']} superseded)",
        f"  Stale References: {m['stale_memories']}, Rules: {m['rules_count']}, Graph Nodes: {m['graph_nodes']}",
    ]

    if res.get("penalties"):
        lines.append("\nPenalties:")
        for p in res["penalties"]:
            lines.append(f"  * {p}")

    if res.get("recommendations"):
        lines.append("\nRecommendations:")
        for r in res["recommendations"]:
            lines.append(f"  -> {r}")

    return "\n".join(lines)


@mcp.tool()
def configure_ide_bridge(target: str = "all", workspace_dir: str = "", scope: str = "personal") -> str:
    """Export and configure mem0 integration for AI editor environments (Cursor, Claude Code, VS Code, Cline).

    Args:
        target: Target IDE ('cursor', 'claude', 'vscode', 'cline', or 'all').
        workspace_dir: Absolute path to workspace root.
        scope: Project scope for exported rules and memory instructions.
    """
    import ide_bridge
    ws = workspace_dir or "."
    target_clean = target.lower().strip()
    actions = []

    if target_clean in ("cursor", "all"):
        rules_files = ide_bridge.sync_cursor_rules(ws, scope=scope)
        mcp_file = ide_bridge.generate_mcp_config(ws, target="cursor")
        actions.append(f"Cursor: Synced {len(rules_files)} rules to .cursor/rules/*.mdc + {mcp_file}")

    if target_clean in ("claude", "all"):
        claude_file = ide_bridge.sync_claude_instructions(ws, scope=scope)
        actions.append(f"Claude Code: Injected memory recall guidelines into {claude_file}")

    if target_clean in ("vscode", "all"):
        vscode_file = ide_bridge.generate_mcp_config(ws, target="vscode")
        actions.append(f"VS Code: Generated {vscode_file}")

    if target_clean in ("cline", "all"):
        cline_file = ide_bridge.generate_mcp_config(ws, target="cline")
        actions.append(f"Cline/RooCode: Generated {cline_file}")

    return f"IDE Bridge Configured for '{target_clean}' in '{ws}':\n" + "\n".join(f"  - {a}" for a in actions)


if __name__ == "__main__":
    # Already validated above by build_server(); safe to re-read.
    mcp.run(transport=transport_from())



