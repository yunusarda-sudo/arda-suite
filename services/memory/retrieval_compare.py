#!/usr/bin/env python3
"""Offline, read-only comparison of memory retrieval ranking strategies."""

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_DATASET_BYTES = 2 * 1024 * 1024
MAX_QUERIES = 500
MAX_CORPUS = 1000
MAX_SEMANTIC_HITS = 500
MAX_K = 100
MCP_STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "how", "what", "which", "where", "from", "are",
}
BM25_K1 = 1.5
BM25_B = 0.75
TEMPORAL_HALF_LIFE_DAYS = 90.0


def _parse_time(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _tokens(text):
    return re.findall(r"[^\W_]+", text.lower(), flags=re.UNICODE)


def _mcp_keywords(text):
    return {
        token for token in re.findall(r"[A-Za-z0-9_\-.]{3,}", text.lower())
        if token not in MCP_STOP_WORDS
    }


def validate_dataset(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("dataset must be an object with version: 1")
    scope = data.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("dataset scope must be a non-empty string")
    as_of = _parse_time(data.get("as_of"), "as_of")

    corpus = data.get("corpus")
    if not isinstance(corpus, list) or not corpus:
        raise ValueError("corpus must be a non-empty list")
    if len(corpus) > MAX_CORPUS:
        raise ValueError(f"corpus may contain at most {MAX_CORPUS} memories")
    by_id = {}
    for index, memory in enumerate(corpus):
        if not isinstance(memory, dict):
            raise ValueError(f"corpus[{index}] must be an object")
        mid = memory.get("id")
        if not isinstance(mid, str) or not mid.strip() or mid in by_id:
            raise ValueError(f"corpus[{index}].id must be unique and non-empty")
        if memory.get("scope") != scope:
            raise ValueError(f"corpus[{index}] scope does not match dataset scope")
        if not isinstance(memory.get("memory"), str):
            raise ValueError(f"corpus[{index}].memory must be a string")
        metadata = memory.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"corpus[{index}].metadata must be an object")
        created_at = memory.get("created_at")
        if created_at:
            _parse_time(created_at, f"corpus[{index}].created_at")
        by_id[mid] = memory

    queries = data.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("queries must be a non-empty list")
    if len(queries) > MAX_QUERIES:
        raise ValueError(f"queries may contain at most {MAX_QUERIES} items")
    query_ids = set()
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            raise ValueError(f"queries[{index}] must be an object")
        qid, text = query.get("id"), query.get("query")
        if not isinstance(qid, str) or not qid.strip() or qid in query_ids:
            raise ValueError(f"queries[{index}].id must be unique and non-empty")
        query_ids.add(qid)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"queries[{index}].query must be non-empty")
        relevant = query.get("relevant_ids")
        if (not isinstance(relevant, list) or not relevant or
                any(not isinstance(mid, str) or mid not in by_id for mid in relevant)):
            raise ValueError(f"queries[{index}].relevant_ids must reference corpus IDs")
        if len(set(relevant)) != len(relevant):
            raise ValueError(f"queries[{index}].relevant_ids must be unique")
        hits = query.get("semantic_hits")
        if not isinstance(hits, list) or len(hits) > MAX_SEMANTIC_HITS:
            raise ValueError(f"queries[{index}].semantic_hits must be a list of at most {MAX_SEMANTIC_HITS}")
        seen_hits = set()
        for hit in hits:
            if not isinstance(hit, dict):
                raise ValueError(f"queries[{index}].semantic_hits entries must be objects")
            mid, score = hit.get("id"), hit.get("score")
            if not isinstance(mid, str) or mid not in by_id or mid in seen_hits:
                raise ValueError(f"queries[{index}].semantic_hits IDs must be unique corpus IDs")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError(f"queries[{index}].semantic_hits scores must be finite numbers")
            seen_hits.add(mid)
        replacements = query.get("replacements", {})
        if not isinstance(replacements, dict):
            raise ValueError(f"queries[{index}].replacements must be an object")
        for old_id, new_id in replacements.items():
            if (not isinstance(old_id, str) or old_id not in by_id or
                    not isinstance(new_id, str) or new_id not in by_id):
                raise ValueError(f"queries[{index}].replacements must reference corpus IDs")
    return data


def load_dataset(path, max_bytes=MAX_DATASET_BYTES):
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    try:
        with Path(path).open("rb") as source:
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"dataset must be at most {max_bytes} bytes")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read comparison dataset: {exc}") from exc
    return validate_dataset(data)


def _status(memory):
    metadata = memory.get("metadata") or {}
    return memory.get("status") or metadata.get("status") or "active"


def _active(memory):
    return _status(memory) != "superseded"


def _recency_factor(created_at, as_of):
    if not created_at:
        return 1.0
    created = _parse_time(created_at, "created_at")
    days = max(0.0, (as_of - created).total_seconds() / 86400.0)
    decay = math.pow(0.5, days / TEMPORAL_HALF_LIFE_DAYS)
    return 0.6 + 0.4 * decay


def _authority_factor(memory):
    metadata = memory.get("metadata") or {}
    if metadata.get("pinned") or metadata.get("authoritative") or memory.get("pinned"):
        return 1.25
    if (metadata.get("synthesized") or metadata.get("reflexion") or
            "Consolidated Architectural" in memory.get("memory", "")):
        return 1.15
    return 1.0


def _bm25_rank(query_text, corpus, top_k):
    documents = [m for m in corpus if _active(m)]
    query_terms = set(_tokens(query_text))
    if not query_terms or not documents:
        return []
    tokenized = {m["id"]: _tokens(m["memory"]) for m in documents}
    lengths = {mid: len(tokens) for mid, tokens in tokenized.items()}
    average_length = sum(lengths.values()) / len(documents) if documents else 0.0
    df = {term: sum(term in set(tokens) for tokens in tokenized.values()) for term in query_terms}
    scored = []
    n_docs = len(documents)
    for memory in documents:
        terms = tokenized[memory["id"]]
        counts = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            norm = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * lengths[memory["id"]] / (average_length or 1.0))
            score += idf * tf * (BM25_K1 + 1.0) / norm
        if score > 0:
            scored.append((score, memory["id"]))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [mid for _score, mid in scored[:top_k]]


def _semantic_rerank(query, corpus_by_id, as_of, candidate_k, top_k):
    tokens = _mcp_keywords(query["query"])
    scored = []
    for hit in query["semantic_hits"][:candidate_k]:
        memory = corpus_by_id[hit["id"]]
        if not _active(memory):
            continue
        text = memory["memory"].lower()
        matches = sum(1 for token in tokens if token in text)
        keyword_boost = 0.12 * (matches / len(tokens)) if tokens else 0.0
        score = (hit["score"] + keyword_boost)
        score *= _recency_factor(memory.get("created_at"), as_of)
        score *= _authority_factor(memory)
        scored.append((score, hit["id"]))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [mid for _score, mid in scored[:top_k]]


def _metrics(query_items, k, corpus_by_id):
    recall, reciprocal_rank, ndcg = [], [], []
    leakage_count = leakage_queries = eligible_replacements = replacement_hits = 0
    candidate_count = 0
    per_query = {}
    for query, ranked_ids, leakage_ids, raw_candidate_count in query_items:
        relevant = set(query["relevant_ids"])
        returned = ranked_ids[:k]
        hits = [rank for rank, mid in enumerate(returned, 1) if mid in relevant]
        recall.append(len(set(returned) & relevant) / len(relevant))
        reciprocal_rank.append(1.0 / hits[0] if hits else 0.0)
        dcg = sum(1.0 / math.log2(rank + 1) for rank in hits)
        ideal_count = min(len(relevant), k)
        ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        ndcg.append(dcg / ideal_dcg if ideal_dcg else 0.0)
        leaking = len(leakage_ids)
        leakage_count += leaking
        leakage_queries += leaking > 0
        candidate_count += raw_candidate_count
        replacements = query.get("replacements", {})
        for old_id, new_id in replacements.items():
            if _status(corpus_by_id[old_id]) == "superseded":
                eligible_replacements += 1
                replacement_hits += new_id in returned
        per_query[query["id"]] = {
            "recall": recall[-1],
            "mrr": reciprocal_rank[-1],
            "ndcg": ndcg[-1],
            "ranked_ids": returned,
            "superseded_leakage_count": leaking,
        }
    count = len(query_items)
    return {
        "metrics": {
            "recall": sum(recall) / count,
            "mrr": sum(reciprocal_rank) / count,
            "ndcg": sum(ndcg) / count,
            "superseded_leakage_count": leakage_count,
            "superseded_leakage_queries": leakage_queries,
            "superseded_candidate_rate": leakage_count / candidate_count if candidate_count else 0.0,
            "replacement_eligible_count": eligible_replacements,
            "replacement_hit_rate": replacement_hits / eligible_replacements if eligible_replacements else None,
        },
        "per_query": per_query,
    }


def evaluate(dataset, k=5, candidate_k=None):
    dataset = validate_dataset(dataset)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise ValueError(f"k must be an integer from 1 to {MAX_K}")
    candidate_k = k if candidate_k is None else candidate_k
    if (not isinstance(candidate_k, int) or isinstance(candidate_k, bool) or
            not 1 <= candidate_k <= MAX_SEMANTIC_HITS):
        raise ValueError(f"candidate_k must be an integer from 1 to {MAX_SEMANTIC_HITS}")
    corpus_by_id = {memory["id"]: memory for memory in dataset["corpus"]}
    as_of = _parse_time(dataset["as_of"], "as_of")
    strategy_rows = {
        "semantic_baseline": [],
        "semantic_add_only_history": [],
        "bm25": [],
        "semantic_temporal_keyword": [],
    }
    query_report = {}

    for query in dataset["queries"]:
        semantic_raw = [hit["id"] for hit in query["semantic_hits"][:candidate_k]]
        semantic_leaks = [mid for mid in semantic_raw if not _active(corpus_by_id[mid])]
        semantic_active = [mid for mid in semantic_raw if _active(corpus_by_id[mid])]
        bm25_ids = _bm25_rank(query["query"], dataset["corpus"], k)
        temporal_ids = _semantic_rerank(query, corpus_by_id, as_of, candidate_k, k)
        query_report[query["id"]] = {
            "semantic_baseline": {"ranked_ids": semantic_active[:k]},
            "semantic_add_only_history": {"ranked_ids": semantic_raw[:k]},
            "bm25": {"ranked_ids": bm25_ids},
            "semantic_temporal_keyword": {"ranked_ids": temporal_ids},
        }
        strategy_rows["semantic_baseline"].append((query, semantic_active, semantic_leaks, len(semantic_raw)))
        strategy_rows["semantic_add_only_history"].append((query, semantic_raw, semantic_leaks, len(semantic_raw)))
        strategy_rows["bm25"].append((query, bm25_ids, [], len(bm25_ids)))
        strategy_rows["semantic_temporal_keyword"].append((query, temporal_ids, semantic_leaks, len(semantic_raw)))

    methods = {}
    for name, rows in strategy_rows.items():
        result = _metrics(rows, k, corpus_by_id)
        methods[name] = {"name": name, "metrics": result["metrics"], "per_query": result["per_query"]}
    baseline = methods["semantic_baseline"]
    comparison_metrics = ("recall", "mrr", "ndcg")
    for name, method in methods.items():
        method["delta_vs_semantic_baseline"] = {
            metric: method["metrics"][metric] - baseline["metrics"][metric]
            for metric in comparison_metrics
        }
        for qid, per_query in method["per_query"].items():
            baseline_query = baseline["per_query"][qid]
            per_query["delta_vs_semantic_baseline"] = {
                metric: per_query[metric] - baseline_query[metric]
                for metric in comparison_metrics
            }
    fingerprint_input = json.dumps(dataset, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "scope": dataset["scope"],
        "k": k,
        "candidate_k": candidate_k,
        "query_count": len(dataset["queries"]),
        "corpus_count": len(dataset["corpus"]),
        "dataset_sha256": hashlib.sha256(fingerprint_input).hexdigest(),
        "parameters": {
            "bm25_k1": BM25_K1,
            "bm25_b": BM25_B,
            "temporal_half_life_days": TEMPORAL_HALF_LIFE_DAYS,
            "temporal_floor": 0.6,
            "keyword_boost": 0.12,
            "as_of": dataset["as_of"],
            "candidate_note": "BM25 searches the full corpus; temporal/keyword reranking only reorders semantic hits.",
        },
        "methods": methods,
        "queries": query_report,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="path to a local labeled corpus snapshot JSON")
    parser.add_argument("--k", type=int, default=5, help="ranking cutoff (default: 5, max: 100)")
    parser.add_argument("--candidate-k", type=int, help="semantic candidate pool depth (default: --k, max: 500)")
    args = parser.parse_args(argv)
    try:
        result = evaluate(load_dataset(args.dataset), k=args.k, candidate_k=args.candidate_k)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
