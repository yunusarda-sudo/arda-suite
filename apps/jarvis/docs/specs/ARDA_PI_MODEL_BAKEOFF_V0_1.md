# Arda Pi Model Bake-off v0.1

Status: **EVIDENCE FROZEN**

Bu rapor, Raspberry Pi 5 / 8 GB üzerinde D4B Pi-native blind reconstruction workload'u için yapılan model karşılaştırmasının ölçüm kaydıdır. Deneyler aynı D4B contract, aynı validator ve model snapshot provenance yaklaşımıyla yürütüldü.

## Karar özeti

- **Fast baseline:** `qwen3:1.7b`
- **Deep verifier candidate:** `qwen3.5:4b`
- **Challenger, promotion yok:** `qwen3.5:2b`
- **Rejected for current Pi role:** `gemma4:e2b-it-q4_K_M`
- Default model, routing veya promotion bu raporla değiştirilmemiştir.

`qwen3.5:4b` D4B contract davranışında en güçlü adaydır; ancak uzun batch sonunda gözlenen bellek/swap baskısı nedeniyle sürekli çalışan default model olarak onaylanmamıştır.

## Ölçüm tablosu

| Model | Ölçüm | Contract PASS | First-pass | Correction | Latency | Kaynak sinyali | Karar |
|---|---:|---:|---:|---:|---:|---|---|
| `qwen3:1.7b` | 3 koşu | 3/3 | 0/3 | 3/3 | median 69.435 s | RSS ~1.9 GiB, max 36.4°C, swap 0 | Fast baseline |
| `qwen3.5:2b` | 3 koşu | 3/3 | 0/3 | 3/3 | median 161.026 s | RSS ~3.46 GiB, max 50.7°C, swap 0 | Challenger |
| `gemma4:e2b-it-q4_K_M` | 3 koşu | 2/3 | 1/3 | 2/3 | median 71.041 s | RSS ~5.06 GiB, max 55.1°C, swap gözlendi | Rejected |
| `qwen3.5:4b` | 20 episode | 20/20 | 19/20 | 1/20 | median 97.356 s, p95 113.763 s | batch sonrası MemAvailable ~1.2 GiB, swap ~229 MiB | Deep candidate |

## Qwen3.5:4B extended validation

Dağılım: 6 easy, 6 ambiguous, 4 adversarial, 4 conflict.

- Episode: `20`
- Contract PASS: `20/20`
- First-pass: `19/20` (%95)
- Correction: `1/20` (%5)
- Valid sealed record hash: `20/20`
- Uncertainty preserved: `20/20`
- Errors: `0`
- Latency min / median / mean / p95 / max: `75.711 / 97.356 / 96.481 / 113.763 / 161.354 s`

`ADVERSARIAL` ve `CONFLICT` koşularındaki PASS sonucu yalnızca D4B reconstruction/contract sealing başarısını gösterir. Bu koşulların task ile semantik hizası, güvenlik etkisi veya gerçek doğruluğu D4C Alignment tarafından ayrıca değerlendirilmelidir.

## Model kimlikleri

| Model | Digest |
|---|---|
| `qwen3:1.7b` | `8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7`
| `qwen3.5:2b` | `324d162be6ca5629ae4517c8710434d0bd2d665bc94dbad46e9af8fbf8a2f0df`
| `gemma4:e2b-it-q4_K_M` | `b37049369adfe3d2b653af0ab301a062ca5cbe96ace6aa0d3f2559ee9b563fc2`
| `qwen3.5:4b` | `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`

## Deney sınırları

- D4B canonical schema ve deterministic validator değiştirilmedi.
- Mac bağımlılığı kullanılmadı; deney Pi-native çalıştırıldı.
- Model adı tek başına provenance kabul edilmedi; digest/snapshot bağları korundu.
- Model routing veya otomatik promotion uygulanmadı.
- Bu sonuçlar D4C alignment, deployment approval veya genel model doğruluğu anlamına gelmez.
- Uzun batch sonrası bellek/swap baskısı, `qwen3.5:4b` için resource-aware policy gerektiren açık risktir.

## Kanıt

Qwen3.5:4B extended validation artefact'ları Pi üzerinde korunmaktadır:

```text
./artifacts/extended-qwen35-4b.jsonl
./artifacts/extended-qwen35-4b.log
```

Deployment/source baseline: `6007894a842774770653eef0507d7a8642fa9e0a`.

## Sonraki aşama

Bir sonraki milestone **D4C Alignment** olmalıdır:

```text
ReconstructionRecord
+ TaskContract
+ RequirementEvaluation
+ StructuralEvaluation
+ BehavioralEvaluation
→ ALIGNED / PARTIAL / MISALIGNED / INCONCLUSIVE
```

Bu raporun sonucu değiştirilmeden önce yeni model veya routing kararı aynı fixture/contract düzeninde yeni kanıtla değerlendirilmelidir.
