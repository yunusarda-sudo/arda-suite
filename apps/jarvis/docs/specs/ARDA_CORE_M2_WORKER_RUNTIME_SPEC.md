# Arda Core M2 — Worker Runtime

Durum: İlk deterministic runtime dilimi implement edildi
Sürüm: M2-runtime-0.1
Tarih: 2026-10-01

## Amaç

Pi üzerindeki Arda Core’un authenticated bir Mac worker’a LLM’siz,
deterministic bir test komutu çalıştırabilmesi.

```text
durable job
  ↓
authenticated worker
  ↓
claim + lease + fencing
  ↓
isolated workspace
  ↓
argv-only command
  ↓
test-report artifact
  ↓
SHA-256 verification
  ↓
complete
  ↓
cleanup
```

## Kapsam

Uygulanan ilk dilim:

- hash’lenmiş worker token ile authentication
- worker registration ve heartbeat
- mevcut durable queue claim/start/heartbeat/fencing akışı
- geçici, worker-scoped workspace
- shell açmadan argv listesiyle deterministic subprocess
- stdout/stderr/exit code içeren `test-report.json`
- Core tarafında artifact hash doğrulaması
- artifact upload’dan sonra job completion
- completion’dan sonra cleanup
- setup, timeout ve cleanup failure audit’i

Kapsam dışı:

- Hermes, Ollama veya coding agent
- browser ve GitHub write
- remote HTTP listener/protocol deployment
- network isolation policy enforcement
- secret forwarding
- reviewer pipeline

## Worker authentication

Worker token yalnızca SHA-256 hash olarak `workers.auth_token_hash` alanında
tutulur. Token değerleri event, log veya artifact payload’ına yazılmaz.
Revoked worker authenticate veya heartbeat edemez.

## Failure sözleşmesi

| Failure | Beklenen davranış |
|---|---|
| yanlış worker token | job claim edilmez |
| worker crash / lease expiry | Core mevcut queue recovery ile lease’i fence eder |
| command timeout | report upload edilir, job `failed` olur, cleanup denenir |
| setup failure | job `running` kalmaz, terminal `failed` olur |
| artifact hash mismatch | completion reddedilir |
| stale fencing token | geç sonuç reddedilir |
| cleanup failure | job sonucu değişmez, `worker.cleanup.failed` audit event’i yazılır |
| duplicate completion | Core `StaleLeaseError` döndürür |

## Kabul kriterleri

1. Authenticated worker fixture test job’ını claim edebilmelidir.
2. Job ayrı workspace’te çalışmalıdır.
3. Command shell olmadan çalıştırılmalıdır.
4. Test report artifact’ı SHA-256 ile doğrulanmalıdır.
5. Verified artifact olmadan job `succeeded` olmamalıdır.
6. Cleanup completion’dan sonra çalışmalıdır.
7. Timeout/setup/cleanup failure state’i ve audit’i korumalıdır.
8. Full isolated test suite geçmelidir.

## Implementation Status

Status: `IMPLEMENTED — initial deterministic slice`

Commit: `91ce16b feat(core): add M2 deterministic worker runtime`

Kod:

- `arda/worker_runtime.py`
- `arda/core.py` migration v3
- `tests/test_worker_runtime.py`

Bir sonraki M2 dilimi remote worker transport/authenticated protocol’dür; bu
ilk dilim gerçek Mac networking veya LLM execution içermez.
