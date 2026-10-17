# Arda Core M2.1 — Worker Protocol

Durum: Protocol contract + in-memory simulation implement edildi
Sürüm: M2.1-protocol-0.1
Tarih: 2026-10-01

## Doğrulama sırası

```text
Protocol contract
  ↓
Contract tests
  ↓
In-memory simulation
  ↓
Remote adapter
  ↓
Real Mac worker
```

Bu dilim yalnız ilk üç katmanı kapsar. Gerçek socket, SSH, mTLS veya Mac
bağlantısı henüz yoktur.

## Request envelope

Her request şu alanları taşır:

```text
request_id
worker_id
message_type
payload
auth_token
signature
```

Signature, canonical request body üzerinde HMAC-SHA256 ile üretilir. Token
Core’a kalıcı olarak yazılmaz; worker registry yalnız token hash saklar.

## Supported lifecycle messages

```text
ping
claim
start
heartbeat
upload_artifact
complete
fail
```

`upload_artifact` content’i base64 taşıyabilir; Core SHA-256 hash’i yeniden
hesaplar ve yalnız doğrulanmış artifact’ı kabul eder.

## Distributed failure semantics

- invalid token veya signature request’i job’a ulaşmadan reddedilir
- `request_id` durable idempotency key olarak korunur
- aynı request tekrar gelirse aynı response döner
- aynı request id farklı payload ile gelirse replay conflict döner
- aynı request eşzamanlı işleniyorsa ikinci dispatch’e izin verilmez
- transport partition request’i teslim edilmemiş olarak bırakır
- partition sonrası aynı request id ile retry güvenli replay sağlar
- lease/fencing kararı hâlâ Core JobQueue tarafından verilir
- duplicate completion ve geç completion stale lease olarak reddedilir

## Implementation Status

Status: `IMPLEMENTED — contract + simulation`

Kod:

- `arda/worker_protocol.py`
- `tests/test_worker_protocol.py`

Bir sonraki adım remote adapter’dır. Adapter değişse bile bu protocol
contract ve failure semantics değişmemelidir.
