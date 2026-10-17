# Arda Core v0.1 Specification

Durum: Tasarım sözleşmesi — implementasyon kapsamı için başlangıç noktası
Sürüm: 0.1
Tarih: 2026-09-30

## 1. Amaç

Arda Core, Raspberry Pi üzerinde 7/24 çalışan kişisel proje işletim sistemidir.
Görevi ağır hesaplama yapmak değil; proje bağlamını, görevleri, kararları,
zamanlamayı, izinleri ve harici worker işlerini güvenilir biçimde yönetmektir.

Arda Core şu yetenekleri sağlar:

- Proje ve second-brain state’ini kalıcı tutma
- Deadline, inactivity ve recurring job takibi
- Sabah özeti ve anlamlı değişiklik bildirimleri
- Durable job queue ve worker koordinasyonu
- Tool Gateway üzerinden kontrollü harici servis erişimi
- İnsan onayı ve audit trail
- Mac, CI ve cloud worker’lara iş dağıtımı
- Hermes veya başka bir reasoning motorunu adapter olarak kullanma

## 2. Kapsam dışı

v0.1 aşağıdakileri garanti etmez:

- Pi üzerinde ağır LLM inference
- Pi üzerinde browser automation veya büyük build
- Otonom production deploy
- Otomatik PR merge
- Agent’a doğrudan secret, shell veya production erişimi
- Hermes’e özel veri modeline bağımlılık
- Her memory kaydının otomatik ve kalıcı yazılması
- Network Guardian, DNS monitoring, CrowdSec veya device discovery

## 2.1 Implementation Status

### M0 — Reliable Kernel

Status: `COMPLETE`

Snapshot:

```text
commit: cd9d945
tag:    arda-core-m0-reliable-kernel
```

Validated behavior:

- SQLite schema and idempotent migrations
- append-only event log
- durable job queue
- lease expiry and fencing token protection
- idempotency and payload conflict detection
- approval lifecycle, snapshot hash and expiry
- artifact SHA-256 verification
- concurrent job claim safety
- concurrent idempotency safety
- concurrent approval consume safety
- transaction rollback
- restart/recovery reconciliation
- policy levels: `auto`, `confirm`, `human-only`, `deny`

Implemented code:

- `arda/core.py`
- `tests/test_core.py`

Known limitations:

- scheduler yok
- external tool adapter’ları yok
- worker execution yok
- orchestrator/Hermes adapter yok
- GitHub write yok
- Network Guardian yok
- learning/reflection engine yok

M0 kapsamı Arda Core’un güvenilir state ve policy kernel’idir; bu bölümdeki
olmayan yetenekler tasarım hedefi olarak kalır ve implementation status olarak
yorumlanmamalıdır.

### M2 — Worker Runtime (initial deterministic slice)

Status: `IMPLEMENTED`

Validated:

- authenticated worker registration ve heartbeat
- token hash persistence
- isolated workspace
- argv-only deterministic command
- verified test-report artifact
- completion sonrası cleanup
- timeout, setup failure, cleanup failure ve duplicate completion davranışları

M2’nin remote transport, network isolation, coding agent, reviewer ve Hermes
katmanları henüz uygulanmamıştır.

### M2.1 — Worker Protocol Contract + Simulation

Status: `IMPLEMENTED`

Protocol envelope, HMAC request signature, durable request-id replay protection
ve in-memory transport simulation doğrulanmıştır. Gerçek remote adapter, SSH,
mTLS ve Mac worker bağlantısı sonraki dilimdir.

### M2.2 — Remote Transport Adapter

Status: `IMPLEMENTED — localhost integration`

Bounded HTTP transport, signed request/response, worker-specific authentication,
retry/replay, size limits ve server restart replay’i localhost TCP üzerinde
doğrulanmıştır. Gerçek Pi↔Mac private-network deploymentı henüz doğrulanmamış;
transport Core kararlarını veya protocol semantics’i üstlenmez.

## 3. Sistem sınırları

```text
User
  ↓
Arda Core (Pi)
  ├── Markdown Brain + Git
  ├── SQLite metadata/state
  ├── Scheduler
  ├── Durable Queue
  ├── Policy/Approval Engine
  ├── Tool Gateway
  └── Orchestrator Adapter (Hermes/LLM)
        ↓
      Mac / CI / Cloud workers
```

### 3.1 Pi

Pi, owned state ve koordinasyonun kaynağıdır. Scheduler, queue, SQLite,
Markdown brain, FTS, monitoring ve notification burada çalışır.

### 3.2 Mac worker

Mac worker coding, test, build, browser ve local Ollama işlerini yürütür.
Her job ayrı workspace ile başlar. Worker varsayılan olarak secret almaz.

### 3.3 Cloud/CI worker

GitHub Actions veya disposable cloud worker deterministic test, build ve
araştırma gibi Pi için ağır işleri yürütür.

### 3.4 Orchestrator

Hermes, Arda Core’un sahibi değildir. Yalnızca requirement decomposition,
planlama, özetleme ve tool/job önerisi yapan değiştirilebilir bir adapter’dır.
Policy kararları Arda Core tarafından verilir.

## 4. Source of truth

Bir bilgi yalnızca bir canonical kaynağa sahip olmalıdır.

| Veri | Canonical kaynak | Kopyalar |
|---|---|---|
| Proje tanımı | `brain/projects/<id>/README.md` | SQLite index |
| İnsan anlamlı proje state’i | `brain/projects/<id>/state.md` | SQLite index |
| Operasyonel proje sinyalleri | SQLite `project_metrics` | Digest projection |
| Mimari karar | `brain/projects/<id>/decisions/` veya `brain/decisions/` | SQLite index |
| İnsan notu/lesson | Markdown brain | FTS index |
| Task state’i | SQLite `tasks` tablosu | Markdown görünümü opsiyonel |
| Job state’i | SQLite `jobs` tablosu | Audit event log |
| Approval state’i | SQLite `approvals` tablosu | Immutable audit event |
| Worker state’i | SQLite `workers` tablosu | Heartbeat event |
| GitHub durumu | Provider snapshot + `sync_state` | Project event |
| Secret | External secret store/keychain | Arda state’ine yazılmaz |

Markdown ve SQLite çelişirse veri türünün canonical kaynağı uygulanır; sessiz
otomatik birleştirme yapılmaz. Çelişki `reconciliation_required` event’i üretir.

`state.md` yalnızca insan tarafından anlamlandırılmış proje durumunu taşır:
hedef, blocker, sıradaki işler, bekleyen kararlar ve önemli bağlam. Scheduler’ın
sık değiştirdiği `last_github_activity`, `days_inactive`, `open_pr_count`,
`failed_ci_count`, `next_deadline`, `last_worker_run` ve `last_sync` gibi
operasyonel değerler SQLite’ta tutulur; her sync’te Markdown değiştirilmez.

## 5. Brain dosya yapısı

```text
brain/
├── user/
│   ├── profile.md
│   ├── preferences.md
│   └── context.md
├── projects/
│   └── <project-id>/
│       ├── README.md
│       ├── state.md
│       ├── roadmap.md
│       ├── tasks/
│       ├── decisions/
│       ├── research/
│       ├── contacts/
│       └── lessons/
├── decisions/
├── skills/
├── opportunities/
├── inbox/
└── memory/
```

`state.md` önerilen insan-okur yapısı:

```text
# Current state
## Current objective
## Current blockers
## Next three actions
## Pending decisions
## Important context
```

Her kalıcı Markdown kaydı mümkün olduğunda şu metadata’yı taşımalıdır:

```yaml
source: user|worker|github|calendar|research|system
source_ref: "event-... veya URL"
captured_at: "2026-09-30T08:00:00+03:00"
fresh_until: "2026-10-07T08:00:00+03:00"
confidence: high|medium|low
approved_by: user|policy|none
```

## 6. Veri modelleri

Mutable entity’ler en az `id`, `created_at`, `updated_at` ve monoton artan
integer `version` alanlarını taşır. `version`, optimistic locking için
kullanılabilir. Dış provider kimlikleri ayrıca `source` ve `source_id` olarak
korunur.

Event immutable olduğu için `updated_at` ve entity `version` taşımaz; kendi
şemasında `event_id`, `event_schema_version` ve `created_at` bulunur.

### Project

```text
id, name, description, status, priority, owner
last_meaningful_activity_at, next_review_at
brain_path, external_refs, created_at, updated_at
```

`status`: `active | paused | blocked | completed | archived`.

### Task

```text
id, project_id, title, description, status, priority
due_at, blocked_reason, source_event_id, assignee
requires_approval, created_at, updated_at, completed_at
```

`status`: `backlog | ready | in_progress | blocked | awaiting_decision | done | cancelled`.

`priority`: `low | normal | high | critical`. Model tek başına `critical`
seviyesi atayamaz; bunun için policy veya insan onayı gerekir.

### Decision

```text
id, project_id, adr_path, status, decision_date
supersedes, created_at, updated_at, version
```

`status`: `proposed | accepted | rejected | superseded`.

Decision’ın asıl içeriği — question, options, selected option, rationale ve
source references — Markdown ADR dosyasının canonical içeriğidir. SQLite
`Decision` kaydı yalnızca index/projection alanlarını taşır; karar metni ikinci
kez saklanmaz.

### Event

Event log append-only’dir; yazılmış event değiştirilmez.

```text
event_id, type, project_id, source, source_id
event_schema_version, observed_at, payload, correlation_id, created_at
```

Örnek event türleri: `github.pr.updated`, `job.completed`,
`github.ci.failed`, `job.retry_scheduled`, `approval.requested`,
`approval.approved`, `worker.unavailable`, `morning_digest.generated`,
`reconciliation_required`.

`observed_at`, olayın kaynakta gerçekleştiği veya gözlendiği zamandır;
`created_at`, Arda Core’un event’i kendi log’una yazdığı zamandır.

### ProjectMetric

Scheduler tarafından üretilen operational projection’dır; canonical Markdown
state’i değildir.

```text
project_id, last_github_activity_at, days_inactive
open_pr_count, failed_ci_count, next_deadline
last_worker_run_at, last_sync_at, calculated_at
```

`health_score` v0.1’de kullanılmaz. İleride eklenirse yalnızca versioned ve
deterministic bir formülle hesaplanır. LLM değerlendirmesi ayrı bir
`project_assessment` kaydıdır ve ProjectMetric’e yazılamaz.

### Job

```text
id, project_id, type, objective, acceptance_criteria
status, priority
available_at, leased_by, lease_token, lease_expires_at
attempt_count, max_attempts, timeout_seconds
idempotency_key, context_manifest, policy_snapshot
input_hash, approval_id, result, error
created_at, started_at, finished_at, updated_at, version
```

`status`: `queued | leased | running | awaiting_approval | retry_wait | blocked | failed | succeeded | cancelled`.

`type`: `summary | research | coding | review | test | sync | notification`.

`leased_by`, job’ın o anki lease sahibi worker’ı belirtir. `lease_token`, eski
worker’ın lease kaybından sonra sonuç yazmasını engelleyen fencing token’dır.

### Worker

```text
id, public_key, capabilities, trust_level
status, last_seen_at, registered_at, metadata
```

`status`: `registered | healthy | unavailable | revoked`.

### Approval

```text
id, action_type, subject_id, requested_by
payload_hash, payload_snapshot, status
expires_at, decided_by, decided_at, execution_id
```

`status`: `requested | pending | approved | rejected | expired | executed`.

Approval, onaylanan payload’ın hash’ini ve snapshot’ını taşımalıdır. Sonradan
değişen bir payload eski approval ile çalıştırılamaz.

### Artifact

```text
id, job_id, kind, path_or_ref, sha256, created_at, retention_until
```

Artifact örnekleri: test report, review report, diff summary, PR URL, research
source listesi.

### Task ve Job ayrımı

```text
Task = kullanıcının/projenin yapılması gereken uzun ömürlü işi
Job  = Task’ı gerçekleştirmek için worker’a verilen geçici execution unit
```

Örneğin bir `Sample Project SMTP blocker` Task’ı altında research, coding, test ve
review Job’ları oluşturulabilir. Task domain state’ini; Job ise execution
state’ini temsil eder.

## 7. State machine’ler

### Task

```text
backlog → ready → in_progress → done
                    ├→ blocked
                    ├→ awaiting_decision
                    └→ cancelled
```

### Job

```text
queued → leased → running → succeeded
                   ├→ awaiting_approval
                   ├→ blocked
                   ├→ failed → retry_wait → queued
                   └→ cancelled

leased → lease expired → queued
running → heartbeat lost / lease expired → retry_wait → queued
```

Worker `claim_job()` ile süreli lease alır; `GET job` ile sahipsiz iş çekemez.
Heartbeat lease’i uzatır. Worker lease’i kaybettikten sonra eski worker’ın
`complete`, `fail`, `progress` veya `heartbeat` çağrıları kabul edilmez.
Retry yalnızca `attempt_count < max_attempts`, idempotency ve cleanup koşulları
sağlanıyorsa yapılır.

### Approval

```text
requested → pending → approved → executed
                    ├→ rejected
                    └→ expired
```

`approved` durumundaki approval tek kullanımlıktır. Payload hash eşleşmezse
execution reddedilir.

### Worker

```text
registered → healthy ⇄ unavailable
                 ↓
              revoked
```

Heartbeat timeout worker’ı `unavailable` yapar; job otomatik olarak başka
worker’a taşınmadan önce mevcut execution lease’i sona ermelidir.

## 8. Idempotency ve retry

Her dış yan etkili işlem idempotency key taşır:

```text
<job-id>:<operation>:<version>
job-2026-001:create-pr:v1
```

Gateway aynı key için daha önce başarılı sonuç varsa işlemi tekrarlamaz ve
önceki sonucu döndürür.

Zorunlu işlemler: issue/PR oluşturma, notification gönderme, memory write,
task oluşturma ve worker dispatch.

`attempt_count` yalnızca gerçek bir execution attempt’ında artar. `attempts` ve
`attempt_count` eşzamanlı kullanılmaz.

`input_hash`; objective, acceptance criteria, resolve edilmiş context manifest,
policy snapshot ve ilgili artifact version’larından üretilir. Secret değerleri
hash input’una doğrudan dahil edilmez. Böylece çalıştırılan işin onaylanan iş
ile aynı olduğu doğrulanabilir.

Retry kuralları:

- network timeout: sınırlı exponential backoff
- provider 4xx: otomatik retry yok; blocked/approval
- provider 5xx: sınırlı retry
- worker crash: lease expiration sonrası retry
- unknown side effect: otomatik retry yok; reconciliation gerekir
- create PR gibi dış yazma: idempotency olmadan çalıştırılamaz

## 9. Scheduler ve monitoring

Scheduler LLM tabanlı değildir. Cron, deadline, inactivity, timeout, retry,
polling, backup ve digest tetikleme deterministic olarak çalışır; LLM yalnızca
sonuçları özetleyebilir veya öneri üretebilir.

Scheduler şu kontrolleri destekler:

- deadline yaklaşması
- proje inactivity
- recurring review
- GitHub issue/PR/CI sync
- worker heartbeat
- stale approval
- failed job reconciliation
- morning digest

Varsayılan davranış:

- aynı uyarıyı her çalışmada tekrar gönderme
- yalnızca state değişiminde veya escalation eşiğinde bildirim gönder
- provider başarısızlığını gerçek proje blocker’ı ile karıştırma
- son başarılı sync zamanını her raporda belirt

## 10. Durable queue ve recovery

Queue yalnızca RAM’de tutulamaz. SQLite transaction ile:

1. job oluşturulur
2. queue kaydı yazılır
3. event kaydı yazılır
4. commit tamamlanır

Pi yeniden başladığında:

- `queued` job’lar yeniden planlanır
- lease’i süresi dolmuş `leased/running` job’lar reconcile edilir
- `succeeded` job’lar yeniden çalıştırılmaz
- belirsiz dış yan etki job’ları insan incelemesine alınır

## 11. Tool Gateway

Her tool ortak bir execution sözleşmesine uyar:

```text
execute(actor, project_id, arguments, idempotency_key, approval_id=None)
```

Orchestrator doğrudan HTTP, shell, database veya secret erişimi alamaz.
Her tool için:

```text
tool_name
input_schema
output_schema
required_scope
risk_level
network_policy
approval_policy
rate_limit
audit_fields
```

Örnek politika:

| Tool | Risk | İlk v0.1 politikası |
|---|---|---|
| `github.list_issues` | düşük | auto |
| `github.get_pr_status` | düşük | auto |
| `github.read_issue` | düşük | auto |
| `github.read_ci` | düşük | auto |
| `worker.prepare_job` | düşük | auto |
| `worker.execute_isolated_tests` | orta | auto |
| `memory.write` | orta | confirm |
| `skill.write` | orta | confirm |
| `package.install` | yüksek | confirm |
| `github.create_issue` | orta | confirm |
| `github.create_pr` | yüksek | confirm |
| `github.push` | yüksek | confirm |
| `network.request` | yüksek | confirm |
| `secret.request` | kritik | human-only |
| `github.merge_pr` | kritik | human-only |
| `production.deploy` | kritik | human-only |
| `production.db_write` | kritik | human-only |
| `sudo` | kritik | deny |
| `policy.modify` | kritik | deny |
| `private_key.read` | kritik | deny |
| `llm.summarize` | orta | confirm/provider policy |
| `calendar.read` | orta | auto/allowlist |
| `calendar.create` | yüksek | confirm |
| `worker.submit` | yüksek | confirm |
| `worker.cancel` | orta | confirm |
| `shell` | yüksek | deny by default |
| `secret.read` | kritik | agent’a kapalı |

Tool sonucu secret içermemeli. Gateway logları credential değerlerini değil,
yalnızca tool adı, scope, actor, hash, sonuç ve hata sınıfını tutar.

### 11.1 Network Guardian sınırı (M5+ source adapter)

Network güvenliği Arda Core’un içine gömülmez; ayrı provider adapter’ları ile
event source olarak bağlanır:

```text
Network Guardian
├── AdGuard Home adapter
├── CrowdSec adapter
├── Suricata adapter
├── ntopng adapter
├── device inventory
└── security event normalizer
        ↓
     Arda Core event log
```

Arda Core’un tanıyacağı normalized event’ler:

```text
security.device.new
security.ssh.bruteforce
security.ip.blocked
security.dns.blocked
security.dns.anomaly
security.ids.alert
security.traffic.anomaly
security.guardian.unhealthy
```

Normalized security event envelope’i provider ayrıntısını Core’dan izole eder:

```json
{
  "type": "security.dns.blocked",
  "source": "network_guardian",
  "severity": "info|warning|critical",
  "confidence": 0.0,
  "recommended_action": null
}
```

AdGuard Home veya CrowdSec seçimi Core veri modelini değiştirmemelidir.
Network Guardian M0/M1/M2 kapsamı dışındadır ve M5+ bağımsız source adapter
olarak planlanır. İlk güvenlik akışı `detect → report → approve → block`
olmalıdır; onaysız otomatik firewall değişikliği yapılmaz.

Pi gözlemci ve koordinatör olarak kalır; router/firewall bağımlılığı Arda
Core’un erişilebilirliğini internetin tek hata noktası haline getirmemelidir.

#### M5 precondition: router capability audit

Bu önkoşul yalnızca Network Guardian kapsamını etkiler; M2 Worker Runtime ve
M3 Hermes Adapter’ı bloklamaz. M5 başlamadan önce router capability audit şu
başlıkları doğrulamalıdır:

- DHCP üzerinden custom DNS dağıtımı
- IPv4 DNS override / zorunlu DNS yönlendirmesi
- IPv6 DNS ve Router Advertisement kontrolü
- DoH/DoT bypass kontrolleri
- port mirroring / SPAN desteği
- NetFlow, sFlow veya IPFIX export desteği

Desteklenmeyen yetenekler Network Guardian planında açıkça `unsupported` veya
`requires_additional_hardware` olarak işaretlenir; Core yol haritası bunun için
geriye çekilmez.

## 12. Worker protocol

Worker kayıt mesajı:

```json
{
  "worker_id": "mac-primary",
  "public_key": "<key-id>",
  "capabilities": ["ollama", "coding", "pytest", "browser"],
  "trust_level": "sandboxed",
  "protocol_version": "0.1"
}
```

Job mesajı:

```json
{
  "job_id": "job-2026-001",
  "project_id": "sample-project",
  "type": "coding",
  "objective": "SMTP blocker'ını çöz",
  "acceptance_criteria": ["testler geçmeli", "secret koda yazılmamalı"],
  "context_refs": ["projects/sample-project/state.md"],
  "idempotency_key": "job-2026-001:execute:v1",
  "workspace_policy": {
    "isolated": true,
    "network": "restricted",
    "secrets": "none"
  },
  "signature": "<detached-signature>"
}
```

`claim_job()` başarılı olduğunda Core worker’a `leased_by`, `lease_token` ve
`lease_expires_at` verir. Worker aşağıdaki çağrıların tümünde güncel
`lease_token` göndermek zorundadır:

```text
heartbeat(job_id, lease_token)
report_progress(job_id, lease_token, payload)
complete(job_id, lease_token, result)
fail(job_id, lease_token, error)
```

Lease expire olduktan veya job yeni bir worker’a verildikten sonra eski token
fenced olur. Eski worker’ın geç gelen sonucu reddedilir; yalnızca event log’una
`stale_worker_result_rejected` olayı yazılabilir.

Worker job lifecycle:

```text
register → heartbeat → claim → acknowledge → execute → report_progress
→ upload artifacts → verify hashes → complete → cleanup
```

`complete` yalnızca gerekli artifact’lar upload edilip SHA-256 hash’leri Core
tarafından doğrulandıktan sonra çağrılabilir. Böylece `succeeded`, artifact
transferinin de tamamlandığını ifade eder. Worker raporu job id, status,
commit/branch, test summary, artifact hash’leri, error class ve cleanup
sonucunu içermelidir.

Context path’leri worker’a kör şekilde verilmez. Arda Core referansları resolve
eder, policy filter uygular ve yalnızca imzalı/manifest’li context package’ını
gönderir. Böylece worker Pi dosya ağacında serbestçe dolaşamaz.

Pi ile Mac arasındaki bağlantı için v0.1 adayları Tailscale + SSH veya mTLS’tir.
JSON tek başına kimlik doğrulama sağlamaz.

## 13. Policy ve approval

Policy seviyeleri:

- `auto`: salt-okuma ve düşük riskli deterministic işlemler
- `confirm`: kullanıcı etkileşimiyle çalışabilecek orta riskli işlemler
- `human-only`: yalnızca açık insan kararıyla dış yan etki
- `deny`: sistem tarafından hiçbir koşulda çalıştırılmayan işlemler

Approval ekranı/mesajı şu bilgileri göstermelidir:

- yapılacak işlem
- hedef proje/repo
- değişecek kaynak
- payload özeti
- payload hash’i
- risk seviyesi
- expiry zamanı
- beklenen yan etki

Genel “onaylıyorum” durumu kabul edilmez; approval spesifik action ve snapshot’a
bağlıdır.

## 14. Orchestrator adapter

Adapter arayüzü reasoning motorunu Arda Core’dan ayırır:

```text
plan(request, context) -> ProposedPlan
summarize(context) -> Summary
classify(event) -> Classification
```

İsteğe bağlı operasyonlar:

```text
propose_tools(plan) -> ProposedToolCalls
propose_jobs(plan) -> ProposedJobs
review_result(job_result) -> ReviewSummary
```

Adapter tool çalıştırmaz. Yalnızca öneri üretir. Arda Core:

1. öneriyi schema ile doğrular
2. policy uygular
3. approval gerekiyorsa bekler
4. Tool Gateway veya Queue üzerinden çalıştırır
5. sonucu audit eder

Hermes, OpenClaw, hosted LLM veya Ollama adapter olarak değiştirilebilir.

## 15. Bildirimler

Bildirim seviyeleri:

- `digest`: sabah/akşam özetine eklenir
- `notice`: anlamlı state değişiminde gönderilir
- `urgent`: deadline, security veya worker failure escalation
- `approval`: insan kararı bekleniyor

Bildirim deduplication anahtarı:

```text
<project-id>:<event-type>:<subject-id>:<state-version>
```

## 16. Backup ve disaster recovery

Minimum hedef:

- Markdown brain private Git remote’a düzenli push/snapshot
- SQLite düzenli snapshot ve restore testi
- config versioned, secret’lardan ayrı
- secrets ayrı backup policy ile korunur
- Pi microSD kaybında yeni host’a restore runbook’u bulunur

Backup başarı sayılmaz; periyodik restore testi yapılmadan recovery doğrulanmış
kabul edilmez.

MicroSD source of truth değildir. Yeni Pi’ye clone/restore, config reconnect ve
service health doğrulamasını içeren bir recovery runbook’u tutulmalıdır.

## 16.1 Memory provenance

Her kalıcı memory kaydı şunları taşır:

```text
content, source, source_ref, created_at, created_by
confidence, approval_id, supersedes
```

Kullanıcı/kanıta dayalı gerçek ile LLM inference ayrıdır. `source=user` veya
`source=project_decision` kanıta dayalı kayıt; `source=model_inference` ise ayrı
provenance ve confidence ile tutulan bir öneridir.

## 17. Observability

Her job ve tool call için:

- correlation id
- actor (`user`, `orchestrator`, `scheduler`, `worker`)
- project/job/tool id
- start/end zamanı
- karar ve policy sonucu
- attempt numarası
- çıktı özeti
- hata sınıfı
- artifact referansları

Loglarda secret, token, private key, full credential veya gereksiz kişisel veri
tutulmaz.

İlk health göstergeleri:

- Pi service uptime
- queue depth
- oldest queued job
- failed jobs
- stale approvals
- worker last seen
- last successful GitHub sync
- last successful backup
- disk usage

## 18. Minimal API ve CLI

API public internete bind edilmez; başlangıçta `127.0.0.1` veya authenticated
VPN/Tailscale interface kullanılır.

```text
GET  /health
GET  /projects
GET  /projects/{id}
GET  /tasks
POST /tasks
GET  /jobs
POST /jobs
POST /jobs/{id}/claim
POST /jobs/{id}/heartbeat
POST /jobs/{id}/complete
GET  /workers
POST /workers/heartbeat
GET  /approvals
POST /approvals/{id}/approve
POST /approvals/{id}/reject
GET  /events
```

İlk sürümde dashboard yerine şu CLI yüzeyi yeterlidir:

```text
arda status
arda projects
arda jobs
arda workers
arda approvals
arda events
arda doctor
```

## 19. Test stratejisi

Hermes kurulmadan `FakeOrchestrator` ile queue, worker, policy, approval, state
ve recovery test edilmelidir. İlk uçtan uca senaryo:

```text
"Sample Project için coding job oluştur"
→ FakeOrchestrator
→ policy
→ approval
→ durable queue
→ fake worker lease
→ artifact
→ complete
```

Zorunlu recovery testleri: Pi reboot, worker crash, lease expiry, duplicate
create-PR, GitHub unavailable, LLM unavailable ve invalid approval snapshot.

## 20. Kabul kriterleri

v0.1 aşağıdakileri karşılamalıdır:

1. Pi yeniden başladığında queued job kaybolmamalı.
2. Aynı idempotency key dış yan etkiyi iki kez üretmemeli.
3. Approval payload değişirse execution reddedilmeli.
4. Hermes/LLM yokken state ve scheduler çalışmaya devam etmeli.
5. Agent secret değerini hiçbir tool sonucunda görememeli.
6. Worker kimliği doğrulanmadan job claim edilememeli.
7. Coder ve Reviewer aynı writable workspace’i kullanmamalı.
8. Git push ve PR oluşturma approval gerektirmeli; PR merge ve production
   işlemleri human-only kalmalı.
9. Backup restore testi başarılı olmadan sistem healthy sayılmamalı.
10. Her job sonuca, hataya veya açıkça blocked durumuna ulaşmalı.

Ek recovery kriterleri:

11. Worker heartbeat kesildiğinde lease expire olmalı ve job sonsuza kadar
    `running` kalmamalı.
12. Scheduler operasyonel metrikleri güncellerken `state.md` dosyasını
    değiştirmemeli.
13. GitHub veya LLM erişilemezken local state ve deterministic scheduler
    çalışmaya devam etmeli.
14. Aynı `create-pr` isteği iki kez gelse de tek PR sonucu korunmalı.
15. Context manifest’i dışında Pi dosyaları worker’dan erişilememeli.

## 21. Freeze sonrası v0.1 uygulama kapsamı

Spec freeze edildikten sonra ilk implementasyon dalı yalnızca şu dört temel
katmanla sınırlıdır:

1. SQLite schema ve migrations
2. Append-only Event Log ve correlation id
3. Durable Queue, lease, fencing token ve idempotency
4. Policy + Approval Engine

Bu çekirdek Pi reboot, worker crash, duplicate side effect, lease expiry ve
approval snapshot testlerini geçmeden Hermes, GitHub write, gerçek coding
agent veya Network Guardian eklenmez.

## 22. Sonraki uygulama sırası

1. Scheduler ve morning digest
2. Read-only diagnostics yüzeyi
3. Tool Gateway’in read-only GitHub adapter’ı
4. Worker registration/heartbeat/job protocol
5. Mac worker’ın tek bir coding job’ı
6. Reviewer ve artifact flow
7. Backup/restore
8. Hermes adapter
9. Network Guardian source adapter’ları (M5+)
6. Backup/restore
7. Hermes adapter
8. Network Guardian source adapter’ları (M5+)

Hermes entegrasyonu en sona bırakılır. Böylece Arda Core’un veri modeli ve
güvenlik sınırları tek bir orchestrator’ın davranışına göre şekillenmez.
