#!/usr/bin/env python3
"""Read-only retrieval evaluation against a caller-supplied relevance dataset."""

import argparse
import json
import math
import os
import sys
from pathlib import Path

MAX_DATASET_BYTES = 2 * 1024 * 1024
MAX_QUERIES = 500
MAX_K = 100


def validate_dataset(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("dataset must be an object with version: 1")
    scope = data.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("dataset scope must be a non-empty string")
    queries = data.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("dataset queries must be a non-empty list")
    if len(queries) > MAX_QUERIES:
        raise ValueError(f"dataset may contain at most {MAX_QUERIES} queries")
    seen = set()
    for index, item in enumerate(queries):
        if not isinstance(item, dict):
            raise ValueError(f"queries[{index}] must be an object")
        query_id, query = item.get("id"), item.get("query")
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError(f"queries[{index}].id must be a non-empty string")
        if query_id in seen:
            raise ValueError(f"duplicate query id: {query_id}")
        seen.add(query_id)
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"queries[{index}].query must be a non-empty string")
        relevant = item.get("relevant_ids")
        if (not isinstance(relevant, list) or not relevant or
                any(not isinstance(v, str) or not v.strip() for v in relevant)):
            raise ValueError(f"queries[{index}].relevant_ids must be a non-empty list of IDs")
        if len(set(relevant)) != len(relevant):
            raise ValueError(f"queries[{index}].relevant_ids contains duplicates")
        replacements = item.get("replacements", {})
        if (not isinstance(replacements, dict) or
                any(not isinstance(old, str) or not old.strip() or
                    not isinstance(new, str) or not new.strip()
                    for old, new in replacements.items())):
            raise ValueError(f"queries[{index}].replacements must map non-empty IDs to non-empty IDs")
    return data


def load_dataset(path):
    try:
        with Path(path).open("rb") as dataset_file:
            raw = dataset_file.read(MAX_DATASET_BYTES + 1)
        if len(raw) > MAX_DATASET_BYTES:
            raise ValueError(f"dataset must be at most {MAX_DATASET_BYTES} bytes")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read evaluation dataset: {exc}") from exc
    return validate_dataset(data)


def _status(hit):
    metadata = hit.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("search returned hit metadata that is not an object")
    return hit.get("status") or metadata.get("status") or "active"


def _dedupe_hits(hits):
    unique, positions = [], {}
    for hit in hits:
        if hit["id"] not in positions:
            positions[hit["id"]] = len(unique)
            unique.append(hit)
        elif _status(hit) == "superseded":
            # Conflicting duplicate metadata should fail conservatively: a
            # stale copy must not be hidden by an earlier active copy.
            unique[positions[hit["id"]]]["status"] = "superseded"
    return unique


def _require_store(client):
    # Mem0Client.search() intentionally degrades an unavailable store to an
    # empty list. The evaluator must distinguish that outage from zero hits.
    get_backend = getattr(client, "_get_memory_client", None)
    if callable(get_backend) and get_backend() is None:
        raise RuntimeError("memory store unavailable during evaluation")


class _ReadOnlyMem0Search:
    """Reuse one configured backend for the complete evaluation run."""

    def __init__(self, scope):
        from config import get_memory

        self.scope = scope
        self.owner = os.environ.get("MEM0_OWNER", "default")
        self.backend = get_memory()
        if self.backend is None:
            raise RuntimeError("memory store unavailable; evaluation was not run")

    def search(self, query, top_k, include_superseded=False):
        result = self.backend.search(
            query,
            filters={"user_id": self.owner, "run_id": self.scope},
            top_k=top_k,
        )
        hits = result.get("results", []) if isinstance(result, dict) else (result or [])
        if not include_superseded:
            hits = [hit for hit in hits if _status(hit) != "superseded"]
        return hits


def evaluate_dataset(dataset, client, k=5):
    dataset = validate_dataset(dataset)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise ValueError(f"k must be an integer from 1 to {MAX_K}")
    _require_store(client)

    recalls, reciprocal_ranks, ndcgs = [], [], []
    superseded_hits = total_normal_hits = leaking_queries = 0
    replacement_hits = replacement_total = replacement_mappings = 0
    per_query = []

    for item in dataset["queries"]:
        query = item["query"]
        historical = client.search(query, top_k=k, include_superseded=True)
        normal = client.search(query, top_k=k, include_superseded=False)
        if not isinstance(historical, list) or not isinstance(normal, list):
            raise ValueError("search must return a list of hit objects")
        if any(not isinstance(hit, dict) for hit in historical + normal):
            raise ValueError("search returned a hit that is not an object")
        for hit in historical + normal:
            if not isinstance(hit.get("id"), str) or not hit["id"].strip():
                raise ValueError("search returned a hit without a non-empty string id")
            _status(hit)
        historical = _dedupe_hits(historical)
        normal = _dedupe_hits(normal)

        relevant = set(item["relevant_ids"])
        # Score the ordinary retrieval path: historical results are fetched only
        # to verify supersession labels for replacement accounting.
        ranked_ids = list(dict.fromkeys(str(hit.get("id")) for hit in normal))
        hits = [rank for rank, hit_id in enumerate(ranked_ids, 1) if hit_id in relevant]
        recalls.append(len(set(ranked_ids) & relevant) / len(relevant))
        reciprocal_ranks.append(1 / hits[0] if hits else 0.0)
        dcg = sum(1 / math.log2(rank + 1) for rank in hits)
        ideal_hits = min(len(relevant), k)
        ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
        ndcgs.append(dcg / ideal_dcg if ideal_dcg else 0.0)

        leaked = sum(1 for hit in normal if _status(hit) == "superseded")
        superseded_hits += leaked
        total_normal_hits += len(normal)
        leaking_queries += leaked > 0

        historical_status = {str(hit.get("id")): _status(hit) for hit in historical}
        normal_ids = {str(hit.get("id")) for hit in normal}
        query_replacement_total = query_replacement_hits = 0
        for old_id, new_id in item.get("replacements", {}).items():
            replacement_mappings += 1
            if historical_status.get(old_id) == "superseded":
                replacement_total += 1
                query_replacement_total += 1
                found = new_id in normal_ids
                replacement_hits += found
                query_replacement_hits += found

        per_query.append({
            "id": item["id"],
            "recall_at_k": recalls[-1],
            "mrr_at_k": reciprocal_ranks[-1],
            "ndcg_at_k": ndcgs[-1],
            "superseded_hits": leaked,
            "returned_hits": len(normal),
            "replacement_mappings": len(item.get("replacements", {})),
            "eligible_replacements_at_k": query_replacement_total,
            "replacement_hits": query_replacement_hits,
        })

    count = len(dataset["queries"])
    metrics = {
        "recall_at_k": sum(recalls) / count,
        "mrr_at_k": sum(reciprocal_ranks) / count,
        "ndcg_at_k": sum(ndcgs) / count,
        "superseded_hit_rate": superseded_hits / total_normal_hits if total_normal_hits else 0.0,
        "superseded_query_rate": leaking_queries / count,
        "replacement_hit_rate": replacement_hits / replacement_total if replacement_total else None,
    }
    return {"scope": dataset["scope"], "k": k, "query_count": count,
            "replacement_mapping_count": replacement_mappings,
            "eligible_replacements_at_k": replacement_total,
            "metrics": metrics, "queries": per_query}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="path to caller-supplied JSON relevance dataset")
    parser.add_argument("--k", type=int, default=5, help="retrieval cutoff (default: 5)")
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        if not 1 <= args.k <= MAX_K:
            raise ValueError(f"k must be an integer from 1 to {MAX_K}")
        # Mem0Client.search() reinitializes its backend on every call. Use one
        # read-only scoped adapter so a larger dataset does not churn clients.
        client = _ReadOnlyMem0Search(scope=dataset["scope"])
        print(json.dumps(evaluate_dataset(dataset, client, k=args.k), indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
