# ARDA Core Hermes Provider Selection v0.1

Durum: **Selection contract defined; governed provider run pending**

## Seçim sırası

Hermes loop provider seçimi şu sırayla yapılır:

1. Capability ve metadata eligibility
2. Privacy/policy constraints
3. Immutable evaluation evidence
4. Contract compliance ve uncertainty davranışı
5. Resource/cost/latency
6. Ayrı Core authority kararı

Model adı veya genel benchmark skoru tek başına route eligibility değildir.

## Provider durumları

```text
registered
    -> metadata_eligible
    -> evaluated
    -> validated capability
    -> explicitly routing eligible
```

Her geçişin evidence hash'i ve policy snapshot'ı olmalıdır. `VALIDATED`,
`routing_eligible` anlamına gelmez.

## v0.1 aday matrisi

| Provider | Context | Tools | Metadata | İlk durum |
|---|---:|---:|---:|---|
| `ollama-qwen3:1.7b` | 40960 | var | Hermes için uyumsuz | `REJECTED` / negative control |
| `ollama-qwen3.5:4b` | 262144 | var | uygun görünüyor | `CANDIDATE` |

Metadata değerleri model tag'ından değil, Pi Ollama `/api/tags` çıktısından
alınmalıdır; snapshot digest registry identity'sinin parçasıdır.

## Governed-run şartı

Bir provider ancak şu zincirden geçen evaluation ile capability promotion'a
girebilir:

```text
TaskContract
 -> RouteProposal
 -> Core RouteDecision(ALLOW)
 -> ProviderBoundary
 -> ProviderResult
 -> OutputArtifact
 -> Verification
 -> EvaluationRecord
```

Doğrudan Ollama endpointine yapılan smoke/trace ölçümü yalnızca ön gözlemdir;
`VALIDATED` veya routing kanıtı olarak kullanılamaz.

## Action sınırı

Bu milestone boyunca Nous Hermes MCP yüzeyi read-only kalır. Provider
benchmarkı action tool, shell, filesystem write, credential veya automatic
fallback açmaz.
