# Offline retrieval comparison experiment

This tool compares ranking approaches against a caller-supplied local corpus
snapshot and relevance labels. It is experimental and does not change the
production search path or contact Mem0, Qdrant, or an embedding service.

```sh
PYTHONPATH=services/memory python3 services/memory/retrieval_compare.py ./retrieval-eval.json --k 5 --candidate-k 20
```

The dataset is one JSON file with a fixed scope and evaluation timestamp:

```json
{
  "version": 1,
  "scope": "project-alpha",
  "as_of": "2026-10-14T12:00:00Z",
  "corpus": [
    {
      "id": "memory-1",
      "memory": "The service uses PostgreSQL.",
      "scope": "project-alpha",
      "status": "active",
      "created_at": "2026-10-01T09:00:00Z",
      "metadata": {"authoritative": true}
    }
  ],
  "queries": [
    {
      "id": "database-choice",
      "query": "Which database does the service use?",
      "relevant_ids": ["memory-1"],
      "semantic_hits": [{"id": "memory-1", "score": 0.91}],
      "replacements": {}
    }
  ]
}
```

All corpus items and relevance/semantic IDs must belong to the dataset scope and
corpus. `semantic_hits` records the current semantic candidate list and score
for each query; obtain it from an approved, read-only snapshot process. Use
reviewed relevance labels. Keep real memory text and IDs out of Git.

`--candidate-k` sets how many provided semantic candidates enter the semantic
baseline and reranker; it defaults to `--k`. The baseline filters superseded
items after this candidate cutoff, then metrics use top-k. The reranker scores
the same candidate pool, filters superseded items, and returns top-k.

The report compares:

- `semantic_baseline`: the provided semantic candidate list, excluding
  superseded records after the candidate retrieval cutoff, matching the current
  client filter order; metrics are then measured at the requested `k`.
- `semantic_add_only_history`: the same semantic order with superseded history
  left visible. This is a retrieval ablation for retained history; it does not
  simulate or change memory writes.
- `bm25`: standard-library BM25 over the full active corpus (`k1=1.5`, `b=0.75`).
- `semantic_temporal_keyword`: reranks only active semantic candidates using
  the MCP-style exact-token boost, 90-day half-life decay, and authority flags.
  Graph expansion is intentionally excluded.

The candidate sets differ: BM25 can find any corpus item, while temporal and
keyword reranking cannot introduce a document absent from semantic hits. The
report records that boundary and must not be interpreted as a same-candidate
BM25 bake-off. Superseded counts on semantic methods describe raw superseded
candidates in the semantic candidate pool; the baseline's final ranked IDs
omit them, while the history ablation retains them. For BM25, the candidate
count is the set of documents with a non-zero lexical score.

Each method reports macro Recall@k, MRR@k, nDCG@k, paired deltas against the
semantic baseline (overall and per query), superseded candidate counts, and
replacement hit rate. Replacement mappings count as eligible only if the
old ID is marked superseded in the snapshot; the eligible count is shown next
to the rate. `superseded_candidate_rate` is normalized against each method's
candidate pool; candidate pools differ by method, so compare the count across
methods and interpret the rate within each method. The output includes a
SHA-256 fingerprint of the full dataset,
fixed scoring parameters, and per-query rankings. The input is capped at 2 MiB,
500 queries, 1,000 memories, and 500 semantic candidates per query; `k` is at
most 100.

This experiment can validate implementation mechanics with synthetic fixtures.
It cannot establish real retrieval quality until run with a reviewed, labeled
project dataset. Do not publish a conclusion from the synthetic unit tests.
