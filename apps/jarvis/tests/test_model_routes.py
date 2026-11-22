from __future__ import annotations

import json

import pytest

from arda.core import Database
from arda.model_routes import (ModelProfile, ModelRouteError, ModelRoutePolicy,
                               RoutedModelGateway, SQLiteModelRouteStore, build_routed_gateway)
from arda.research_synthesis import (
    ModelAuthenticationError,
    ModelBudgetError,
    ModelRateLimitedError,
    ModelProviderError,
    ModelResponse,
    ModelTransientError,
)


class FakeGateway:
    def __init__(self, result=None, error=None):
        self.result = result or ModelResponse("ok", "actual", 10, 5)
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def profile(name, gateway, *, privacy=("public", "internal", "sensitive"),
            capabilities=("chat", "json_schema"), cost=1.0, paid=False):
    return ModelProfile(
        profile_id=name, provider_id=f"provider-{name}", model_id=f"model-{name}", gateway=gateway,
        capabilities=frozenset(capabilities), allowed_data_classes=frozenset(privacy),
        input_usd_per_million_tokens=cost, output_usd_per_million_tokens=cost, paid=paid,
    )


def test_fallback_requires_explicit_policy_and_only_retries_transient_errors():
    first = FakeGateway(error=ModelRateLimitedError("limited"))
    second = FakeGateway()
    routed = RoutedModelGateway(
        [profile("local", first), profile("cloud", second, paid=True)],
        ModelRoutePolicy(profile_ids=("local", "cloud"), required_capabilities=frozenset({"chat", "json_schema"}),
                         data_class="sensitive", allow_fallback=True, allow_paid_fallback=True, max_attempts=2),
    )

    response = routed.generate(system_prompt="secret", user_prompt="private task", model="ignored",
                               max_output_tokens=20, timeout_seconds=3, response_schema={"type": "object"})

    assert response.model == "actual"
    assert first.calls[0]["model"] == "model-local"
    assert second.calls[0]["response_schema"] == {"type": "object"}
    assert routed.health_snapshot()["fallbacks"] == 1
    assert "private task" not in json.dumps(routed.health_snapshot())


def test_auth_denial_never_falls_back():
    first = FakeGateway(error=ModelAuthenticationError("denied"))
    second = FakeGateway()
    routed = RoutedModelGateway(
        [profile("primary", first), profile("fallback", second)],
        ModelRoutePolicy(profile_ids=("primary", "fallback"), data_class="sensitive",
                         allow_fallback=True, max_attempts=2),
    )

    with pytest.raises(ModelAuthenticationError):
        routed.generate(system_prompt="s", user_prompt="u", max_output_tokens=10, timeout_seconds=2)
    assert len(first.calls) == 1
    assert second.calls == []


def test_privacy_denied_fallback_is_not_invoked():
    first = FakeGateway(error=ModelTransientError("offline"))
    cloud = FakeGateway()
    routed = RoutedModelGateway(
        [profile("local", first), profile("cloud", cloud, privacy=("public",), paid=True)],
        ModelRoutePolicy(profile_ids=("local", "cloud"), data_class="sensitive",
                         allow_fallback=True, allow_paid_fallback=True, max_attempts=2),
    )

    with pytest.raises(ModelTransientError):
        routed.generate(system_prompt="s", user_prompt="private", max_output_tokens=10, timeout_seconds=2)
    assert cloud.calls == []


def test_paid_fallback_requires_explicit_cost_consent():
    first = FakeGateway(error=ModelTransientError("offline"))
    cloud = FakeGateway()
    routed = RoutedModelGateway(
        [profile("primary", first), profile("cloud", cloud, paid=True)],
        ModelRoutePolicy(profile_ids=("primary", "cloud"), data_class="sensitive",
                         allow_fallback=True, allow_paid_fallback=False, max_attempts=2),
    )

    with pytest.raises(ModelTransientError):
        routed.generate(system_prompt="s", user_prompt="u", max_output_tokens=10, timeout_seconds=2)
    assert len(first.calls) == 1
    assert cloud.calls == []


def test_cost_preflight_excludes_profiles_without_known_rates_or_over_budget():
    gateway = FakeGateway()
    model = profile("cloud", gateway, cost=10, paid=True)
    routed = RoutedModelGateway([model], ModelRoutePolicy(profile_ids=("cloud",), data_class="sensitive",
                                                           max_cost_usd=0.000001))

    with pytest.raises(ModelBudgetError, match="cost budget"):
        routed.generate(system_prompt="long " * 100, user_prompt="private", max_output_tokens=100,
                        timeout_seconds=2)
    assert gateway.calls == []


def test_transient_failure_enters_cooldown_and_cannot_be_immediately_replayed():
    failing = FakeGateway(error=ModelTransientError("offline"))
    routed = RoutedModelGateway(
        [profile("local", failing)],
        ModelRoutePolicy(profile_ids=("local",), data_class="sensitive", cooldown_seconds=30),
    )

    with pytest.raises(ModelTransientError):
        routed.generate(system_prompt="s", user_prompt="u", max_output_tokens=10, timeout_seconds=2)
    with pytest.raises(ModelProviderError, match="no model profile"):
        routed.generate(system_prompt="s", user_prompt="u", max_output_tokens=10, timeout_seconds=2)
    assert len(failing.calls) == 1


def test_route_limits_attempts_and_uses_remaining_deadline():
    first = FakeGateway(error=ModelTransientError("offline"))
    second = FakeGateway()
    policy = ModelRoutePolicy(profile_ids=("first", "second"), data_class="sensitive",
                              allow_fallback=True, max_attempts=2, max_elapsed_seconds=1)
    routed = RoutedModelGateway([profile("first", first), profile("second", second)], policy)
    routed.generate(system_prompt="s", user_prompt="u", max_output_tokens=10, timeout_seconds=5)

    assert first.calls[0]["timeout_seconds"] <= 1
    assert second.calls[0]["timeout_seconds"] > 0


def test_route_rejects_a_late_success_when_adapter_ignores_its_timeout():
    import time

    class SlowGateway(FakeGateway):
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            time.sleep(0.03)
            return self.result

    slow = SlowGateway()
    routed = RoutedModelGateway(
        [profile("slow", slow)],
        ModelRoutePolicy(profile_ids=("slow",), data_class="sensitive", max_elapsed_seconds=0.005),
    )

    with pytest.raises(ModelTransientError, match="deadline"):
        routed.generate(system_prompt="s", user_prompt="u", max_output_tokens=10, timeout_seconds=1)


def test_profile_and_route_reject_unknown_capabilities():
    with pytest.raises(ModelRouteError):
        profile("bad", FakeGateway(), capabilities=("tool_calling",))
    with pytest.raises(ModelRouteError):
        ModelRoutePolicy(profile_ids=("x",), required_capabilities=frozenset({"streaming"}))


def test_versioned_route_snapshots_are_append_only_and_exclude_gateway_secrets(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    try:
        store = SQLiteModelRouteStore(db)
        gateway = FakeGateway()
        profiles = [profile("local", gateway)]
        policy = ModelRoutePolicy(profile_ids=("local",), route_id="coding", version="3",
                                  data_class="sensitive")
        profile_hash = store.append_profile(profiles[0])
        route_hash = store.append_route(policy, profiles)
        assert store.append_profile(profiles[0]) == profile_hash
        assert store.append_route(policy, profiles) == route_hash
        snapshot = store.get_route("coding", "3")
        assert snapshot["snapshot_hash"] == route_hash
        assert snapshot["route"]["profiles"][0]["version"] == "1"
        assert "gateway" not in json.dumps(snapshot)
        with pytest.raises(ModelRouteError, match="identity conflict"):
            store.append_profile(ModelProfile(
                profile_id="local", provider_id="provider-local", model_id="different-model",
                gateway=gateway, version="1", capabilities=frozenset({"chat"}),
                allowed_data_classes=frozenset({"sensitive"}),
            ))
    finally:
        db.close()


def test_configured_gateway_loads_only_named_environment_credentials():
    api_key = "synthetic-secret-value"
    config = {
        "profiles": [{
            "profile_id": "openai", "provider_id": "openai", "model_id": "gpt-4o-mini",
            "adapter": "openai_compatible", "base_url": "https://api.openai.com/v1",
            "api_key_env": "TEST_OPENAI_KEY", "capabilities": ["chat"],
            "allowed_data_classes": ["public"], "paid": True,
        }],
        "routes": {"research": {"profile_ids": ["openai"], "data_class": "public"}},
    }
    with pytest.raises(ModelRouteError, match="credential is unavailable"):
        build_routed_gateway(config, "research", environ={})

    gateway = build_routed_gateway(config, "research", environ={"TEST_OPENAI_KEY": api_key})
    snapshot = gateway.policy.public_snapshot(gateway._profiles)
    assert snapshot["profiles"][0]["model_id"] == "gpt-4o-mini"
    assert api_key not in json.dumps(snapshot)
    assert "base_url" not in json.dumps(snapshot)
