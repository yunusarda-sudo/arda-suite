# Arda Core M2.3A — Deterministic Research Worker

Durum: deterministic fixture/local slice  
Canlı web, provider ve LLM synthesis: kapsam dışı

## Job contract

`jobs.type` değeri `research` olan job’ın `objective` alanı canonical JSON’dır:

```json
{
  "research_questions": ["..."],
  "source_refs": ["fixture://..."],
  "source_constraints": {"allowed_source_types": ["fixture"]},
  "max_sources": 2,
  "time_budget_seconds": 30,
  "required_output_format": ["research.json", "research.md"],
  "expected_claims": [{"claim_id": "claim-1", "text": "..."}]
}
```

Worker yalnız `claim(job_type=research)` ve lease-bound `describe` ile job
contract’ını alır. Core state’i doğrudan değiştirmez; start, artifact upload,
complete ve fail mevcut worker protocol üzerinden yapılır.

## Source, evidence and claim

Her source şu provenance alanlarını korur: `source_id`, `source_type`, `uri`,
`retrieved_at`, `title` ve `provenance`. Fixture adapter network kullanmaz ve
source içeriğini deterministik cümle evidence’larına böler.

Her claim `support_status` taşır:

- `supported`: bir veya daha fazla `evidence_id` zorunludur.
- `not_found`: edinilen kaynaklarda bulunamadı; fact olarak sunulmaz.
- `unsupported`: açıkça işaretlenebilir, fakat sessizce supported sayılamaz.

## Artifacts and failures

Worker iki verified artifact üretir:

- `research.json`: canonical, machine-readable result
- `research.md`: human-readable report

Source unavailable, malformed/partial source, budget exhaustion, invalid claim
mapping veya artifact hash hatası explicit job failure’dır. Aynı artifact identity
ve hash ile yapılan retry dedupe edilir; farklı hash conflict’tir. Lease fencing
ve idempotency Core’da kalır.

M2.3A deliberately does not include live browsing, arbitrary shell, secrets,
LLM synthesis, autonomous code execution or persistent learning.
