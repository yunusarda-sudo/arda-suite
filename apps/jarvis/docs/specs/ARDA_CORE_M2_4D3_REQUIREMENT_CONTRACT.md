# Arda Core M2.4D3 — Requirement Audit Contract

## Amaç

M2.4D3, testlerin yeşil olmasını kullanıcı gereksiniminin otomatik olarak
karşılandığı kabul etmez. Task sözleşmesi, PatchResult, StructuralEvaluation ve
BehavioralEvaluation birlikte incelenir; yalnız typed ve tekrar üretilebilir
evidence üreten kontroller `SATISFIED` olabilir.

```text
TaskContract
  → typed Requirement listesi
  → StructuralEvaluation gate
  → BehavioralEvaluation evidence
  → RequirementEvaluation
  → RequirementAudit
```

## TaskContract

`TaskContract` şu alanları taşır:

- `task_id`
- `episode_id`
- `objective`
- `explicit_requirements[]`
- `inferred_requirements[]`
- `forbidden_changes[]`
- `allowed_scope[]`
- `acceptance_criteria[]`
- `unresolved_uncertainties[]`

Explicit ve inferred requirement’lar provenance için ayrı tutulur. Free-form
metin bu deterministic slice’ta otomatik olarak doğrulanabilir requirement’a
dönüştürülmez.

## Typed requirement kinds

İlk v0.1 auditor şu primitive’leri destekler:

- `file_exists`
- `file_contains`
- `file_not_contains`
- `allowed_scope`
- `no_forbidden_changes`
- `behavioral_status`
- `no_regression`

Bunların dışındaki veya yalnız doğal dil ile verilen requirement’lar
`UNVERIFIED` kalır.

## RequirementEvaluation

Her evaluation şunları içerir:

- `requirement_id`
- source (`explicit` / `inferred`)
- status
- typed evidence listesi
- `episode_id`
- `task_contract_hash`
- `patch_result_id`
- `structural_evaluation_id`
- `behavioral_evaluation_id` (varsa)
- evaluator version ve failure class

Status vocabulary:

| Status | Anlamı |
| --- | --- |
| `SATISFIED` | Typed check gerçek patched workspace veya evaluation evidence’ı ile geçti |
| `VIOLATED` | Typed check açıkça başarısız veya forbidden/scope ihlali var |
| `UNVERIFIED` | Evidence eksik, requirement unsupported/free-text veya upstream gate geçmedi |
| `NOT_APPLICABLE` | Yalnız boş aggregate için kullanılır; bilinmeyen requirement için kullanılmaz |

`UNVERIFIED` hiçbir durumda `SATISFIED` sayılmaz.

## Aggregate policy

- Bir requirement `VIOLATED` ise aggregate `VIOLATED` olur.
- Violation yok ama herhangi bir requirement `UNVERIFIED` ise aggregate `UNVERIFIED` olur.
- Tüm requirement’lar `SATISFIED` ise aggregate `SATISFIED` olur.
- Requirement listesi boşsa aggregate `NOT_APPLICABLE` olur.
- StructuralEvaluation `STRUCTURAL_PASS` değilse requirement satisfaction
  yayınlanmaz; typed checks `UNVERIFIED / structural_gate_not_passed` olur.

`RequirementAudit.SATISFIED` yine `CORRECT` veya `DEPLOYMENT_APPROVED` değildir.
Requirement audit, Core policy ve human approval katmanlarının yerine geçmez.

## Güvenlik sınırları

- file target’ları absolute, traversal veya symlink escape içeremez.
- patched workspace dışındaki dosyalar okunmaz.
- evidence büyük içerik yerine reference/value hash’i taşır.
- LLM, implicit requirement recovery, live browsing ve model-assisted
  interpretation bu slice’ın parçası değildir.
