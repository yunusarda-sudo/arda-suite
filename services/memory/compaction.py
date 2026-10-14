#!/usr/bin/env python3
"""
Memory Compaction & Staleness Sweeper Engine for mem0-local.
Identifies related/fragmented memories in a scope, clusters them,
synthesizes them into authoritative architectural summaries,
marks older fragments as superseded, and audits memories for references
to deleted files (staleness detection).
Stdlib-first: zero cloud dependencies, optional local Ollama acceleration.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from memory_store import MemoryReadError, read_inventory


def extract_keywords(text: str) -> set[str]:
    """Extracts significant alphanumeric keywords (length >= 4) from memory text."""
    words = re.findall(r"[A-Za-z0-9_]{4,}", (text or "").lower())
    # Filter common stop words
    stop = {
        "this", "that", "with", "from", "have", "will", "using", "when",
        "should", "must", "always", "never", "only", "also", "into", "been",
        "where", "were", "what", "which", "there", "their", "about",
    }
    return {w for w in words if w not in stop}


def cluster_memories(memories: list[dict], min_cluster_size: int = 2, similarity_threshold: float = 0.25) -> list[list[dict]]:
    """Groups related memories into clusters using Graph-Augmented Hybrid Clustering
    (text token Jaccard + knowledge graph topological neighbor overlap).
    """
    active = [m for m in memories if (m.get("metadata") or {}).get("status") != "superseded" and m.get("status") != "superseded"]
    if len(active) < min_cluster_size:
        return []

    # Pre-extract keywords, entities, and graph topological neighbors
    token_map = {}
    graph_neighbor_map = {}
    for m in active:
        mid = str(m.get("id") or m.get("memory") or "")
        text = str(m.get("memory") or "")
        kw = extract_keywords(text)
        graph_neighbors = set()
        # Try extracting entities and graph connections if graph_store is available
        try:
            import graph_store
            ents = set(graph_store.extract_entities(text))
            kw.update(ents)
            if m.get("id"):
                nbrs = graph_store.get_neighbors(str(m["id"]), direction="both")
                for nb in nbrs:
                    nid = nb.get("neighbor_id", "")
                    if nid and not nid.startswith("scope:"):
                        graph_neighbors.add(nid)
        except Exception:
            pass
        token_map[mid] = kw
        graph_neighbor_map[mid] = graph_neighbors

    clusters: list[list[dict]] = []
    used_ids = set()

    for i, m1 in enumerate(active):
        id1 = str(m1.get("id") or m1.get("memory") or "")
        if id1 in used_ids:
            continue

        cluster = [m1]
        tokens1 = token_map.get(id1, set())
        gnbrs1 = graph_neighbor_map.get(id1, set())
        if not tokens1 and not gnbrs1:
            continue

        for j, m2 in enumerate(active[i + 1:], start=i + 1):
            id2 = str(m2.get("id") or m2.get("memory") or "")
            if id2 in used_ids:
                continue

            tokens2 = token_map.get(id2, set())
            gnbrs2 = graph_neighbor_map.get(id2, set())
            if not tokens2 and not gnbrs2:
                continue

            intersection = len(tokens1 & tokens2)
            union = len(tokens1 | tokens2)
            text_sim = (intersection / union) if union > 0 else 0.0

            g_intersection = len(gnbrs1 & gnbrs2)
            g_union = len(gnbrs1 | gnbrs2)
            graph_sim = (g_intersection / g_union) if g_union > 0 else 0.0

            # Graph-augmented hybrid similarity
            if g_union > 0:
                hybrid_sim = 0.75 * text_sim + 0.25 * graph_sim
            else:
                hybrid_sim = text_sim

            if hybrid_sim >= similarity_threshold or intersection >= 3 or g_intersection >= 2:
                cluster.append(m2)

        if len(cluster) >= min_cluster_size:
            for item in cluster:
                used_ids.add(str(item.get("id") or item.get("memory") or ""))
            clusters.append(cluster)

    return clusters


def synthesize_cluster(cluster: list[dict], scope: str = "personal", use_llm: bool = True) -> str:
    """Synthesizes a cluster of memories into a single authoritative architectural note."""
    items = [str(m.get("memory") or "").strip() for m in cluster if (m.get("memory") or "").strip()]
    if not items:
        return ""

    if len(items) == 1:
        return items[0]

    # Optional LLM acceleration (OpenAI API or local Ollama)
    if use_llm:
        try:
            import urllib.request
            import config
            active_cfg = config.get_config()
            llm_info = active_cfg.get("llm", {})
            provider = llm_info.get("provider", "ollama")
            cfg = llm_info.get("config", {})

            prompt = (
                f"Synthesize the following {len(items)} related technical memories for project '{scope}' "
                "into a single concise, authoritative architectural note. "
                "Preserve all key rules, gotchas, configurations, and technical facts. "
                "Output ONLY the consolidated summary without commentary:\n\n"
                + "\n".join(f"- {it}" for it in items)
            )

            if provider == "openai":
                from openai import OpenAI
                client = OpenAI(api_key=cfg.get("api_key") or "none", base_url=cfg.get("openai_base_url"))
                resp = client.chat.completions.create(
                    model=cfg.get("model", config.LLM_MODEL),
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=300,
                    timeout=10,
                )
                out = (resp.choices[0].message.content or "").strip()
                if out and len(out) > 20:
                    return out
            else:
                ollama_base = cfg.get("ollama_base_url", "http://localhost:11434")
                ollama_url = f"{ollama_base.rstrip('/')}/api/generate"
                req_body = {
                    "model": cfg.get("model", config.LLM_MODEL),
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 300},
                }
                req = urllib.request.Request(
                    ollama_url,
                    data=json.dumps(req_body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    out = data.get("response", "").strip()
                    if out and len(out) > 20:
                        return out
        except Exception:
            pass

    # Deterministic fallback synthesis (stdlib-first)
    title = f"Consolidated Architectural Synthesis ({scope}):"
    points = []
    seen = set()
    for it in items:
        # Normalize
        clean = re.sub(r"^[-*•]\s*", "", it).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            points.append(f"- {clean}")

    return title + "\n" + "\n".join(points)


def get_memory_client():
    """Lazily retrieves configured Memory client."""
    try:
        from config import get_memory
        return get_memory()
    except Exception as exc:
        raise MemoryReadError(f"unable to initialize memory store: {exc}") from exc


def compact_scope(
    scope: str = "personal",
    dry_run: bool = False,
    min_cluster_size: int = 2,
    memories: Optional[list[dict]] = None,
) -> dict:
    """Finds clusters of memories in scope and consolidates them into synthesized notes."""
    if not (scope or "").strip():
        raise ValueError("scope is required for memory compaction")
    if memories is not None and not dry_run:
        raise ValueError("live compaction must read its own owner-scoped inventory")
    if memories is None:
        owner = os.environ.get("MEM0_OWNER", "default")
        all_mem = read_inventory(owner=owner, scope=scope, include_superseded=True)
        client = get_memory_client() if not dry_run else None
    else:
        all_mem = memories
        client = get_memory_client() if not dry_run else None
    if not dry_run and not client:
        raise MemoryReadError("memory store client is unavailable for live compaction")


    clusters = cluster_memories(all_mem, min_cluster_size=min_cluster_size)
    results = []

    for cluster in clusters:
        synthesis = synthesize_cluster(cluster, scope=scope)
        old_ids = [str(m.get("id") or "") for m in cluster if m.get("id")]
        res_entry = {
            "synthesis": synthesis,
            "superseded_ids": old_ids,
            "count": len(cluster),
            "status": "preview" if dry_run else "pending",
            "superseded_count": 0,
            "would_supersede": len(old_ids) if dry_run else 0,
        }

        if not dry_run:
            try:
                owner = os.environ.get("MEM0_OWNER", "default")
                # 1. Add synthesized memory
                add_res = client.add(
                    synthesis,
                    user_id=owner,
                    run_id=scope,
                    metadata={"type": "synthesis", "consolidated_from": old_ids, "status": "active"},
                )
                new_id = ""
                if isinstance(add_res, list) and add_res:
                    new_id = str(add_res[0].get("id") or "")
                elif isinstance(add_res, dict):
                    new_id = str(add_res.get("id") or "")
                if not new_id:
                    raise RuntimeError("memory store did not return an ID for the synthesis")

                # 2. Mark old memories as superseded
                supersession_errors = []
                for oid in old_ids:
                    try:
                        client.update(oid, metadata={"status": "superseded", "superseded_by": new_id})
                    except Exception as ex:
                        supersession_errors.append({"id": oid, "error": str(ex)})

                # 3. Update knowledge graph
                graph_error = None
                try:
                    import graph_store
                    if new_id:
                        graph_store.add_node(f"mem:{new_id}", label=f"Synthesis: {synthesis[:40]}", kind="synthesis")
                        for oid in old_ids:
                            graph_store.add_edge(f"mem:{oid}", f"mem:{new_id}", relation="consolidated_into")
                        graph_store.sync_memory_links(new_id, synthesis, metadata={"status": "active"}, scope=scope)
                except Exception as ex:
                    graph_error = str(ex)

                res_entry["new_id"] = new_id
                if graph_error is not None:
                    res_entry["graph_error"] = graph_error
                if supersession_errors or graph_error is not None:
                    res_entry["status"] = "partial"
                else:
                    res_entry["status"] = "completed"
                if supersession_errors:
                    res_entry["supersession_errors"] = supersession_errors
                res_entry["superseded_count"] = len(old_ids) - len(supersession_errors)
            except Exception as ex:
                res_entry["status"] = "error"
                res_entry["error"] = str(ex)

        results.append(res_entry)

    statuses = [entry["status"] for entry in results]
    if dry_run:
        overall_status = "preview"
    elif not statuses:
        overall_status = "no-op"
    elif "error" in statuses and not any(status in ("completed", "partial") for status in statuses):
        overall_status = "error"
    elif "error" in statuses or "partial" in statuses:
        overall_status = "partial"
    else:
        overall_status = "completed"

    return {
        "scope": scope,
        "dry_run": dry_run,
        "status": overall_status,
        "clusters_found": len(clusters),
        "memories_compacted": sum(entry["superseded_count"] for entry in results),
        "would_supersede": sum(entry["would_supersede"] for entry in results),
        "syntheses": results,
    }


def sweep_stale_memories(
    scope: str = "personal",
    workspace_dir: Optional[str] = None,
    dry_run: bool = True,
    memories: Optional[list[dict]] = None,
) -> dict:
    """Audits memories referencing specific source files and flags them if the files no longer exist."""
    if not (scope or "").strip():
        raise ValueError("scope is required for stale memory sweeping")
    if memories is not None and not dry_run:
        raise ValueError("live stale sweeping must read its own owner-scoped inventory")
    ws = Path(workspace_dir).resolve() if workspace_dir else Path(".").resolve()
    if memories is None:
        owner = os.environ.get("MEM0_OWNER", "default")
        all_mem = read_inventory(owner=owner, scope=scope, include_superseded=True)
        client = get_memory_client() if not dry_run else None
    else:
        all_mem = memories
        client = get_memory_client() if not dry_run else None
    if not dry_run and not client:
        raise MemoryReadError("memory store client is unavailable for live stale sweeping")

    active = [m for m in all_mem if (m.get("metadata") or {}).get("status") != "superseded" and m.get("status") != "superseded"]
    stale_candidates = []

    # Regex detecting referenced file paths with common code extensions
    file_pattern = re.compile(r"(?:^|[\s\"'`(])([a-zA-Z0-9_\-./]+\.(?:py|ts|js|jsx|tsx|json|yaml|yml|md|sql|prisma|sh|env))(?=[\s\"'`),:;]|$)")

    for m in active:
        mid = str(m.get("id") or "")
        text = str(m.get("memory") or "")
        matches = file_pattern.findall(text)

        missing_files = []
        for match in matches:
            # Skip URLs, generic schemas, or library names
            if "://" in match or match.startswith("http") or "/" not in match:
                continue
            path_cand = ws / match
            if not path_cand.exists():
                missing_files.append(match)

        if missing_files:
            entry = {
                "id": mid,
                "memory": text,
                "missing_files": missing_files,
            }
            if not dry_run and mid and client:
                try:
                    client.update(mid, metadata={"status": "stale", "missing_files": missing_files})
                    entry["status_updated"] = "stale"
                except Exception as ex:
                    entry["update_error"] = str(ex)
            stale_candidates.append(entry)


    updated_count = sum(1 for entry in stale_candidates if entry.get("status_updated") == "stale")
    failed_count = sum(1 for entry in stale_candidates if "update_error" in entry)
    if dry_run:
        status = "preview"
    elif not stale_candidates:
        status = "no-op"
    elif failed_count and not updated_count:
        status = "error"
    elif failed_count:
        status = "partial"
    else:
        status = "completed"

    return {
        "scope": scope,
        "workspace_dir": str(ws),
        "dry_run": dry_run,
        "status": status,
        "stale_count": len(stale_candidates),
        "updated_count": updated_count,
        "failed_count": failed_count,
        "candidates": stale_candidates,
    }
