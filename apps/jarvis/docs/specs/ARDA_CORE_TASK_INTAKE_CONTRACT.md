# Arda Core Task Intake Contract v0.1

Status: **DESIGN BASELINE — IMPLEMENTATION NOT STARTED**

Amaç, insan hedefini Hermes/provider katmanlarından önce Core’un typed ve
denetlenebilir gerçeklik modeline çevirmektir.

```text
HumanGoal
  → TaskIntakeRequest
  → immutable TaskContract revision 1
  → Episode
  → later planning/routing/execution
```

## Authority boundary

- Jarvis/UI yalnız `HumanGoal` ve kullanıcı tarafından verilen metadata’yı taşır.
- Task Intake, Core içinde typed contract ve episode oluşturur.
- Hermes henüz bu aşamada yoktur ve görevi yorumlayıp provider seçmez.
- Provider sonucu, plan veya model tahmini `EXPLICIT` requirement truth olamaz.
- TaskContract v1 yalnız kullanıcı/source tarafından açıkça verilen bilgilerle oluşturulur.
- Eksik bilgiler `UNRESOLVED`; model hipotezleri `CANDIDATE` olarak kalır.

## TaskIntakeRequest

```yaml
human_goal: string                    # zorunlu, ham kullanıcı ifadesi
task_type: research|coding|review|maintenance|diagnostic|other
objective: string                     # zorunlu, Core’un işlem hedefi
risk: low|medium|high|critical
privacy: public|internal|private|secret
required_capabilities: [string]
constraints: [string]
explicit_requirements: [Requirement]
acceptance_criteria: [string]
evidence_requirements: [string]
allowed_scope: [relative path/glob]
forbidden_changes: [relative path/glob]
unresolved_uncertainties: [string]
source_ref: string                    # UI/request/import referansı
created_by: USER|SYSTEM
```

Zorunlu alanlar:

- `human_goal`
- `task_type`
- `objective`
- `risk`
- `privacy`
- `source_ref`

`explicit_requirements` boş olabilir; bu durumda Episode tamamlanmış görev
olarak değil, requirement clarification gerektiren bir başlangıç olarak
işaretlenir.

## TaskContract v1

Mevcut `TaskContract` modelinin korunacağı alanlara ek olarak aşağıdaki
canonical alanlar gerekir:

```yaml
task_id: string
episode_id: string
objective: string
task_type: string
risk: string
privacy: string
required_capabilities: [string]
constraints: [string]
explicit_requirements: [Requirement]
inferred_requirements: []
forbidden_changes: [string]
allowed_scope: [string]
acceptance_criteria: [string]
evidence_requirements: [string]
unresolved_uncertainties: [string]
source_ref: string
created_by: USER|SYSTEM
```

`inferred_requirements` ilk intake’te boş olmalıdır. Promotion yalnız ayrı bir
evidence/promotion akışıyla yapılabilir.

Canonical hash şu alanların tamamını kapsar; secret değerleri veya credential
değerleri contract’a yazılmaz:

```text
task identity
episode identity
objective
classification/risk/privacy
capabilities
constraints
requirements
scope/forbidden changes
acceptance/evidence requirements
uncertainties
source/provenance
```

## Episode

Episode, aynı görevin tüm ilerideki kayıtlarını bağlayan Core identity’sidir.

```yaml
episode_id: string                    # immutable primary identity
task_id: string
human_goal: string                    # original input, append-only provenance
task_contract_revision_id: string
task_contract_hash: string
status: open|clarifying|planned|running|blocked|awaiting_decision|done|cancelled
created_by: USER|SYSTEM
source_ref: string
created_at: timestamp
updated_at: timestamp
```

Episode altında ileride şu kayıtlar bağlanır:

```text
TaskContract revisions
Intent / planning attempts
RouteProposal / RouteDecision
ProviderAttempts
ExplorerRuns
ExecutionRuns
PatchResults
Artifacts
Structural/Behavioral/Requirement/Alignment evaluations
Final outcome
```

## Invariants

1. `episode_id` ve `task_id` intake sonrası değişmez.
2. İlk contract revision’ın parent’ı yoktur ve revision number `1`’dir.
3. Episode ve contract aynı `episode_id` ile bağlanır.
4. Contract hash, saklanan canonical payload ile eşleşmelidir.
5. Aynı `source_ref` için aynı idempotency key ikinci Episode oluşturamaz.
6. İnsan hedefi ve ilk contract overwrite edilemez.
7. Clarification yeni revision üretir; eski revision silinmez/değişmez.
8. `CANDIDATE`, `UNRESOLVED` veya model çıktısı `EXPLICIT` kabul edilemez.
9. Eksik acceptance/evidence bilgisi `UNVERIFIED` riskini taşır; otomatik
   `SATISFIED` üretmez.
10. Intake provider çağırmaz, shell çalıştırmaz, worker başlatmaz ve approval
    vermez.
11. Episode oluşturmak execution authorization değildir.

## State semantics

```text
open
  → clarifying       # zorunlu bilgi/acceptance eksik
  → planned          # contract yeterli, henüz provider/worker yok
  → running
  → blocked
  → awaiting_decision
  → done | cancelled
```

`planned` durumuna geçmek yalnız contract’ın oluşturulduğunu belirtir; route,
approval veya execution sonucu değildir.

## Failure-first acceptance matrix

| Durum | Beklenen sonuç |
|---|---|
| boş human goal | reject |
| boş objective | reject |
| bilinmeyen task type | reject |
| risk/privacy vocabulary dışı değer | reject |
| episode/task identity mismatch | reject |
| secret/credential contract payload’ında | reject veya redact |
| duplicate source/idempotency key | mevcut Episode döndür, ikinci kayıt yok |
| eksik acceptance criteria | `clarifying`, execution yok |
| yalnız candidate requirement | `UNRESOLVED`, `SATISFIED` değil |
| clarification | yeni immutable revision |
| eski revision update/delete | reject |
| intake sırasında provider unavailable | intake etkilenmez; provider hiç çağrılmaz |
| episode oluşturma | approval/execution anlamına gelmez |

## Implementation boundary

Bu belge yalnız sözleşmedir. Sonraki ayrı implementation slice’ları:

1. `TaskIntakeRequest` / typed validation
2. TaskContract canonical payload/hash extension
3. Episode SQLite table + append-only revision linkage
4. idempotent intake service
5. Core diagnostics/read surface
6. Pi physical persistence gate

Bunlar tamamlanmadan Hermes routing veya provider registry execution path’e
bağlanmayacaktır.

## Current gap

Repo’da `TaskContract` ve requirement revision primitive’leri mevcut olsa da
bu intake sözleşmesindeki `task_type`, `risk`, `privacy`, `required_capabilities`,
`evidence_requirements`, raw `human_goal` ve Episode ledger alanları aktif Core
intake yüzeyinde henüz uygulanmış değildir.
