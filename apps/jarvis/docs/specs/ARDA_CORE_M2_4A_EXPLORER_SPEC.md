# Arda Core M2.4A — Deterministic Repository Explorer

Status: implementation in progress; M2.4B is not started.

## Boundary

M2.4A is a read-only repository-context component. It scans a repository
snapshot, ranks bounded code regions, records provenance, and persists
measurement data. It does not use an LLM, execute shell commands, mutate the
repository, create patches, or report downstream patch success.

## Immutable records

`ExplorerRun` records what the explorer selected:

- explorer version and strategy/config hash;
- repository commit/tree identity and task hash;
- selected files/regions and ignored regions;
- context budget and context-pack hash;
- region provenance (`path`, blob hash, line range, reason, score);
- latency and resource usage.

`ExplorerEvaluation` is a separate post-hoc record written by an independent
evaluator. It may contain file recall, line recall, MRR, context efficiency,
irrelevant-context ratio, targeted/regression test results, and downstream
patch success. The explorer never self-reports these fields.

## Determinism

Identical repository snapshot, objective, explorer version, strategy config,
ignore rules, and context budget produce a byte-identical canonical result and
canonical hash. Run IDs, timestamps, and measured latency are operational
metadata and are excluded from the canonical result.

Ranking tie-breakers are explicit:

1. score descending;
2. path lexicographically ascending;
3. region start line ascending.

## Baseline strategy

`LexicalExplorer v1` uses deterministic filename/path and content token matches,
bounded regions, and explicit file/line budgets. Embeddings, dependency graphs,
LLM retrieval, and arbitrary shell execution are deferred challengers.

## Acceptance gate

- fixture repositories with explicit oracle regions;
- byte-identical canonical reruns;
- zero context-budget violations;
- verified blob/region provenance;
- append-only persisted run/evaluation records;
- no repository mutation or arbitrary shell;
- no LLM dependency;
- Pi-compatible execution;
- targeted and full regression suites passing.

Downstream patch and verification metrics remain unresolved until M2.4C/M2.4D
produce independent evidence.
