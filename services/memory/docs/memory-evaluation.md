# Memory retrieval evaluation

The evaluator measures retrieval and supersession behavior for one scope. It is
read-only with respect to stored memories: it calls search and never adds,
updates, or deletes memories. Supply a trusted local dataset; its `scope` selects
which run ID is searched under the current `MEM0_OWNER`. The configured embedding
service may still receive search queries. Do not commit private memory IDs or
user transcripts. Datasets are limited to 2 MiB and 500 queries; `--k` is limited
to 100.

```sh
PYTHONPATH=services/memory python3 services/memory/evaluate_retrieval.py ./my-eval.json --k 5
```

Dataset format:

```json
{
  "version": 1,
  "scope": "project-alpha",
  "queries": [
    {
      "id": "deployment-location",
      "query": "Where is the project deployed?",
      "relevant_ids": ["memory-id-1"],
      "replacements": {"old-memory-id": "memory-id-1"}
    }
  ]
}
```

`relevant_ids` contains the expected current answers for that query.
`replacements` maps a superseded ID to its replacement. Replacement hit rate
is conditional: a mapping is eligible only when the old ID appears in the
historical top-k results with `status: superseded`, and it is a hit when the
replacement appears in the ordinary top-k results. The report includes both
the total mapping count and eligible count, so this rate does not claim recall
over superseded memories outside the cutoff. The evaluator runs both a
historical search
(`include_superseded=True`) and the ordinary search for each query.

The report includes mean Recall@k, MRR@k, and nDCG@k from the ordinary result
ordering, plus superseded hit rate (superseded hits divided by ordinary-search
hits), superseded query rate (queries with any such hit), and replacement hit
rate. This means a stale hit in the ordinary result list can displace a relevant
answer and lower ranking metrics. Replacement hit rate is `null` when no eligible mapping was
found. A result of zero ordinary-search hits yields a superseded hit rate of 0.

This measures the configured semantic search stack only when run against a
live store. The unit tests use deterministic fake results and verify metric
calculation, not embedding quality, Qdrant persistence, or dataset validity.
