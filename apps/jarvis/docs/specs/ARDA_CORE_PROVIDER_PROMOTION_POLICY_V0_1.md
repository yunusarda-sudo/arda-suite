# ARDA Core Provider Promotion Policy v0.1

Durum: **Implemented; routing activation remains explicit**

## Amaç

Bir provider'ın belirli bir capability için hangi kanıt eşiğinde
`candidate`, `evaluated`, `validated` veya `rejected` sayılacağını tanımlar.
Bu politika provider seçmez, provider çağırmaz ve tek başına routing izni
vermez.

## Temel ayrımlar

```text
ProviderEvaluationRecord
        !=
Capability promotion decision
        !=
RouteDecision
```

`Evaluation` kanıt üretir. `PromotionDecision` bu kanıtı eşiklere göre
değerlendirir. Gerçek routing aktivasyonu ayrı ve yetkili bir mapping/policy
transition gerektirir.

## Varsayılan v0.1 eşikleri

```yaml
minimum_samples: 3
minimum_accepted_rate: 1.0
minimum_fixture_pass_rate: 1.0
maximum_tool_violation_rate: 0.0
maximum_timeout_rate: 0.0
maximum_malformed_response_rate: 0.0
maximum_latency_p95_ms: null
routing_activation_enabled: false
```

Üç örnek, capability doğrulaması için minimum başlangıç eşiğidir; production
güvencesi veya genel model üstünlüğü iddiası değildir. Latency sınırı workload
verisi oluşana kadar zorunlu değildir; sınır tanımlandığında her batch'in en
yüksek p95 değeriyle karşılaştırılır.

## Durum geçişleri

```text
matching evaluation yok
    -> CANDIDATE

1..2 matching evaluation
    -> EVALUATED

en az 3 evaluation ve tüm hard eşikler geçildi
    -> VALIDATED

metadata uyumsuzluğu veya hard eşik ihlali
    -> REJECTED
```

`VALIDATED` yalnızca capability evidence'in yeterli olduğunu gösterir.
Varsayılan `routing_eligible` değeri `false` kalır. Routing'e açmak için
ayrı bir Core authority kararı, policy snapshot'ı ve evidence hash'i gerekir.

## Değerlendirme yöntemi

- `accepted_rate` batch genelinde hesaplanır.
- `fixture_pass_rate` için en düşük gözlem kullanılır.
- tool violation, timeout ve malformed response için en yüksek gözlem kullanılır.
- latency p95 limiti varsa batch içindeki en yüksek p95 kullanılır.
- eksik zorunlu metrik başarısızlık sayılır.
- metadata eligibility başarısızsa provider Hermes capability'si için reddedilir.

Bu worst-case yaklaşım, tek bir güvenlik ihlalinin başarılı koşuların
ortalamasında gizlenmesini engeller.

## Suspend ve yeniden değerlendirme

v0.1'de otomatik suspend veya otomatik yeniden değerlendirme yoktur.

`VALIDATED` bir provider daha sonra:

- yeni model snapshot'ı,
- capability contract değişikliği,
- policy değişikliği,
- gözlenen timeout/violation,
- provenance uyuşmazlığı

nedeniyle yeniden değerlendirilmelidir. Bu olaylar mevcut mapping'i sessizce
değiştirmez; yeni immutable evaluation ve açık bir transition gerektirir.

## Uygulama sınırı

Karar fonksiyonu:

```python
arda.provider_promotion.evaluate_promotion(...)
```

salt-okunur ve yan etkisizdir. Registry'ye mapping yazmaz, provider çağırmaz,
fallback seçmez ve routing kararını değiştirmez.

Qwen3:1.7B gibi `context_window < 64000` adayları metadata eligibility
sonucu olarak aynı mevcut `ProviderEvaluationRecord` zincirinde
`accepted=false` ve gerekçe ile kaydedilebilir. Bu reddetme yalnızca
`agent.hermes_loop` capability'si içindir; modelin D4B gibi daha dar
capability'lerdeki uygunluğunu değiştirmez.

## Acceptance

- üç temiz evaluation `VALIDATED` üretir;
- iki veya daha az temiz evaluation `EVALUATED` kalır;
- tek bir contract violation `REJECTED` üretir;
- metadata uyumsuz provider `REJECTED` üretir;
- varsayılan politika hiçbir provider'ı otomatik routing'e açmaz;
- aynı evaluation identity'si tekrar yazılamaz.
