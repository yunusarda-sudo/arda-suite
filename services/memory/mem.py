#!/usr/bin/env python
"""
Tiny terminal client for your local mem0, with project isolation + attribution.

Scoping:
  --scope   project/context bucket (mem0 run_id) -> isolation
  --agent   who wrote it (mem0 agent_id)         -> attribution
  owner defaults to "default" (override with MEM0_OWNER)

Usage:
  ./mem.py add    "Uses Postgres"        --scope sample-panel --agent architect
  ./mem.py search "which database?"      --scope sample-panel
  ./mem.py log                           [--scope X] [--agent Y]   # who wrote what, when
  ./mem.py delete-all                    --scope sample-panel

  ./mem.py rules add await-services "Servis cagrilarinda await kullan." \
                    --scope sample-app --paths 'src/services/**' --severity high
  ./mem.py rules list  [--scope X] [--all]
  ./mem.py rules rm    await-services --scope sample-app

`rules` is a separate store (~/.mem0/rules/*.json, see rules.py) and needs
neither Ollama nor Qdrant — it stays usable with the stack down, which is when
you are most likely to be writing a rule about it.

Run with the venv python:
  ~/arda-suite/services/memory/.venv/bin/python ~/arda-suite/services/memory/mem.py log
"""

import argparse
import json
import os
import re
import sys
import urllib.request

import rules as rules_store
import tokens as tokens_store
import graph_store
import skills_engine
import harness as harness_runner

OWNER = os.environ.get("MEM0_OWNER", "default")
# mem0's get_all() caps results (~20) regardless of limit, hiding records once the
# store grows. Scroll Qdrant directly for list/log/rm so they stay reliable. mem0
# flattens custom metadata to the payload top level, so `type` lives at pl["type"].
_q_host = os.environ.get("MEM0_QDRANT_HOST", "localhost")
_q_port = os.environ.get("MEM0_QDRANT_PORT", "6333")
QDRANT_SCROLL = f"http://{_q_host}:{_q_port}/collections/mem0_local/points/scroll"


def _scroll(scope="", agent="", mtype="", include_superseded=False):
    body = json.dumps({"limit": 10000, "with_payload": True}).encode()
    req = urllib.request.Request(QDRANT_SCROLL, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        pts = json.loads(urllib.request.urlopen(req, timeout=10).read())["result"]["points"]
    except Exception as ex:
        print(f"  warning: unable to reach Qdrant ({ex})", file=sys.stderr)
        return []
    out = []
    for p in pts:
        pl = p.get("payload", {})
        if pl.get("user_id") != OWNER:
            continue
        if scope and pl.get("run_id") != scope:
            continue
        if agent and pl.get("agent_id") != agent:
            continue
        if mtype and pl.get("type") != mtype:
            continue
        status = pl.get("status") or (pl.get("metadata") or {}).get("status") or "active"
        if not include_superseded and status == "superseded":
            continue
        out.append({"id": str(p["id"]), "memory": pl.get("data", ""),
                    "run_id": pl.get("run_id"), "agent_id": pl.get("agent_id"),
                    "status": status,
                    "created_at": pl.get("created_at", "")})
    return out



def _filters(scope, agent):
    f = {"user_id": OWNER}
    if scope:
        f["run_id"] = scope
    if agent:
        f["agent_id"] = agent
    return f


def _attr(item, score=False):
    ts = (item.get("created_at") or "")[:16].replace("T", " ")
    scope = item.get("run_id") or "-"
    agent = item.get("agent_id") or "-"
    status = item.get("status") or (item.get("metadata") or {}).get("status") or "active"
    status_tag = " [SUPERSEDED]" if status == "superseded" else ""
    line = f"[{ts}] ({scope}/{agent}){status_tag}"
    if score:
        s = item.get("score")
        line += f" {s:.3f}" if isinstance(s, (int, float)) else ""
    return f"{line}  {item.get('memory', '')}"


def _globs(value):
    return [g.strip() for g in (value or "").split(",") if g.strip()]


def _rules_command(args) -> int:
    """The `rules` subtree. Overlap warnings go to stderr and the write still
    succeeds — the check is a heuristic, and no heuristic here can tell a real
    contradiction from a legitimate pair of rules sharing a directory."""
    if args.rules_cmd == "add":
        rule = {"id": args.id, "instruction": args.instruction,
                "paths": _globs(args.paths), "exclude_paths": _globs(args.exclude),
                "severity": args.severity,
                "evidence": [args.evidence] if args.evidence else []}
        try:
            warnings = rules_store.add_rule(args.scope, rule)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            return 1
        for warning in warnings:
            print(f"  warning: {warning}", file=sys.stderr)
        print(f"  added '{args.id}' to scope '{args.scope}'")

    elif args.rules_cmd == "list":
        items = rules_store.list_rules(args.scope, include_disabled=args.all)
        if not items:
            print("  (empty)")
        for rule in items:
            flag = "" if rule["status"] == "active" else "  (disabled)"
            print(f"  {rule['id']}  [{rule['severity']}] "
                  f"{rules_store.scope_text(rule)}{flag}\n      {rule['instruction']}")

    elif args.rules_cmd == "rm":
        if rules_store.remove_rule(args.scope, args.id):
            print(f"  deleted '{args.id}' from scope '{args.scope}'")
        else:
            print(f"  no rule '{args.id}' in scope '{args.scope}'", file=sys.stderr)
            return 1

    elif args.rules_cmd == "check":
        items = rules_store.match_rules_for_file(args.scope, args.path)
        if not items:
            print(f"  (no active rules apply to '{args.path}' in scope '{args.scope}')")
        for rule in items:
            print(f"  {rule['id']}  [{rule['severity']}] "
                  f"{rules_store.scope_text(rule)}\n      {rule['instruction']}")

    return 0


def _tokens_command(args) -> int:
    if args.tokens_cmd == "add":
        scopes = [s.strip() for s in (args.scopes or "").split(",") if s.strip()]
        tok, meta = tokens_store.generate_token(
            user_id=args.user, role=args.role, scopes=scopes, client_id=args.client
        )
        print(f"  generated token for user '{args.user}':")
        print(f"    token:  {tok}")
        print(f"    role:   {meta['role']}")
        if scopes:
            print(f"    scopes: {', '.join(scopes)}")
    elif args.tokens_cmd == "list":
        rows = tokens_store.list_tokens()
        if not rows:
            print("  (no tokens registered in ~/.mem0/tokens.json)")
            return 0
        for r in rows:
            scopes = ", ".join(r.get("scopes", [])) or "all"
            print(f"  {r['token_prefix']}  {r['token_masked']}  user: {r.get('user_id')}  role: {r.get('role')}  scopes: [{scopes}]")
    elif args.tokens_cmd == "rm":
        if tokens_store.revoke_token(args.token):
            print(f"  revoked token matching '{args.token}'")
        else:
            print(f"  no unique token matching '{args.token}'", file=sys.stderr)
            return 1
    return 0


def _graph_command(args) -> int:
    if args.graph_cmd == "neighbors":
        nbrs = graph_store.get_neighbors(args.id, direction=args.dir, relation=args.rel)
        if not nbrs:
            print(f"  (no neighbors found for '{args.id}')")
            return 0
        print(f"  neighbors of '{args.id}' ({len(nbrs)}):")
        for n in nbrs:
            arr = "-->" if n["direction"] == "outgoing" else "<--"
            print(f"    {arr} [{n['relation']}] {n['neighbor_id']} ({n['kind']}) : {n['label']}")

    elif args.graph_cmd == "path":
        p = graph_store.find_path(args.start, args.end, max_depth=args.depth)
        if not p:
            print(f"  (no path found between '{args.start}' and '{args.end}' within depth {args.depth})")
            return 1
        print(f"  shortest path ({len(p)-1} hops): " + " -> ".join(p))

    elif args.graph_cmd == "link":
        edge = graph_store.add_edge(args.source, args.target, relation=args.rel)
        print(f"  linked: {edge['source_id']} --({edge['relation']})--> {edge['target_id']}")

    elif args.graph_cmd == "export":
        data = graph_store.export_subgraph(args.id, max_hops=args.hops)
        print(json.dumps(data, indent=2, ensure_ascii=False))

    elif args.graph_cmd in ("hubs", "centrality"):
        hubs = graph_store.get_centrality_nodes(top_k=args.top_k, kind=args.kind, scope=args.scope)
        if not hubs:
            print("  (no graph nodes found)")
            return 0
        print(f"  top architectural hubs / degree centrality nodes ({len(hubs)}):")
        for h in hubs:
            print(f"    * [{h['total_degree']} links | in:{h['in_degree']} out:{h['out_degree']}] {h['id']} ({h['kind']}) : {h['label']}")

    return 0


def _skills_command(args) -> int:
    if args.skills_cmd == "list":
        skills = skills_engine.discover_skills()
        if not skills:
            print("  (no skills found in ./skills or ~/.claude/skills)")
            return 0
        print(f"  available skills ({len(skills)}):")
        for s in skills:
            print(f"    * {s['name']}: {s['description']}")
    elif args.skills_cmd == "check":
        matched = skills_engine.match_skills_for_task(args.task)
        if not matched:
            print(f"  (no skills matched for: '{args.task}')")
            return 0
        print(f"  matched skills ({len(matched)}):")
        for s in matched:
            print(f"    * {s['name']}: {s['description']}")
    return 0


def _harness_command(args) -> int:
    if args.harness_cmd == "run":
        res = harness_runner.run_harness_task(
            task=args.task,
            scope=args.scope,
            verify_command=args.verify,
        )
        status = "NOT EXECUTED" if not res.get("execution_performed") else ("PASSED" if res["verified"] else "FAILED")
        print(f"  Harness [{status}] run_id={res['run_id']}")
        print(f"  verification: {res.get('verification_status', 'not_run')}")
        print(f"  trajectory: {res['trajectory_file']}")
        ctx = res["context"]
        print(f"  context status: {ctx.get('status', 'unknown')} (memories={bool(ctx['memories'])}, rules={bool(ctx['rules'])}, skills={bool(ctx['skills'])})")
        return 0 if res.get("execution_performed") else 3
    return 0


def _compact_command(args) -> int:
    import compaction
    memories = _scroll(scope=args.scope, include_superseded=False)
    res = compaction.compact_scope(
        scope=args.scope,
        dry_run=args.dry_run,
        min_cluster_size=args.min_cluster,
        memories=memories,
    )
    mode = "[DRY-RUN]" if res["dry_run"] else "[APPLIED]"
    print(f"  Compaction {mode} in scope '{res['scope']}':")
    print(f"  found {res['clusters_found']} clusters ({res['memories_compacted']} memories compacted)")
    for s in res["syntheses"]:
        print("\n  --- Synthesis ---")
        for line in s["synthesis"].splitlines():
            print(f"  {line}")
        print(f"  consolidated {s['count']} memories: {', '.join(s['superseded_ids'])}")
        if s.get("new_id"):
            print(f"  new memory ID: {s['new_id']}")
    return 0


def _sweep_command(args) -> int:
    import compaction
    memories = _scroll(scope=args.scope, include_superseded=False)
    res = compaction.sweep_stale_memories(
        scope=args.scope,
        workspace_dir=args.workspace,
        dry_run=args.dry_run,
        memories=memories,
    )
    mode = "[DRY-RUN]" if res["dry_run"] else "[APPLIED]"
    print(f"  Staleness Sweeper {mode} in scope '{res['scope']}':")
    print(f"  audited against workspace: {res['workspace_dir']}")
    print(f"  stale candidates found: {res['stale_count']}")
    for c in res["candidates"]:
        mid = (c.get("id") or "")[:8]
        missing = ", ".join(c.get("missing_files", []))
        print(f"  - [{mid}] missing: {missing}")
        print(f"    text: {c.get('memory', '')[:70]}")
    return 0


def _ide_command(args) -> int:
    import ide_bridge
    target = args.target.lower()
    ws = args.workspace or "."
    created = []

    if target in ("cursor", "all"):
        rules_files = ide_bridge.sync_cursor_rules(ws, scope=args.scope)
        mcp_file = ide_bridge.generate_mcp_config(ws, target="cursor")
        created.extend(rules_files)
        created.append(str(mcp_file))
        print(f"  Cursor: synced {len(rules_files)} rules to .cursor/rules/*.mdc + .cursor/mcp.json")

    if target in ("claude", "all"):
        claude_file = ide_bridge.sync_claude_instructions(ws, scope=args.scope)
        created.append(claude_file)
        print(f"  Claude Code: injected mem0 guidelines into {claude_file}")

    if target in ("vscode", "all"):
        vscode_file = ide_bridge.generate_mcp_config(ws, target="vscode")
        created.append(str(vscode_file))
        print(f"  VS Code: generated {vscode_file}")

    if target in ("cline", "all"):
        cline_file = ide_bridge.generate_mcp_config(ws, target="cline")
        created.append(str(cline_file))
        print(f"  Cline/RooCode: generated {cline_file}")

    if target in ("antigravity", "all"):
        res = ide_bridge.sync_antigravity(ws, scope=args.scope)
        for act in res.get("actions", []):
            print(f"  Antigravity: {act}")

    print(f"  IDE setup complete for target '{target}'.")
    return 0


def _obsidian_command(args) -> int:
    import obsidian_bridge
    if args.obs_action == "export":
        res = obsidian_bridge.export_vault(vault_dir=args.vault, scope=args.scope)
        print(f"✅ Exported {res['active_memories']} active memories and {res['rules_exported']} rules to Obsidian vault:")
        print(f"   📂 {res['vault_path']}")
        print("   💡 Open this folder in Obsidian ('Open folder as vault') to view the visual Graph View!")
        return 0
    elif args.obs_action == "import":
        res = obsidian_bridge.import_inbox(vault_dir=args.vault)
        print(f"✅ Imported {res['imported']} note(s) from Obsidian Inbox into Central Brain:")
        for n in res.get("notes", []):
            print(f"   • {n['file']} -> scope: {n['scope']}")
        return 0
    elif args.obs_action == "status":
        st = obsidian_bridge.get_vault_status(vault_dir=args.vault)
        print(json.dumps(st, indent=2))
        return 0
    return 1


def _prompt_command(args) -> int:
    import ide_bridge
    prompt = ide_bridge.generate_universal_prompt(scope=args.scope, task=args.task or "")
    print(prompt)
    return 0


def _health_command(args) -> int:

    import ide_bridge
    memories = _scroll(scope=args.scope, include_superseded=True)
    res = ide_bridge.evaluate_health_score(
        scope=args.scope,
        workspace_dir=args.workspace,
        memories=memories,
    )
    score = res["score"]
    status = res["status"]
    m = res["metrics"]

    print(f"  Memory Health Score: {score}/100 [{status}] (scope: {res['scope']})")
    print(f"  metrics: {m['total_memories']} total ({m['active_memories']} active, {m['superseded_memories']} superseded)")
    print(f"           {m['stale_memories']} stale file references, {m['rules_count']} rules, {m['graph_nodes']} graph nodes")

    if res.get("penalties"):
        print("  penalties:")
        for p in res["penalties"]:
            print(f"    * {p}")

    if res.get("recommendations"):
        print("  recommendations:")
        for r in res["recommendations"]:
            print(f"    -> {r}")
    return 0


def main() -> int:


    p = argparse.ArgumentParser(description="Local mem0 CLI (Ollama, no API key)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="store a memory")
    a.add_argument("text")
    a.add_argument("--scope", default="personal")
    a.add_argument("--agent", default="cli")
    a.add_argument("--supersedes", default="", help="id or prefix of memory this replaces")
    a.add_argument("--verbatim", action="store_true", help="store text directly without LLM summarization")

    sup = sub.add_parser("supersede", help="mark a memory as superseded, optionally adding a replacement")
    sup.add_argument("id", help="id or prefix of memory to supersede")
    sup.add_argument("--new", default="", help="replacement memory text")
    sup.add_argument("--scope", default="personal")
    sup.add_argument("--agent", default="cli")
    sup.add_argument("--verbatim", action="store_true", help="store replacement verbatim without LLM extraction")

    s = sub.add_parser("search", help="semantic search")
    s.add_argument("query")
    s.add_argument("--scope", default="")
    s.add_argument("--agent", default="")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("--all", action="store_true", help="include superseded memories")

    l = sub.add_parser("log", help="who wrote what, when")
    l.add_argument("--scope", default="")
    l.add_argument("--agent", default="")
    l.add_argument("--all", action="store_true", help="include superseded memories")

    d = sub.add_parser("delete-all", help="wipe a scope (or everything)")
    d.add_argument("--scope", default="")

    ls = sub.add_parser("list", help="list memories with ids (for rm), optional --type")
    ls.add_argument("--scope", default="")
    ls.add_argument("--agent", default="")
    ls.add_argument("--type", default="", help="metadata type filter, e.g. lesson")
    ls.add_argument("--all", action="store_true", help="include superseded memories")

    r = sub.add_parser("rm", help="delete memories by id or id-prefix")
    r.add_argument("ids", nargs="+")

    ru = sub.add_parser("rules", help="scoped project/path rules (no Ollama/Qdrant needed)")
    rsub = ru.add_subparsers(dest="rules_cmd", required=True)

    ra = rsub.add_parser("add", help="add or replace a rule by id")
    ra.add_argument("id")
    ra.add_argument("instruction")
    ra.add_argument("--scope", default=rules_store.SHARED)
    ra.add_argument("--paths", default="", help="comma-separated globs; empty = project-wide")
    ra.add_argument("--exclude", default="", help="comma-separated globs to exempt")
    ra.add_argument("--severity", default=rules_store.DEFAULT_SEVERITY,
                    choices=list(rules_store.SEVERITIES))
    ra.add_argument("--evidence", default="", help="why this rule exists")

    rl = rsub.add_parser("list", help="rules in one scope")
    rl.add_argument("--scope", default=rules_store.SHARED)
    rl.add_argument("--all", action="store_true", help="include disabled rules")

    rr = rsub.add_parser("rm", help="delete a rule by id")
    rr.add_argument("id")
    rr.add_argument("--scope", default=rules_store.SHARED)

    rc = rsub.add_parser("check", help="test which rules match a file path")
    rc.add_argument("path", help="relative file path, e.g. src/services/user.ts")
    rc.add_argument("--scope", default=rules_store.SHARED)

    tk = sub.add_parser("tokens", help="multi-tenant token access management (no Ollama/Qdrant needed)")
    tsub = tk.add_subparsers(dest="tokens_cmd", required=True)

    ta = tsub.add_parser("add", help="create a new token for user/tenant")
    ta.add_argument("user", help="user or tenant ID")
    ta.add_argument("--role", default="developer", choices=["developer", "admin", "viewer"])
    ta.add_argument("--scopes", default="", help="comma-separated permitted scopes (empty = all)")
    ta.add_argument("--client", default="mcp-client", help="client identifier")

    tl = tsub.add_parser("list", help="list active tokens")

    tr = tsub.add_parser("rm", help="revoke a token by prefix or exact token")
    tr.add_argument("token", help="token prefix or full token")

    gk = sub.add_parser("graph", help="knowledge graph and GraphRAG relations (no Ollama/Qdrant needed)")
    gsub = gk.add_subparsers(dest="graph_cmd", required=True)

    gn = gsub.add_parser("neighbors", help="list neighbors of a node")
    gn.add_argument("id", help="node ID (e.g. memory ID or entity:tech)")
    gn.add_argument("--dir", default="both", choices=["both", "out", "in"])
    gn.add_argument("--rel", default=None, help="filter by relationship")

    gp = gsub.add_parser("path", help="find shortest path between two nodes")
    gp.add_argument("start", help="start node ID")
    gp.add_argument("end", help="end node ID")
    gp.add_argument("--depth", type=int, default=3, help="max search depth")

    gl = gsub.add_parser("link", help="create an edge between two nodes")
    gl.add_argument("source", help="source node ID")
    gl.add_argument("target", help="target node ID")
    gl.add_argument("--rel", default="relates_to", help="relationship type")

    ge = gsub.add_parser("export", help="export subgraph JSON")
    ge.add_argument("id", help="root node ID")
    ge.add_argument("--hops", type=int, default=2, help="max traversal hops")

    gh = gsub.add_parser("hubs", help="top architectural hubs and degree centrality nodes")
    gh.add_argument("--top-k", type=int, default=10, help="number of hubs to return")
    gh.add_argument("--kind", default=None, help="filter by node kind (entity, memory, scope)")
    gh.add_argument("--scope", default=None, help="filter by scope")

    sk = sub.add_parser("skills", help="SKILL.md discovery and matching (no Ollama/Qdrant needed)")
    sksub = sk.add_subparsers(dest="skills_cmd", required=True)
    skl = sksub.add_parser("list", help="list installed skills")
    skc = sksub.add_parser("check", help="test which skills match a task description")
    skc.add_argument("task", help="task description or prompt")

    hn = sub.add_parser("harness", help="context preparation and trajectory harness")
    hnsub = hn.add_subparsers(dest="harness_cmd", required=True)
    hnr = hnsub.add_parser("run", help="prepare context without executing a task")
    hnr.add_argument("task", help="task description")
    hnr.add_argument("--scope", default="personal")
    hnr.add_argument("--verify", default=None, help="command to verify task success (e.g. pytest)")

    cp = sub.add_parser("compact", help="hierarchical memory compaction and synthesis")
    cp.add_argument("--scope", default="personal", help="target scope (default: personal)")
    cp.add_argument("--dry-run", action="store_true", help="preview synthesis without modifying storage")
    cp.add_argument("--min-cluster", type=int, default=2, help="minimum cluster size (default: 2)")

    sw = sub.add_parser("sweep", help="audit memories for references to deleted files (staleness sweeper)")
    sw.add_argument("--scope", default="personal", help="target scope (default: personal)")
    sw.add_argument("--workspace", default=None, help="workspace root path (default: current directory)")
    sw.add_argument("--dry-run", action="store_true", help="preview stale memories without modifying storage")

    ide_p = sub.add_parser("ide", help="universal IDE & agent ecosystem bridge")
    ide_sub = ide_p.add_subparsers(dest="ide_cmd", required=True)
    ide_setup = ide_sub.add_parser("setup", help="configure Cursor, Claude Code, VS Code, or Cline")
    ide_setup.add_argument("--target", default="all", choices=["cursor", "claude", "vscode", "cline", "antigravity", "all"])
    ide_setup.add_argument("--workspace", default=None, help="workspace root path")
    ide_setup.add_argument("--scope", default="personal")

    hl = sub.add_parser("health", help="evaluate memory system health, quality, and staleness score")
    hl.add_argument("--scope", default="personal")
    hl.add_argument("--workspace", default=None, help="workspace root path")

    pr = sub.add_parser("prompt", help="generate universal system prompt context block for any AI model")
    pr.add_argument("--scope", default="personal")
    pr.add_argument("--task", default="", help="optional task description to match skills and graph entities")

    obs = sub.add_parser("obsidian", help="Obsidian Knowledge Vault bridge & markdown graph export")
    obs.add_argument("obs_action", choices=["export", "import", "status"], default="export", nargs="?", help="action (default: export)")
    obs.add_argument("--vault", default=None, help="vault root path (default: ~/mem0-vault)")
    obs.add_argument("--scope", default=None, help="export only specific scope")

    args = p.parse_args()

    # Rules, tokens, skills, and graph are local SQLite/files; loading the mem0 client for them
    # would make the command need a running Ollama and Qdrant it never talks to.
    if args.cmd == "rules":
        return _rules_command(args)
    if args.cmd == "tokens":
        return _tokens_command(args)
    if args.cmd == "graph":
        return _graph_command(args)
    if args.cmd == "skills":
        return _skills_command(args)
    if args.cmd == "harness":
        return _harness_command(args)
    if args.cmd == "compact":
        return _compact_command(args)
    if args.cmd == "sweep":
        return _sweep_command(args)
    if args.cmd == "ide":
        return _ide_command(args)
    if args.cmd == "health":
        return _health_command(args)
    if args.cmd == "prompt":
        return _prompt_command(args)
    if args.cmd == "obsidian":
        return _obsidian_command(args)



    from config import get_memory

    mem = get_memory()

    if args.cmd == "add":
        metadata = {"status": "active"}
        target_id = ""
        if args.supersedes:
            by_id = {i["id"]: i for i in _scroll(include_superseded=True)}
            matches = [fid for fid in by_id if fid and (fid == args.supersedes or fid.startswith(args.supersedes))]
            if not matches:
                print(f"  no match for superseded id: {args.supersedes}", file=sys.stderr)
                return 1
            if len(matches) > 1:
                print(f"  ambiguous prefix '{args.supersedes}': {len(matches)} matches — use more chars", file=sys.stderr)
                return 1
            target_id = matches[0]
            metadata["supersedes"] = target_id

        res = mem.add(args.text, user_id=OWNER, run_id=args.scope, agent_id=args.agent,
                      metadata=metadata, infer=not args.verbatim)
        results = res.get("results", [])
        for item in results:
            print(f"  [{item.get('event','ADD')}] {item.get('memory','')}")
        if not results:
            print("  (no new facts extracted)")

        if target_id and results:
            new_id = str(results[0].get("id", ""))
            try:
                mem.update(target_id, metadata={"status": "superseded", "superseded_by": new_id})
                print(f"  marked {target_id[:8]} as [SUPERSEDED] by {new_id[:8] if new_id else '(unknown)'}")
            except Exception as ex:
                print(f"  warning: failed to mark {target_id[:8]} as superseded: {ex}", file=sys.stderr)

    elif args.cmd == "supersede":
        by_id = {i["id"]: i for i in _scroll(include_superseded=True)}
        matches = [fid for fid in by_id if fid and (fid == args.id or fid.startswith(args.id))]
        if not matches:
            print(f"  no match for id: {args.id}", file=sys.stderr)
            return 1
        if len(matches) > 1:
            print(f"  ambiguous prefix '{args.id}': {len(matches)} matches — use more chars", file=sys.stderr)
            return 1
        target_id = matches[0]
        if args.new:
            meta = {"status": "active", "supersedes": target_id}
            res = mem.add(args.new, user_id=OWNER, run_id=args.scope, agent_id=args.agent,
                          metadata=meta, infer=not args.verbatim)
            results = res.get("results", [])
            for item in results:
                print(f"  [{item.get('event','ADD')}] {item.get('memory','')}")
            new_id = str(results[0].get("id", "")) if results else ""
            mem.update(target_id, metadata={"status": "superseded", "superseded_by": new_id})
            print(f"  marked {target_id[:8]} as [SUPERSEDED] by {new_id[:8] if new_id else '(unknown)'}")
        else:
            mem.update(target_id, metadata={"status": "superseded"})
            print(f"  marked {target_id[:8]} as [SUPERSEDED]")

    elif args.cmd == "search":
        res = mem.search(
            args.query, filters=_filters(args.scope, args.agent), top_k=args.top * 2
        )
        hits = res.get("results", [])
        if not args.all:
            hits = [h for h in hits if (h.get("status") or (h.get("metadata") or {}).get("status") or "active") != "superseded"]

        # Exact Token / Keyword Boost (Sparse-Dense Hybrid)
        raw_tokens = re.findall(r"[A-Za-z0-9_\-\.]{3,}", args.query.lower())
        stop_words = {"the", "and", "for", "with", "this", "that", "how", "what", "which", "where", "from", "are"}
        query_keywords = {t for t in raw_tokens if t not in stop_words}

        by_id = {}
        for h in hits:
            meta = h.get("metadata") or {}
            raw_score = float(h.get("score") or 0.0)

            kw_boost = 0.0
            if query_keywords:
                mem_text = (h.get("memory") or "").lower()
                matches = sum(1 for kw in query_keywords if kw in mem_text)
                if matches:
                    kw_boost = 0.12 * (matches / len(query_keywords))

            auth_mult = 1.0
            if meta.get("pinned") or meta.get("authoritative") or h.get("pinned"):
                auth_mult = 1.25
            elif meta.get("synthesized") or meta.get("reflexion") or "Consolidated Architectural" in (h.get("memory") or ""):
                auth_mult = 1.15

            h["score"] = round((raw_score + kw_boost) * auth_mult, 4)
            key = str(h.get("id") or h.get("memory"))
            by_id[key] = h

        # Multi-hop Knowledge Graph Expansion
        top_seeds = sorted(by_id.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[:3]
        seed_ids = [str(s.get("id") or "") for s in top_seeds if s.get("id")]
        if seed_ids:
            try:
                discovered = graph_store.multi_hop_query(start_ids=seed_ids, max_hops=1)
                for disc in discovered:
                    nid = disc.get("node_id")
                    if disc.get("kind") == "memory" and nid and nid not in by_id:
                        node = graph_store.get_node(nid)
                        if node:
                            props = node.get("properties") or {}
                            st = props.get("status") or "active"
                            if not args.all and st == "superseded":
                                continue
                            by_id[nid] = {
                                "id": nid,
                                "memory": props.get("text") or node.get("label") or "",
                                "run_id": props.get("scope") or args.scope or "personal",
                                "agent_id": props.get("agent_id") or "graph",
                                "status": st,
                                "created_at": node.get("updated_at") or "",
                                "score": 0.70,
                                "metadata": {"graph_expanded": True, "relation": disc.get("relation")},
                            }
            except Exception:
                pass

        final_hits = list(by_id.values())
        final_hits.sort(key=lambda h: h.get("score") or 0.0, reverse=True)
        final_hits = final_hits[:args.top]
        if not final_hits:
            print("  (no matches)")
        for h in final_hits:
            print("  " + _attr(h, score=True))

    elif args.cmd == "log":
        items = _scroll(args.scope, args.agent, include_superseded=args.all)
        if not items:
            print("  (empty)")
        for h in sorted(items, key=lambda i: i.get("created_at") or ""):
            print("  " + _attr(h))

    elif args.cmd == "delete-all":
        if args.scope:
            mem.delete_all(user_id=OWNER, run_id=args.scope)
            print(f"  wiped scope '{args.scope}'")
        else:
            mem.delete_all(user_id=OWNER)
            print("  wiped ALL memories for owner")

    elif args.cmd == "list":
        items = _scroll(args.scope, args.agent, args.type, include_superseded=args.all)
        if not items:
            print("  (empty)")
        for h in sorted(items, key=lambda i: i.get("created_at") or ""):
            print(f"  {(h.get('id') or '')[:8]}  " + _attr(h))

    elif args.cmd == "rm":
        by_id = {i["id"]: i for i in _scroll(include_superseded=True)}
        for token in args.ids:
            matches = [fid for fid in by_id if fid and (fid == token or fid.startswith(token))]
            if len(matches) == 1:
                mem.delete(matches[0])
                print(f"  deleted {matches[0][:8]}  {by_id[matches[0]].get('memory', '')[:55]}")
            elif not matches:
                print(f"  no match: {token}")
            else:
                print(f"  ambiguous prefix '{token}': {len(matches)} matches — use more chars")

    return 0


if __name__ == "__main__":
    sys.exit(main())
