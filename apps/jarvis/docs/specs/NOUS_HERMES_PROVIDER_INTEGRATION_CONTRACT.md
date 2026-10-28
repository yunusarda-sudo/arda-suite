# Nous Hermes Provider Integration Contract v0.1

Durum: **Contract defined; provider selection pending**

Bu sözleşme, Nous Research Hermes Agent'in Arda Core governance sınırı içinde
çalışabilmesi için gereken provider koşullarını tanımlar. Nous Hermes bir
agent client/orchestrator'dır; provider authority'si değildir. Provider seçimi
Arda Core capability registry, evaluation memory ve policy üzerinden yapılır.

## Authority boundary

```text
Nous Hermes request
        |
        v
Arda Core RouteProposal
        |
        v
Core Policy / RouteDecision
        |
        v
Provider Boundary
        |
        v
ProviderResult -> Artifact -> Verification -> Evaluation
```

Nous Hermes'in config'inde bir model adı bulunması, provider'ın Arda Core
tarafından route edildiği veya kullanılmasına izin verildiği anlamına gelmez.

## Minimum provider capability

Bir provider, Hermes agent-loop adayı sayılabilmek için aşağıdaki metadata'yı
sağlamalıdır:

```yaml
capability: agent.hermes_loop
requirements:
  context_window_min: 64000
  tool_calling: true
  structured_messages: true
  timeout_seconds_max: 300
  provenance_identity: required
```

`context_window` provider descriptor constraint'idir; model tag'ı tek başına
kanıt değildir. Context window metadata'sı yoksa provider uygun aday değildir.

## Required provenance

Her provider request/result zinciri şu alanları taşımalıdır:

```yaml
provider_id: required
model_snapshot: required
model_snapshot_hash: required_when_available
episode_id: required
task_contract_hash: required
route_proposal_id: required
route_decision_id: required
route_decision_hash: required
policy_snapshot_hash: required
request_hash: required
output_hash: required_for_returned_output
```

`ProviderResult` doğrulanmış sonuç değildir. Hermes'in metin cevabı ancak
Core artifact, verification, alignment ve completion zincirinden sonra
`VerifiedOutcome` veya `EvaluationRecord` üretimine katkı sağlayabilir.

## Privacy and policy

- `private`/`secret` task'lar için yalnız policy'nin izin verdiği local veya
  özel veri sınırına uygun provider kullanılabilir.
- Cloud provider credential'ları Hermes MCP payload'ına veya tool sonucuna
  verilmez.
- `DENY` ve `REQUIRE_APPROVAL` durumlarında provider adapter çağrılmaz.
- Provider timeout/unavailable/malformed sonucu fabricated success üretemez.
- Fallback provider seçimi adapter içinde yapılamaz; yeni RouteProposal ve
  Core policy değerlendirmesi gerekir.

## Current Pi status

```yaml
qwen3:1.7b:
  context_window: 40960
  hermes_minimum: 64000
  status: blocked_context_window
```

Bu nedenle Hermes config'inde context'i zorlayan bir override yapılmaz ve bu
model `agent.hermes_loop` için route edilmez. Aynı model Arda'nın daha dar
D4B local verifier workload'unda ayrı bir capability için kullanılabilir;
capability suitability görev bazlıdır.

Provider metadata eligibility kontrolü `arda.hermes_provider` içinde
deterministic olarak uygulanır. Bu kontrol yalnız metadata değerlendirir;
provider çağrısı veya network smoke yapmaz.

## Acceptance gates

1. Provider descriptor + context/tool metadata kaydı.
2. Capability/evaluation evidence ile deterministic eligibility.
3. Core `RouteProposal` ve `RouteDecision(ALLOW)`.
4. Nous Hermes minimal read-only loop: request -> MCP read -> answer.
5. Tool discovery allowlist ve no-shell/no-direct-provider kanıtı.
6. Provider failure, timeout, malformed response ve replay testleri.
7. Action tool'ları ancak read-only loop ve provider provenance gate'lerinden
   sonra ayrıca açılır.

## Evaluation fixture v0.1

Ortak read-only, abstention ve mutation-boundary fixture seti
`arda.hermes_evaluation` içinde tanımlıdır. Provider karşılaştırması aynı
fixture seti ve aynı trace evaluator üzerinden yapılır; provider'ın kendi
başarı beyanı evaluation kanıtı sayılmaz.

Fixture batch sonucu mevcut append-only `ProviderEvaluationRecord` tipine
`arda.hermes_evaluation.build_provider_evaluation` ile dönüştürülür. Aynı
evaluation identity'si ikinci kez yazılamaz; ayrı bir Hermes memory store'u
oluşturulmaz.

## Promotion policy v0.1

Evaluation sonucu tek başına capability promotion veya routing izni değildir.
Promosyon kararı `arda.provider_promotion.evaluate_promotion` tarafından
yan etkisiz ve deterministik olarak hesaplanır; registry mapping'i yazmaz,
provider çağırmaz ve mevcut routing kararını değiştirmez.

Varsayılan eşikler:

```yaml
minimum_samples: 3
minimum_accepted_rate: 1.0
minimum_fixture_pass_rate: 1.0
maximum_tool_violation_rate: 0.0
maximum_timeout_rate: 0.0
maximum_malformed_response_rate: 0.0
routing_activation_enabled: false
```

Durum geçişi:

```text
0 matching records        -> CANDIDATE
1..2 clean records        -> EVALUATED
3+ clean records          -> VALIDATED
hard contract/resource failure or metadata ineligibility -> REJECTED
```

`VALIDATED` yalnızca capability kanıtının eşikleri geçtiğini belirtir;
`routing_eligible` varsayılan olarak `false` kalır. Routing aktivasyonu ayrı,
açık ve yetkili bir policy/mapping transition gerektirir. Böylece
`evaluation != approval`, `validated != automatically routed` invariant'ları
korunur.

Qwen3:1.7B gibi Hermes minimum context koşulunu karşılamayan baseline'lar
metadata eligibility sonucu `REJECTED` olarak raporlanabilir; bu, modelin
diğer daha dar Arda capability'lerindeki uygunluğunu etkilemez.
