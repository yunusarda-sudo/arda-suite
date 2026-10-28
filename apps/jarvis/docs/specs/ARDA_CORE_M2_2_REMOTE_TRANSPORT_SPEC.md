# Arda Core M2.2 — Remote Transport Adapter

Durum: Local TCP/HTTP integration implement edildi; gerçek Pi↔Mac deployment
henüz doğrulanmadı
Sürüm: M2.2-transport-0.1
Tarih: 2026-10-01

## Sınır

Transport yalnızca şu işleri yapar:

```text
serialize → send → receive → authenticate → deserialize
```

Lease, fencing, retry semantics, replay ve job state kararları
`WorkerProtocolServer`/`JobQueue` içinde kalır.

## Uygulanan transport

- stdlib HTTP POST adapter
- `/worker/protocol` endpoint’i
- varsayılan bind: `127.0.0.1`
- `0.0.0.0` ve `::` bind’i reddedilir
- request ve response HMAC-SHA256 doğrulaması
- worker-specific token authentication
- bounded request/response/artifact boyutları
- bounded network retry
- retry aynı `request_id` ile yapılır
- server her request için ayrı SQLite bağlantısı açar

HTTP endpoint private LAN veya Tailscale interface üzerinde kullanılabilir.
Loopback dışı cleartext HTTP yalnız explicit `allow_private_http` opt-in ile
ve private/Tailscale interface için açılır. Public internet bind’i bu dilimin
varsayılanı veya kabul kriteri değildir.
HTTPS/mTLS deployment adapter configuration aşamasında ayrıca doğrulanmalıdır.

## Verified local scenarios

- localhost TCP ping
- claim → start → artifact upload → complete
- response kaybı sonrası aynı request replay’i
- HTTP server restart sonrası durable replay
- yanlış token → `401`
- request/artifact size limit → `413`
- Core job state ve fencing kararlarının transport dışında kalması

## Protocol contract

İmza canonicalization sözleşmesi sabittir: envelope JSON UTF-8 olarak encode
edilir; object key'leri lexicographic sırada, separator'lar kompakt
(`,` ve `:`), Unicode escape kullanılmadan serialize edilir. HTTP parser duplicate
object key'lerini kabul etmemelidir. Protocol payload'larında belirsiz float,
NaN/Infinity veya farklı timestamp/number temsilleri kullanılmaması çağıran
taraf için sözleşme kuralıdır; mevcut parser/canonicalizer sayısal kısıtları
zorlamaz. Bu kısıtların enforcement ve regression testleri ayrı bir gate
olarak tamamlanmalıdır. Yeni alanlar sözleşmeye göre contract test ile eklenir.

Her worker ayrı bir authentication secret/token kullanır. Secret yalnız güvenli
provisioning/runtime environment üzerinden alınır; event, response, artifact,
test çıktısı ve loglara yazılmaz. Worker revoke edildiğinde yalnız o worker'ın
credential'ı geçersiz olur. Rotation ve credential key/version metadata'sı
physical deployment öncesi açıkça tanımlanmalı ve test edilmelidir; tek global
master secret kabul edilmez.

Replay retention henüz M2.2 transport kapsamının tamamlanmış bir operasyonel
özelliği değildir. Gelecek GC yalnız `worker-protocol:*`, tamamlanmış ve retention
penceresi dışındaki kayıtları silebilir; `result IS NULL` in-flight kayıtlar
korunmalıdır. GC request lifecycle ile aynı idempotency kararını yeniden
uygulamamalıdır.

Physical validation harness’i `arda.worker_transport_cli` içindedir. `serve`
yalnız endpoint’i açar; `smoke` yalnız sabit fixture job’ını claim edip küçük
bir transport artifact’ı upload eder, arbitrary command çalıştırmaz. Fixture
DB ve `run_id` açıkça verilmeden probe yapılmaz.

## Kapsam dışı

- gerçek Raspberry Pi ↔ Mac donanım bağlantısı
- Tailscale kurulumu veya network policy değişikliği
- SSH bootstrap
- TLS certificate/mTLS provisioning
- chunked/resumable artifact upload
- coding agent, Hermes, browser, GitHub write

## Implementation Status

Status: `IMPLEMENTED — localhost integration`

Kod:

- `arda/worker_http.py`
- `tests/test_worker_http.py`

Sonraki doğrulama gerçek private-network Pi↔Mac testidir; bu testte protocol
contract değişmemelidir.
