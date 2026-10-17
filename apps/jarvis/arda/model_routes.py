"""Policy-first model selection and bounded, privacy-preserving fallback.

This stays in-process on the Pi. A route lists exact model profiles in order;
the gateway never discovers credentials, broadens a privacy class, or retries a
tool action. Only explicitly transient generation failures may advance a route.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from arda.research_synthesis import (
    ModelAuthenticationError,
    ModelGateway,
    ModelBudgetError,
    ModelProviderError,
    ModelRateLimitedError,
    ModelResponse,
    ModelTransientError,
    OllamaNativeGateway,
    OpenAICompatibleGateway,
    _response,
)
from arda.core import Database


class ModelRouteError(ValueError):
    """A configured model route is invalid or has no policy-eligible candidate."""


# This gateway returns text and supports structured JSON requests. Streaming,
# vision, and tool-call execution are not implemented here and cannot be claimed.
MODEL_CAPABILITIES = frozenset({"chat", "json_schema"})
DATA_CLASSES = frozenset({"public", "internal", "sensitive"})


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    provider_id: str
    model_id: str
    gateway: ModelGateway = field(compare=False, repr=False)
    version: str = "1"
    capabilities: frozenset[str] = frozenset({"chat"})
    allowed_data_classes: frozenset[str] = frozenset({"public"})
    input_usd_per_million_tokens: float | None = None
    output_usd_per_million_tokens: float | None = None
    paid: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.profile_id or not self.provider_id or not self.model_id or not self.version:
            raise ModelRouteError("model profile identity is required")
        if not self.capabilities or self.capabilities - MODEL_CAPABILITIES:
            raise ModelRouteError("model profile has unknown or empty capabilities")
        if not self.allowed_data_classes or self.allowed_data_classes - DATA_CLASSES:
            raise ModelRouteError("model profile has an unknown or empty data class")
        for rate in (self.input_usd_per_million_tokens, self.output_usd_per_million_tokens):
            if rate is not None and (not math.isfinite(rate) or rate < 0):
                raise ModelRouteError("model cost rates cannot be negative")

    def public_snapshot(self) -> dict[str, Any]:
        """Safe route metadata; gateway objects, endpoints, and credentials are excluded."""
        return {
            "profile_id": self.profile_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "version": self.version,
            "capabilities": sorted(self.capabilities),
            "allowed_data_classes": sorted(self.allowed_data_classes),
            "input_usd_per_million_tokens": self.input_usd_per_million_tokens,
            "output_usd_per_million_tokens": self.output_usd_per_million_tokens,
            "paid": self.paid,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ModelRoutePolicy:
    profile_ids: tuple[str, ...]
    route_id: str = "default"
    version: str = "1"
    required_capabilities: frozenset[str] = frozenset({"chat"})
    data_class: str = "sensitive"
    allow_fallback: bool = False
    allow_paid_fallback: bool = False
    max_attempts: int = 1
    max_elapsed_seconds: float = 60.0
    max_cost_usd: float | None = None
    cooldown_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.route_id or not self.version:
            raise ModelRouteError("route identity and version are required")
        if not self.profile_ids or len(set(self.profile_ids)) != len(self.profile_ids):
            raise ModelRouteError("route profile IDs must be non-empty and unique")
        if self.data_class not in DATA_CLASSES:
            raise ModelRouteError("route data class is invalid")
        if not self.required_capabilities or self.required_capabilities - MODEL_CAPABILITIES:
            raise ModelRouteError("route capabilities are invalid")
        if self.max_attempts < 1 or self.max_elapsed_seconds <= 0 or self.cooldown_seconds < 0:
            raise ModelRouteError("route attempt/time limits are invalid")
        if self.max_cost_usd is not None and (not math.isfinite(self.max_cost_usd) or self.max_cost_usd < 0):
            raise ModelRouteError("route cost limit cannot be negative")

    def public_snapshot(self, profiles: Mapping[str, ModelProfile]) -> dict[str, Any]:
        if set(self.profile_ids) - set(profiles):
            raise ModelRouteError("route snapshot is missing a referenced model profile")
        return {
            "route_id": self.route_id,
            "version": self.version,
            "profile_ids": list(self.profile_ids),
            "profiles": [profiles[key].public_snapshot() for key in self.profile_ids],
            "required_capabilities": sorted(self.required_capabilities),
            "data_class": self.data_class,
            "allow_fallback": self.allow_fallback,
            "allow_paid_fallback": self.allow_paid_fallback,
            "max_attempts": self.max_attempts,
            "max_elapsed_seconds": self.max_elapsed_seconds,
            "max_cost_usd": self.max_cost_usd,
            "cooldown_seconds": self.cooldown_seconds,
        }


class SQLiteModelRouteStore:
    """Append-only versioned route/profile snapshots without provider secrets."""

    def __init__(self, db: Database):
        self.db = db
        if not all(db.table_exists(name) for name in ("model_profile_snapshots", "model_route_snapshots")):
            raise ModelRouteError("database migrations are not current")

    @staticmethod
    def _canonical_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def append_profile(self, profile: ModelProfile) -> str:
        payload = profile.public_snapshot()
        digest = self._canonical_hash(payload)
        existing = self.db.execute(
            "SELECT snapshot_hash FROM model_profile_snapshots WHERE profile_id=? AND version=?",
            (profile.profile_id, profile.version),
        ).fetchone()
        if existing:
            if existing["snapshot_hash"] != digest:
                raise ModelRouteError("model profile version identity conflict")
            return digest
        self.db.execute(
            "INSERT INTO model_profile_snapshots(profile_id, version, snapshot_hash, profile_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (profile.profile_id, profile.version, digest, json.dumps(payload, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        return digest

    def append_route(self, policy: ModelRoutePolicy, profiles: tuple[ModelProfile, ...] | list[ModelProfile]) -> str:
        profile_map = {profile.profile_id: profile for profile in profiles}
        payload = policy.public_snapshot(profile_map)
        for profile_id in policy.profile_ids:
            profile = profile_map[profile_id]
            stored = self.db.execute(
                "SELECT snapshot_hash FROM model_profile_snapshots WHERE profile_id=? AND version=?",
                (profile.profile_id, profile.version),
            ).fetchone()
            if stored is None or stored["snapshot_hash"] != self._canonical_hash(profile.public_snapshot()):
                raise ModelRouteError("route requires stored matching model profile snapshots")
        digest = self._canonical_hash(payload)
        existing = self.db.execute(
            "SELECT snapshot_hash FROM model_route_snapshots WHERE route_id=? AND version=?",
            (policy.route_id, policy.version),
        ).fetchone()
        if existing:
            if existing["snapshot_hash"] != digest:
                raise ModelRouteError("model route version identity conflict")
            return digest
        self.db.execute(
            "INSERT INTO model_route_snapshots(route_id, version, snapshot_hash, route_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (policy.route_id, policy.version, digest, json.dumps(payload, ensure_ascii=False, sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )
        return digest

    def get_route(self, route_id: str, version: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM model_route_snapshots WHERE route_id=? AND version=?", (route_id, version)
        ).fetchone()
        if row is None:
            raise ModelRouteError("model route snapshot not found")
        payload = json.loads(row["route_json"])
        if self._canonical_hash(payload) != row["snapshot_hash"]:
            raise ModelRouteError("model route snapshot hash mismatch")
        return {"snapshot_hash": row["snapshot_hash"], "route": payload}


def build_routed_gateway(config: Mapping[str, Any], route_id: str, *,
                         environ: Mapping[str, str] | None = None) -> RoutedModelGateway:
    """Build an explicitly configured local/OpenAI-compatible route.

    API keys are read by environment-variable name only; config snapshots and
    resulting profile metadata never contain credential values.
    """
    env = os.environ if environ is None else environ
    raw_profiles = config.get("profiles")
    raw_routes = config.get("routes")
    if not isinstance(raw_profiles, list) or not isinstance(raw_routes, Mapping):
        raise ModelRouteError("model gateway config requires profiles and routes")
    profiles: list[ModelProfile] = []
    for item in raw_profiles:
        if not isinstance(item, Mapping):
            raise ModelRouteError("model profile config entries must be objects")
        adapter = item.get("adapter")
        if any(not isinstance(item.get(key), str) or not item[key]
               for key in ("profile_id", "provider_id", "model_id", "base_url")):
            raise ModelRouteError("model profile identity and base_url are required")
        if adapter == "ollama":
            gateway: ModelGateway = OllamaNativeGateway(str(item.get("base_url", "")))
        elif adapter == "openai_compatible":
            key_name = item.get("api_key_env")
            if not isinstance(key_name, str) or not key_name or not env.get(key_name):
                raise ModelRouteError("model profile credential is unavailable")
            gateway = OpenAICompatibleGateway(str(item.get("base_url", "")), api_key=env[key_name])
        else:
            raise ModelRouteError("unsupported model gateway adapter")
        profiles.append(ModelProfile(
            profile_id=str(item.get("profile_id", "")), provider_id=str(item.get("provider_id", "")),
            model_id=str(item.get("model_id", "")), version=str(item.get("version", "1")), gateway=gateway,
            capabilities=frozenset(item.get("capabilities", ("chat",))),
            allowed_data_classes=frozenset(item.get("allowed_data_classes", ("public",))),
            input_usd_per_million_tokens=item.get("input_usd_per_million_tokens"),
            output_usd_per_million_tokens=item.get("output_usd_per_million_tokens"),
            paid=bool(item.get("paid", adapter != "ollama")), enabled=bool(item.get("enabled", True)),
        ))
    route_data = raw_routes.get(route_id)
    if not isinstance(route_data, Mapping):
        raise ModelRouteError("configured model route was not found")
    try:
        policy = ModelRoutePolicy(
            profile_ids=tuple(route_data["profile_ids"]), route_id=route_id,
            version=str(route_data.get("version", "1")),
            required_capabilities=frozenset(route_data.get("required_capabilities", ("chat",))),
            data_class=str(route_data.get("data_class", "sensitive")),
            allow_fallback=bool(route_data.get("allow_fallback", False)),
            allow_paid_fallback=bool(route_data.get("allow_paid_fallback", False)),
            max_attempts=int(route_data.get("max_attempts", 1)),
            max_elapsed_seconds=float(route_data.get("max_elapsed_seconds", 60)),
            max_cost_usd=route_data.get("max_cost_usd"),
            cooldown_seconds=float(route_data.get("cooldown_seconds", 30)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ModelRouteError):
            raise
        raise ModelRouteError("configured model route is invalid") from exc
    return RoutedModelGateway(profiles, policy)


class RoutedModelGateway:
    """Select eligible exact model profiles, with no implicit privacy downgrade."""

    def __init__(self, profiles: tuple[ModelProfile, ...] | list[ModelProfile], policy: ModelRoutePolicy):
        self._profiles = {profile.profile_id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ModelRouteError("model profile IDs must be unique")
        missing = set(policy.profile_ids) - set(self._profiles)
        if missing:
            raise ModelRouteError("route references an unknown model profile")
        route_profiles = [self._profiles[key] for key in policy.profile_ids]
        identities = {(profile.provider_id, profile.model_id) for profile in route_profiles}
        if len(identities) != len(route_profiles):
            raise ModelRouteError("a route cannot retry the same provider/model identity")
        self.policy = policy
        self._cooldowns: dict[str, float] = {}
        self._last_status: dict[str, str] = {key: "unknown" for key in self._profiles}
        self._calls = 0
        self._fallbacks = 0
        self._last_route: str | None = None

    def _eligible(self, profile: ModelProfile, *, is_fallback: bool) -> bool:
        policy = self.policy
        if not profile.enabled:
            return False
        cooldown_until = self._cooldowns.get(profile.profile_id)
        if cooldown_until is not None:
            if cooldown_until > time.monotonic():
                return False
            self._cooldowns.pop(profile.profile_id, None)
        if not policy.required_capabilities.issubset(profile.capabilities):
            return False
        if policy.data_class not in profile.allowed_data_classes:
            return False
        if is_fallback and (not policy.allow_fallback or (profile.paid and not policy.allow_paid_fallback)):
            return False
        return True

    def persist_snapshot(self, store: SQLiteModelRouteStore) -> str:
        """Persist the exact route and model identities used by this gateway."""
        profiles = [self._profiles[key] for key in self.policy.profile_ids]
        for profile in profiles:
            store.append_profile(profile)
        return store.append_route(self.policy, profiles)

    @staticmethod
    def _estimated_cost(profile: ModelProfile, *, prompt_bytes: int, max_output_tokens: int) -> float | None:
        input_rate, output_rate = profile.input_usd_per_million_tokens, profile.output_usd_per_million_tokens
        if input_rate is None or output_rate is None:
            return None
        # UTF-8 bytes and the requested output cap are conservative preflight
        # bounds; actual provider usage is still reported after the call.
        return (prompt_bytes * input_rate + max_output_tokens * output_rate) / 1_000_000

    def generate(self, *, system_prompt: str, user_prompt: str, model: str | None = None,
                 max_output_tokens: int, timeout_seconds: float,
                 response_schema: Mapping[str, Any] | None = None,
                 temperature: float = 0.0) -> ModelResponse:
        if max_output_tokens < 1 or timeout_seconds <= 0:
            raise ModelRouteError("model call limits are invalid")
        prompt_bytes = len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))
        ordered = [self._profiles[key] for key in self.policy.profile_ids]
        eligible = [profile for index, profile in enumerate(ordered)
                    if self._eligible(profile, is_fallback=index > 0)]
        if not eligible:
            raise ModelProviderError("no model profile satisfies route policy")
        if not self.policy.allow_fallback:
            eligible = eligible[:1]
        candidates = eligible[:self.policy.max_attempts]
        deadline = time.monotonic() + min(timeout_seconds, self.policy.max_elapsed_seconds)
        last_error: ModelTransientError | None = None
        reserved_cost = 0.0
        cost_blocked = False
        for index, profile in enumerate(candidates):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self.policy.max_cost_usd is not None:
                estimated = self._estimated_cost(profile, prompt_bytes=prompt_bytes,
                                                 max_output_tokens=max_output_tokens)
                if estimated is None or reserved_cost + estimated > self.policy.max_cost_usd:
                    cost_blocked = True
                    continue
                # Reserve before dispatch: a timed-out request may still be billed.
                reserved_cost += estimated
            if index > 0:
                self._fallbacks += 1
            self._calls += 1
            try:
                response = _response(profile.gateway.generate(
                    system_prompt=system_prompt, user_prompt=user_prompt, model=profile.model_id,
                    max_output_tokens=max_output_tokens, timeout_seconds=remaining,
                    response_schema=response_schema, temperature=temperature,
                ))
                if time.monotonic() > deadline:
                    self._last_status[profile.profile_id] = "deadline_exceeded"
                    self._cooldowns[profile.profile_id] = time.monotonic() + self.policy.cooldown_seconds
                    last_error = ModelTransientError("model route deadline exceeded")
                    break
            except ModelAuthenticationError:
                self._last_status[profile.profile_id] = "authentication_denied"
                raise
            except ModelTransientError as exc:
                self._last_status[profile.profile_id] = (
                    "rate_limited" if isinstance(exc, ModelRateLimitedError) else "transient_failure"
                )
                self._cooldowns[profile.profile_id] = time.monotonic() + self.policy.cooldown_seconds
                last_error = exc
                continue
            except ModelProviderError:
                self._last_status[profile.profile_id] = "provider_rejected"
                raise
            self._last_status[profile.profile_id] = "healthy"
            self._last_route = profile.profile_id
            return response
        if last_error is not None:
            raise last_error
        if cost_blocked:
            raise ModelBudgetError("model route cost budget exhausted")
        raise ModelProviderError("model route deadline exhausted")

    def health_snapshot(self) -> dict[str, Any]:
        """Return low-cardinality operational state without prompt or secret data."""
        now = time.monotonic()
        return {
            "calls": self._calls,
            "fallbacks": self._fallbacks,
            "last_profile_id": self._last_route,
            "profiles": {
                profile_id: {
                    "status": self._last_status[profile_id],
                    "cooldown_remaining_seconds": max(0.0, round(self._cooldowns.get(profile_id, now) - now, 3)),
                }
                for profile_id in sorted(self._profiles)
            },
        }
