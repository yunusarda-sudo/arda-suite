# Arda Hermes Execution Budget v0.1

Durum: **Core-side budget implemented; natural Nous Hermes loop acceptance pending**

Bu slice, Nous Hermes'in MCP üzerinden sınırsız tool loop'u oluşturmasını
engelleyen transport-level fail-closed sınırını tanımlar. Budget, Core
authority'nin yerine geçmez ve provider seçimi yapmaz.

## Varsayılan sınırlar

```yaml
max_tool_calls: 8
max_duration_seconds: 180
```

Sınırlar `arda.mcp_stdio` üzerinden değiştirilebilir; production profilleri
daha gevşek değerleri ayrıca yetkilendirmeden kullanmamalıdır.

## Davranış

- Her `tools/call` öncesi budget reserve edilir.
- Limit aşılırsa handler çağrılmaz.
- Yanıt `isError: true` ile fail-closed döner.
- Reddedilen çağrı `MCPAuditRecord` olarak kaydedilir.
- `DENY`, `REQUIRE_APPROVAL`, provider timeout ve verification kararları
  budget tarafından değiştirilmez.
- Tek bir handler'ın içindeki uzun-running inference preempt edilmez; bunun
  için provider request timeout'u gereklidir.

## Kabul edilen testler

- `max_tool_calls=1`: ilk çağrı başarılı, ikinci çağrı rejected.
- Süresi geçmiş budget: ilk çağrı handler'a girmeden rejected.
- Reddedilen çağrı audit'te `outcome=rejected` olarak görünür.

Bu mekanizma doğal Nous Hermes loop'unun başarılı olduğunu kanıtlamaz; yalnızca
loop sınırını Core tarafında güvenli ve ölçülebilir hale getirir.
