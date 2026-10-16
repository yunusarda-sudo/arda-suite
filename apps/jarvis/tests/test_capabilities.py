"""Capability/provider descriptor invariants for the governance boundary."""

import pytest
import sqlite3

from arda.capabilities import (
    CapabilityDescriptor,
    CapabilityLifecycle,
    CapabilityRegistry,
    CapabilityRegistryError,
    ProviderCapabilityMapping,
    ProviderDescriptor,
    ProviderEvaluationRecord,
    ProviderRegistry,
    ProviderStatus,
    SQLiteCapabilityStore,
)
from arda.core import Database


def capability(*, capability_id="coding.patch", lifecycle=CapabilityLifecycle.CANDIDATE):
    return CapabilityDescriptor(
        capability_id=capability_id,
        version="0.1",
        description="bounded repository patch candidate",
        input_contract="PatchRequest-v0.1",
        output_contract="PatchResult-v0.1",
        evidence_required=("patch_result", "structural_evaluation"),
        policy_requirements=("repository_scope", "sandbox_required"),
        side_effect_class="repository_workspace_only",
        lifecycle=lifecycle,
    )


def provider(provider_id="local-qwen"):
    return ProviderDescriptor(
        provider_id=provider_id,
        provider_kind="local_model",
        identity={"model_name": "qwen3:1.7b", "model_snapshot_hash": "snapshot-1"},
        capabilities=("verification.alignment",),
        constraints={"local_only": True},
    )


def evaluation(*, provider_id="local-qwen", capability_id="verification.alignment", accepted=True):
    return ProviderEvaluationRecord(
        evaluation_id="evaluation-1",
        provider_id=provider_id,
        provider_snapshot_hash="snapshot-1",
        capability_id=capability_id,
        capability_version="0.1",
        fixture_set_hash="fixture-1",
        contract_version="d4b-v0.1",
        accepted=accepted,
        metrics={"first_pass_rate": 1.0},
        evaluator_version="fixture-evaluator-v1",
    )


def test_capability_descriptor_has_canonical_hash_and_namespace():
    value = capability()
    assert value.canonical_hash == capability().canonical_hash
    assert value.namespace == "coding"
    assert value.operation == "patch"


def test_duplicate_capability_id_is_rejected():
    registry = CapabilityRegistry()
    registry.register(capability())
    with pytest.raises(CapabilityRegistryError, match="already registered"):
        registry.register(capability())


def test_namespace_mismatch_is_rejected():
    with pytest.raises(CapabilityRegistryError, match="namespace"):
        capability(capability_id="research.acquire").__class__(
            capability_id="research.acquire",
            version="0.1",
            description="bad namespace",
            input_contract="x",
            output_contract="y",
            namespace="coding",
            operation="acquire",
        ).validate()


def test_retired_capability_cannot_be_reactivated():
    registry = CapabilityRegistry()
    registry.register(capability(lifecycle=CapabilityLifecycle.RETIRED))
    with pytest.raises(CapabilityRegistryError, match="retired"):
        registry.set_lifecycle("coding.patch", CapabilityLifecycle.CANDIDATE)


def test_validated_mapping_requires_accepted_evaluation():
    capabilities = CapabilityRegistry()
    capabilities.register(capability(capability_id="verification.alignment"))
    registry = ProviderRegistry(capabilities)
    registry.register(provider())
    mapping = ProviderCapabilityMapping(
        provider_id="local-qwen",
        capability_id="verification.alignment",
        capability_version="0.1",
        status=ProviderStatus.VALIDATED,
        evidence_snapshot_hash=evaluation().canonical_hash,
    )
    with pytest.raises(CapabilityRegistryError, match="accepted evaluation"):
        registry.register_mapping(mapping)

    registry.record_evaluation(evaluation())
    registry.register_mapping(mapping)
    assert registry.get_mapping("local-qwen", "verification.alignment").status is ProviderStatus.VALIDATED


def test_rejected_evaluation_cannot_validate_provider():
    capabilities = CapabilityRegistry()
    capabilities.register(capability(capability_id="verification.alignment"))
    registry = ProviderRegistry(capabilities)
    registry.register(provider())
    registry.record_evaluation(evaluation(accepted=False))
    with pytest.raises(CapabilityRegistryError, match="accepted evaluation"):
        registry.register_mapping(ProviderCapabilityMapping(
            provider_id="local-qwen",
            capability_id="verification.alignment",
            capability_version="0.1",
            status=ProviderStatus.VALIDATED,
            evidence_snapshot_hash=evaluation(accepted=False).canonical_hash,
        ))


def test_provider_queries_are_read_only_snapshots():
    registry = ProviderRegistry()
    registry.register(provider())
    listed = registry.list_providers()
    assert listed[0] == provider()
    assert registry.get("local-qwen") == provider()


def test_sqlite_store_persists_append_only_descriptors_evaluations_and_mapping(tmp_path):
    db = Database(tmp_path / "capabilities.sqlite3")
    assert db.migrate() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    store = SQLiteCapabilityStore(db)
    cap = capability(capability_id="verification.alignment")
    prov = provider()
    eval_record = evaluation()
    store.append_capability(cap)
    store.append_provider(prov)
    store.append_evaluation(eval_record)
    store.append_mapping(
        ProviderCapabilityMapping(
            provider_id=prov.provider_id,
            capability_id=cap.capability_id,
            capability_version=cap.version,
            status=ProviderStatus.VALIDATED,
            evidence_snapshot_hash=eval_record.canonical_hash,
        ),
        reason="fixture gate passed",
        transition_id="transition-1",
    )
    assert store.current_mapping(prov.provider_id, cap.capability_id).status is ProviderStatus.VALIDATED
    assert len(store.list_capabilities()) == 1
    assert len(store.list_providers()) == 1
    with pytest.raises(CapabilityRegistryError, match="already persisted"):
        store.append_evaluation(eval_record)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE provider_evaluations SET evaluation_json='tampered'")
    db.close()


def test_sqlite_store_rejects_validated_mapping_without_accepted_evidence(tmp_path):
    db = Database(tmp_path / "capabilities.sqlite3")
    db.migrate()
    store = SQLiteCapabilityStore(db)
    cap = capability(capability_id="verification.alignment")
    prov = provider()
    store.append_capability(cap)
    store.append_provider(prov)
    with pytest.raises(CapabilityRegistryError, match="accepted evaluation"):
        store.append_mapping(
            ProviderCapabilityMapping(
                provider_id=prov.provider_id,
                capability_id=cap.capability_id,
                capability_version=cap.version,
                status=ProviderStatus.VALIDATED,
                evidence_snapshot_hash="missing",
            ),
            reason="not enough evidence",
            transition_id="transition-missing",
        )
    db.close()
