# Arda Core M2.4D3.5 — Requirement Reconstruction and Versioning

## Amaç

D3.5, TaskContract’ın tek seferlik ve üzerine yazılan bir belge olmasını
engeller. Yeni evidence, kullanıcı clarification’ı veya repository discovery
geldiğinde yeni revision append edilir; eski contract silinmez ve değişmez.

```text
TaskContract v1
  → parent_revision_id=v1
TaskContract v2
  → parent_revision_id=v2
TaskContract v3
```

## Requirement source vocabulary

| Source | Anlamı | Auditor davranışı |
| --- | --- | --- |
| `EXPLICIT` | Kullanıcı/source tarafından belirtilmiş | Typed evidence ile audit edilebilir |
| `INFERRED` | Evidence ile desteklenmiş çıkarım | Typed evidence olmadan geçmez |
| `CANDIDATE` | Model veya agent hypothesis’i | `UNVERIFIED`, otomatik kabul yok |
| `UNRESOLVED` | Yeterli evidence yok | `UNVERIFIED` |
| `REJECTED` | Evidence ile çelişiyor veya iptal edilmiş | `VIOLATED` |

`CANDIDATE` hiçbir zaman sessizce `INFERRED` veya `EXPLICIT` olmaz. Promotion
gelecekte ayrı evidence/approval akışı gerektirir.

## ContractRevision

Her revision şunları taşır:

- `revision_id`
- `parent_revision_id`
- monotonic `revision_number`
- immutable `task_contract` ve `contract_hash`
- değişiklik `reason`
- provenance map
- added / changed / removed requirement ID’leri

Chain kuralları:

- parent yalnızca mevcut head olabilir;
- aynı revision ID tekrar kullanılamaz;
- task ve episode identity değiştirilemez;
- append yeni chain state döndürür, eski state’i mutate etmez;
- eski revision’lar history’de kalır.

Bu slice deterministic in-process chain contract’ıdır. SQLite durable persistence
ve model-assisted candidate recovery sonraki gate’lerdir.
