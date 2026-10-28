# Arda Core M1 — Awareness Layer Specification

Durum: Tasarım sözleşmesi — incremental implementation
Sürüm: M1-design-0.1
Tarih: 2026-10-01

## 1. Amaç

M1, Arda Core’un yalnızca state saklayan bir kernel olmaktan çıkıp kendi
durumunu düzenli ve deterministik biçimde kontrol etmeye başlamasını sağlar.

M1’in ilk dilimi:

```text
Scheduler
  ↓
scheduler.tick event
  ↓
deterministic checks
  ↓
ProjectMetric projection
  ↓
digest event
```

## 2. Kapsam

M1 scheduler/event dilimi şunları kapsar:

- scheduler tick üretimi
- tick idempotency
- deadline ve inactivity check tasarımı
- project metrics projection modeli
- morning digest request event’i
- restart/recovery davranışı
- scheduler health ve audit event’leri

M1’in bu dilimi şunları kapsamaz:

- LLM veya Hermes
- GitHub write
- coding/reviewer worker
- browser
- learning/reflection
- Network Guardian
- external notification gönderimi

## 2.1 Implementation Status

### Scheduler + Event Trigger

Status: `IMPLEMENTED`

Commit:

```text
8f47857 feat(core): add M1 awareness scheduler
```

Validated:

- v2 SQLite migration ve tekrar çalıştırma
- schedule/tick state ve `window_key` idempotency
- deterministic project metrics
- deadline approaching/urgent/overdue event’leri
- inactivity warning/stale event’leri
- morning digest request/generated event’leri
- running tick timeout recovery
- scheduler event deduplication
- provider olmadan local metrics çalışması
- isolated Python 3.11 ortamında full repo test suite

Henüz uygulanmayan M1/M2 katmanları:

- gerçek notification provider
- Mac worker
- Hermes adapter
- learning/reflection
- Network Guardian

### Project Registry + Task Store

Status: `IMPLEMENTED`

Bu dilim Core içindeki canonical project/task state için dar bir yazma
sınırı sağlar:

- Project Registry, proje oluşturma, listeleme, okuma ve status güncellemesini
  yönetir.
- Task Store, task oluşturma, listeleme, okuma ve status güncellemesini
  yönetir.
- Project ve task status vocabulary’si model ile aynıdır; `running` veya
  `waiting` gibi alternatif state isimleri kullanılmaz.
- Her güncelleme `version` alanı üzerinden optimistic locking ile korunur.
- `critical` task priority’si açık insan onayı olmadan atanamaz.
- Mutasyonlar `project.created`, `project.status_changed`, `task.created` ve
  `task.status_changed` audit event’leri üretir.

Commit:

```text
f63c66a feat(core): add M1 project registry and task store
```

Bu katman GitHub, worker, Hermes, notification veya LLM çağrısı yapmaz.

### Read-only Awareness Reader

Status: `IMPLEMENTED`

`AwarenessReader`, canonical project kaydını, mevcut `project_metrics`
projection’ını ve local task listesini tek bir read-only snapshot olarak sunar.
Projection henüz üretilmemişse `metrics` alanı `null` kalır; reader projection
üretmez ve event yazmaz. GitHub/provider alanları nullable ve dış sistemlerden
bağımsız kalır.

### M1.5 — Read-only Diagnostics

Status: `IMPLEMENTED`

Diagnostics komutları query-only SQLite bağlantısı kullanır; migration, queue
recovery, scheduler tick veya provider sync çalıştırmaz:

```text
arda status
arda projects
arda jobs
arda events
arda doctor
arda github status
```

`doctor` SQLite erişimi, migration seviyesi, queue tutarlılığı, scheduler’ın
son gözlenen tick’i, GitHub stale state’i, disk alanı, event log okunabilirliği
ve backup durumunu raporlar. Backup altyapısı henüz kurulmadığı için bu alan
`unknown` olarak kalır.

### GitHub Read-only Adapter

Status: `IMPLEMENTED`

`GitHubReadOnlyAdapter` yalnızca GET çağrıları yapar ve repository, issue, pull
request ve CI durumunu normalize eder. `GitHubSyncService` bu veriyi
`project_metrics` içindeki nullable provider alanlarına yazar ve
`github.sync.completed` audit event’i üretir.

Bu dilimde GitHub write endpoint’i, push, issue/PR oluşturma, merge veya
notification yoktur. Provider erişimi başarısız olduğunda local metrics
projection’ı silinmez; provider alanları son bilinen değer olarak kalır.

Provider sync scheduler’a opsiyonel `github_sync` tick’i olarak bağlanır.
Adapter ve project-to-repository mapping yapılandırılmadığında bu schedule
kurulmaz; local scheduler ve metrics kontrolleri çalışmaya devam eder.
Provider hatası `github.sync.failed` event’i ile `stale: true` işaretlenir.

## 3. Scheduler sınırı

Scheduler karar veren bir agent değildir. Yalnızca zaman tabanlı tetik üretir
ve deterministic check’leri kuyruğa alır.

```text
Scheduler
├── clock
├── schedule definitions
├── tick deduplication
├── check dispatch
└── scheduler events
```

Scheduler’ın LLM’e veya dış API’ye doğrudan erişimi yoktur.

## 4. Schedule modeli

İlk sürümde schedule tanımları kod/config ile sabitlenir; kullanıcı tarafından
dinamik olarak çalıştırılabilir kod yazılmaz.

```text
Schedule
--------
id
name
kind
interval_seconds?
cron_expression?
enabled
catch_up_policy
last_run_at?
next_run_at
version
```

`kind` örnekleri:

```text
hourly_health_check
deadline_check
inactivity_check
morning_digest
```

İlk varsayılanlar:

| Schedule | Sıklık | Üretilen iş |
|---|---:|---|
| `hourly_health_check` | 1 saat | project metrics refresh |
| `deadline_check` | 1 saat | deadline events |
| `inactivity_check` | 1 gün | inactivity events |
| `morning_digest` | 08:00 | digest request event |

Timezone açıkça `Europe/Istanbul` olarak config’te tanımlanır; event timestamp’leri
UTC saklanır.

## 5. Tick modeli

Her scheduler çalışması bir tick üretir:

```text
SchedulerTick
------------
tick_id
schedule_id
scheduled_at
observed_at
window_key
status
created_at
```

`window_key`, aynı zaman penceresinde aynı schedule’ın iki kez çalışmasını
engeller:

```text
<schedule_id>:<UTC-window-start>
```

Örnek:

```text
hourly_health_check:2026-10-01T10:00:00Z
```

`window_key` unique olmalıdır. Aynı tick yeniden gelirse ikinci check ve ikinci
digest üretilmez.

Tick durumları:

```text
scheduled
running
succeeded
failed
skipped_duplicate
```

## 6. Event akışı

### Saatlik check

```text
schedule due
  ↓
scheduler.tick
  ↓
metrics.refresh.requested
  ↓
project_metrics updated
  ↓
metrics.refresh.completed
```

### Deadline check

```text
schedule due
  ↓
deadline.check.requested
  ↓
deadline candidates calculated
  ↓
deadline.approaching / deadline.overdue
```

### Inactivity check

```text
schedule due
  ↓
inactivity.check.requested
  ↓
days_inactive calculated
  ↓
project.inactive
```

### Morning digest

```text
08:00 schedule due
  ↓
scheduler.tick
  ↓
morning_digest.requested
  ↓
digest input snapshot created
  ↓
morning_digest.generated
```

`morning_digest.generated` yalnızca deterministic input snapshot’ın üretildiğini
ifade eder. İnsan bildirimi veya LLM özeti M1’in bu diliminde yapılmaz.

## 7. ProjectMetric projection

`project_metrics` canonical proje state’i değildir; event ve task/provider
verilerinden hesaplanan operational projection’dır.

```text
project_id
last_activity_at
days_inactive
open_task_count
blocked_task_count
awaiting_decision_count
upcoming_deadline_count
next_deadline_at
failed_ci_count?
open_pr_count?
last_sync_at?
calculated_at
source_event_watermark
version
```

M1’in local/provider-aware sürümünde GitHub alanları nullable kalır. GitHub
read-only adapter ve provider sync, local projection’ın üstünde optional
entegrasyon olarak uygulanmıştır.

Projection güncellemesi:

1. son işlenen event watermark okunur
2. yeni event’ler transaction içinde uygulanır
3. metric satırı güncellenir
4. watermark kaydedilir
5. projection event’i yazılır

Projection aynı event’i iki kez uygulamamalıdır.

## 8. Deterministic kurallar

### Inactivity

```text
days_inactive = floor(now - last_activity_at)
```

Inactivity event’i yalnızca eşik geçişinde üretilir; her tick’te tekrar edilmez.

Varsayılan eşikler:

```text
7 gün  → project.inactive.warning
14 gün → project.inactive.stale
```

### Deadline

```text
7 gün kala  → deadline.approaching
3 gün kala  → deadline.urgent
deadline geçti → deadline.overdue
```

Aynı task/deadline/window için event deduplication key kullanılır.

### Priority

M1 metric hesapları task priority’sini değiştiremez. `critical` ataması yalnız
policy veya insan kararıyla yapılır.

## 9. Catch-up ve restart

Pi veya scheduler kapalıyken kaçırılmış schedule’lar için policy açıkça
belirtilmelidir.

İlk v0.1 M1 politikası:

- hourly checks: yalnızca mevcut pencere çalıştırılır
- deadline check: mevcut pencere çalıştırılır
- inactivity check: mevcut gün için bir kez çalıştırılır
- morning digest: son başarılı digest yoksa ilk uygun çalışmada bir kez üretilir
- geçmişteki her tick tek tek replay edilmez

Restart akışı:

```text
service start
  ↓
load schedules
  ↓
reconcile running ticks
  ↓
dedupe by window_key
  ↓
run due checks
  ↓
write scheduler.health event
```

Scheduler crash sonrası `running` tick sonsuza kadar kalamaz. Lease veya
timeout ile `failed`/`retryable` duruma alınır.

## 10. Scheduler idempotency

Her scheduled action için:

```text
idempotency_key = <schedule_id>:<window_key>:<action>
```

Örnek:

```text
morning_digest:2026-10-01:generate
```

Aynı key ile ikinci kez event, job veya digest snapshot oluşturulmaz.

## 11. Failure davranışı

| Failure | Davranış |
|---|---|
| SQLite geçici busy | bounded retry |
| Scheduler process crash | running tick reconcile |
| Pi reboot | due schedule yeniden hesaplanır |
| Clock geri gider | window key tekrar çalıştırmaz |
| Clock ileri gider | catch-up policy uygulanır |
| Projection hatası | tick failed + audit event |
| Bozuk schedule | ilgili schedule disabled + alert event |
| Provider yok | local metrics devam eder, provider alanı stale kalır |
| Duplicate tick | skipped_duplicate |

Scheduler failure Core’un local state’ini bozmamalıdır.

## 12. M1 health ve observability

İlk health alanları:

```text
last_scheduler_tick_at
last_successful_tick_at
last_failed_tick_at
last_digest_at
due_schedule_count
stale_running_tick_count
projection_lag
```

CLI taslağı:

```text
arda scheduler status
arda scheduler run-once
arda metrics refresh --project <id>
arda digest preview
```

`run-once` test/diagnostic komutudur; production schedule’ını bypass ederek
ikinci bir yan etki üretmemeli ve aynı idempotency kurallarını kullanmalıdır.

## 13. Kabul kriterleri

M1 Scheduler + Event Trigger dilimi başarılı sayılırsa:

1. Due schedule deterministic biçimde tick üretmeli.
2. Aynı `window_key` ikinci kez çalışmamalı.
3. Tick ve ilgili event’ler append-only log’a yazılmalı.
4. Pi restart sonrası geçmiş tick’ler duplicate üretememeli.
5. Running tick crash sonrası sonsuza kadar running kalmamalı.
6. Deadline event’leri eşik geçişinde bir kez üretilmeli.
7. Inactivity event’leri eşik geçişinde bir kez üretilmeli.
8. Project metrics aynı event’i iki kez uygulamamalı.
9. Provider alanları yokken local metrics çalışmaya devam etmeli.
10. Digest event’i oluşturulmalı; dış bildirim gönderilmemeli.
11. Hiçbir check LLM, Hermes, shell veya GitHub write çağırmamalı.
12. Scheduler failure, local state ve event history’yi bozmamalı.

## 14. Uygulama sırası

1. Schedule/tick SQLite schema ve migration
2. Tick idempotency ve state machine
3. Scheduler clock abstraction
4. Deterministic deadline/inactivity checks
5. ProjectMetric projection ve watermark
6. Morning digest input snapshot/event
7. Restart/failure/concurrency testleri
8. `arda scheduler status` ve `run-once`

M1.5 read-only diagnostics yüzeyi bu scheduler komutlarına ek olarak
uygulanmıştır; hiçbir diagnostics komutu state düzeltmez.

GitHub read-only adapter ve provider sync uygulanmıştır. Gerçek notification
provider, worker, Hermes, Network Guardian ve LLM özeti bu sözleşmenin dışında
kalır.
