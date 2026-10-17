# Arda Core M2.4D2 — Behavioral Harness Contract

## Amaç

M2.4D2, bir `PatchResult` tarafından üretilen değişikliğin baseline ve patched
workspace üzerinde aynı test planı ile gözlemlenebilir davranış farkı üretip
üretmediğini ölçer. Bu katman yalnız davranış kanıtı üretir; requirement
doğrulaması, çözüm doğruluğu veya deployment approval vermez.

Akış:

```text
PatchResult
  → StructuralEvaluation = STRUCTURAL_PASS
  → aynı TestPlan + aynı runtime snapshot
  → baseline ve patched execution
  → BehavioralEvaluation
```

## TestPlan

`TestPlan` targeted ve regression testlerini, beklenen fixed test isimlerini,
`expected_behavior_changed` beklentisini, environment allowlist’ini, resource
limitlerini ve output limitlerini taşır. Her test argv-only çalışır; shell
string veya serbest çalışma komutu bu contract’ın parçası değildir.

Planın canonical JSON SHA-256 özeti `test_plan_hash` olarak evaluation’a yazılır.

Baseline ve patched çalıştırmalarında değişmeyenler:

- test adı ve argv
- cwd
- environment allowlist
- network policy (`none`)
- resource/output limitleri
- test timeout’ı
- runtime capability/image snapshot’ı

Değişen tek şey repository tree’dir.

## TestOutcome

Her execution için test adı, phase, pass/fail/execution_error status’ü, exit
code, stdout/stderr SHA-256 özetleri, duration, termination reason ve execution
sonrası workspace tree hash’i tutulur. Test sırasında workspace değişirse sonuç
`execution_error` olur; bu durum davranış sonucu gibi yorumlanmaz.

## Status vocabulary

| Status | Anlamı |
| --- | --- |
| `BEHAVIORAL_PASS` | Beklenen failure fixed oldu, regression yok ve execution hatası yok |
| `BEHAVIORAL_FAIL` | Patch sonrası beklenen testler hâlâ başarısız |
| `REGRESSION` | Baseline’da geçen bir test patched workspace’te geçmiyor |
| `INCONCLUSIVE` | Beklenen davranış değişimi gözlenmedi veya beklenen fixed seti tanımlı değil |
| `EXECUTION_ERROR` | Runtime, timeout veya workspace containment hatası oluştu |

`BEHAVIORAL_PASS` şu anlamlara gelmez: `REQUIREMENT_PASS`, `CORRECT` veya
`DEPLOYMENT_APPROVED`.

## Provenance

Her `BehavioralEvaluation` aşağıdakileri bağlar:

- `episode_id`
- `patch_result_id`
- `structural_evaluation_id`
- `test_plan_hash`
- `runtime_snapshot_hash`
- `baseline_tree_hash`
- `patched_tree_hash`

Structural evaluation `STRUCTURAL_PASS` değilse test çalıştırılmaz ve sonuç
`EXECUTION_ERROR / structural_gate_not_passed` olur.

## Failure-first kuralları

- baseline fail → patched pass: fixed failure sayılır
- baseline pass → patched fail: `REGRESSION`
- her ikisi pass, beklenen değişim yok: `INCONCLUSIVE`
- timeout, runtime exception veya termination reason: `EXECUTION_ERROR`
- workspace contamination: `EXECUTION_ERROR`
- aynı test planı iki workspace’te de çalıştırılmıyorsa evaluation üretilemez
- model, LLM, live provider veya autonomous browsing bu slice’ın parçası değildir
