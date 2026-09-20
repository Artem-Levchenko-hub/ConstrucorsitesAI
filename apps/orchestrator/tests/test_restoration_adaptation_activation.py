from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    ActivationObservedIdentity,
    ActivationPreparedTarget,
    RestorationAdaptationActivationRequest,
)
from omnia_orchestrator.services.restoration_adaptation_activation import (
    RestorationAdaptationActivationEngine,
    _durable_mkdir,
)


class SimulatedCrash(BaseException):
    pass


def _business_probe() -> ActivationBusinessProbe:
    value: dict[str, object] = {
        "version": 1,
        "endpoint": "/api/orders/restoration-probe",
        "data_contract_digest": "8" * 64,
        "witnesses": [
            {
                "entity": "orders",
                "id_column": "id",
                "owner_column": "max_user_id",
                "value_column": "probe_value",
                "create_values": {},
            }
        ],
        "max_payload_bytes": 4096,
        "pointers": {
            "items": "/items",
            "item": "/item",
            "item_id": "/item/id",
            "owner_id": "/item/ownerId",
            "entity": "/item/entity",
            "marker": "/item/marker",
            "phase": "/item/phase",
            "contract_digest": "/probeContractDigest",
        },
    }
    value["contract_digest"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ActivationBusinessProbe.model_validate(value)


def request(**updates: object) -> RestorationAdaptationActivationRequest:
    value: dict[str, object] = {
        "operation_id": UUID(int=1),
        "generation_run_id": UUID(int=2),
        "project_id": UUID(int=3),
        "owner_id": UUID(int=4),
        "source_workspace_id": UUID(int=5),
        "expected_source_fencing_epoch": 11,
        "target_fencing_epoch": 12,
        "source_workspace_revision": "1" * 64,
        "source_code_volume": "source-code",
        "live_database_volume": "live-business-db",
        "live_database_identity_digest": "2" * 64,
        "candidate_workspace_id": UUID(int=6),
        "candidate_fencing_epoch": 1,
        "candidate_workspace_revision": "3" * 64,
        "proof_digest": "4" * 64,
        "candidate_artifact_digest": "5" * 64,
        "candidate_source_manifest_digest": "8" * 64,
        "candidate_code_digest": hashlib.sha256(b"sealed-candidate-code").hexdigest(),
        "business_probe": _business_probe(),
        "probe_rehearsal_digest": "9" * 64,
        "probe_rehearsal_database_digest": "a" * 64,
        "planned_commit_sha": "a" * 40,
        "activation_id": UUID(int=7),
        "activation_digest": "6" * 64,
    }
    value.update(updates)
    draft = RestorationAdaptationActivationRequest.model_construct(**value)
    value["activation_digest"] = hashlib.sha256(
        json.dumps(
            draft.activation_binding_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return RestorationAdaptationActivationRequest.model_validate(value)


class Effects:
    def __init__(self, value: RestorationAdaptationActivationRequest) -> None:
        self.value = value
        self.calls: list[str] = []
        self.counts: Counter[str] = Counter()
        self.crash_once: str | None = None
        self.crash_after_once: str | None = None
        self.fail_once: str | None = None
        self.sealed_artifact: Path | None = None
        self.transition_locked = False

    @asynccontextmanager
    async def hold_transition(self, _value: RestorationAdaptationActivationRequest):
        assert self.transition_locked is False
        self.transition_locked = True
        try:
            yield
        finally:
            self.transition_locked = False

    def _call(self, name: str) -> None:
        assert self.transition_locked, f"effect {name} ran outside lifecycle lock"
        self.calls.append(name)
        self.counts[name] += 1
        if self.crash_once == name:
            self.crash_once = None
            raise SimulatedCrash(name)
        if self.fail_once == name:
            self.fail_once = None
            raise RuntimeError(name)

    def _after_call(self, name: str) -> None:
        if self.crash_after_once == name:
            self.crash_after_once = None
            raise SimulatedCrash(name)

    async def observe_bound_identities(
        self, value: RestorationAdaptationActivationRequest
    ) -> ActivationObservedIdentity:
        self._call("observe")
        return ActivationObservedIdentity.from_request(value)

    async def export_candidate_code(
        self, _value: RestorationAdaptationActivationRequest, destination: Path
    ) -> None:
        self._call("prepare_target")
        await asyncio.to_thread(destination.write_bytes, b"sealed-candidate-code")

    async def prepare_target_code(
        self,
        value: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
        target_code_volume: str,
    ) -> ActivationPreparedTarget:
        self._call("prepare_target")
        assert await asyncio.to_thread(sealed_artifact.read_bytes) == b"sealed-candidate-code"
        self.sealed_artifact = sealed_artifact
        return ActivationPreparedTarget(
            workspace_id=value.source_workspace_id,
            fencing_epoch=value.target_fencing_epoch,
            code_volume=target_code_volume,
            code_digest=value.candidate_code_digest,
            database_volume=value.live_database_volume,
            database_identity_digest=value.live_database_identity_digest,
        )

    async def verify_prepared_target(
        self,
        _value: RestorationAdaptationActivationRequest,
        _target: ActivationPreparedTarget,
    ) -> str:
        self._call("verify_target")
        return "e" * 64

    async def cleanup_candidate(
        self,
        _value: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
    ) -> None:
        self._call("cleanup_candidate")
        assert await asyncio.to_thread(sealed_artifact.is_file)
        artifact = await asyncio.to_thread(sealed_artifact.read_bytes)
        assert hashlib.sha256(artifact).hexdigest() == (
            self.value.candidate_code_digest
        )

    async def stop_source_writers(
        self, _value: RestorationAdaptationActivationRequest
    ) -> None:
        self._call("stop_source")

    async def start_target_writers(
        self,
        _value: RestorationAdaptationActivationRequest,
        _target: ActivationPreparedTarget,
    ) -> None:
        self._call("start_target")

    async def start_source_writers(
        self, _value: RestorationAdaptationActivationRequest
    ) -> None:
        self._call("start_source")
        self._after_call("start_source")

    async def verify_source_health(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("source_health")
        self._after_call("source_health")
        return "7" * 64

    async def delete_target_code(
        self,
        _value: RestorationAdaptationActivationRequest,
        _target: ActivationPreparedTarget,
    ) -> None:
        self._call("delete_target")
        self._after_call("delete_target")

    async def verify_service_readiness(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("health_readiness")
        return "8" * 64

    async def verify_signed_owner_read(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("health_owner_read")
        return "9" * 64

    async def verify_signed_owner_write(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("health_owner_write")
        return "a" * 64

    async def verify_signed_owner_reload(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("health_owner_reload")
        return "b" * 64

    async def verify_cross_owner_denial(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("health_cross_owner_denial")
        return "c" * 64

    async def verify_unauthenticated_denial(
        self, _value: RestorationAdaptationActivationRequest
    ) -> str:
        self._call("health_unauth_denial")
        return "d" * 64


def engine(tmp_path: Path, value: RestorationAdaptationActivationRequest):
    effects = Effects(value)
    offer_artifacts = tmp_path.parent / f"{tmp_path.name}-offer-artifacts"
    offer_artifacts.mkdir(parents=True, exist_ok=True)
    (offer_artifacts / f"{value.activation_id}.tar").write_bytes(
        b"sealed-candidate-code"
    )
    return (
        RestorationAdaptationActivationEngine(
            root=tmp_path,
            offer_artifacts_root=offer_artifacts,
            effects=effects,
            managed_root=tmp_path.parent,
        ),
        effects,
    )


def test_activation_request_rejects_candidate_aliasing_live_source():
    with pytest.raises(ValueError, match="candidate workspace must be isolated"):
        request(candidate_workspace_id=UUID(int=5))


async def test_activation_seals_candidate_before_cleanup_and_reuses_live_database(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)

    receipt = await service.activate(value)

    assert receipt.state == "activated"
    assert receipt.effects_admitted is True
    assert receipt.source_volume_identity.database_volume == "live-business-db"
    assert receipt.target_volume_identity.database_volume == "live-business-db"
    assert receipt.source_volume_identity.database_identity_digest == "2" * 64
    assert receipt.target_volume_identity.database_identity_digest == "2" * 64
    assert receipt.health_digest is not None
    assert receipt.receipt_digest is not None
    assert not (service.offer_artifacts_root / f"{value.activation_id}.tar").exists()
    assert effects.calls.index("prepare_target") < effects.calls.index("cleanup_candidate")
    assert effects.calls.index("stop_source") < effects.calls.index("start_target")
    assert effects.calls[-6:] == [
        "health_readiness",
        "health_owner_read",
        "health_owner_write",
        "health_owner_reload",
        "health_cross_owner_denial",
        "health_unauth_denial",
    ]


async def test_duplicate_activation_returns_receipt_without_second_effect(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)

    first = await service.activate(value)
    counts = effects.counts.copy()
    second = await service.activate(value)

    assert second == first
    assert effects.counts == counts


def test_protected_activation_registry_keeps_active_and_corrupt_engine_journals(
    tmp_path: Path,
):
    value = request()
    service, _effects = engine(tmp_path, value)
    journal = service._load_or_create(value)

    assert service.protected_activation_ids() == frozenset({value.activation_id})

    path = service._journal_path(value)
    path.write_text("not-json", encoding="utf-8")

    assert service.protected_activation_ids() == frozenset({value.activation_id})

    service._write_journal(value, {**journal, "state": "cancelled"})

    assert service.protected_activation_ids() == frozenset()


@pytest.mark.parametrize(
    ("crash_at", "durable_state", "effects_admitted"),
    [
        ("prepare_target", "intent", False),
        ("cleanup_candidate", "target_prepared", False),
        ("stop_source", "writers_stopping", False),
        ("start_target", "target_writers_admitted", True),
        ("health_owner_write", "target_writers_admitted", True),
    ],
)
async def test_recover_replays_each_durable_phase_forward(
    tmp_path: Path, crash_at: str, durable_state: str, effects_admitted: bool
):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = crash_at

    with pytest.raises(SimulatedCrash):
        await service.activate(value)

    interrupted = await service.status(value)
    assert interrupted.state == durable_state
    assert interrupted.effects_admitted is effects_admitted

    recovered = await service.recover(value)
    assert recovered.state == "activated"
    assert recovered.effects_admitted is True
    assert effects.counts["start_source"] == 0
    assert effects.counts["delete_target"] == 0


async def test_failure_after_ponr_is_forward_only_and_cancel_conflicts(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.fail_once = "start_target"

    with pytest.raises(CellResourceError, match="forward recovery"):
        await service.activate(value)

    receipt = await service.status(value)
    assert receipt.state == "target_writers_admitted"
    assert receipt.effects_admitted is True
    assert "start_source" not in effects.calls
    assert "delete_target" not in effects.calls
    with pytest.raises(CellIdentityConflict, match="cannot be cancelled"):
        await service.cancel(value)
    assert "start_source" not in effects.calls
    assert "delete_target" not in effects.calls

    recovered = await service.recover(value)
    assert recovered.state == "activated"
    assert "start_source" not in effects.calls
    assert "delete_target" not in effects.calls


async def test_cancel_before_admission_checks_old_health_then_deletes_target(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = "cleanup_candidate"
    with pytest.raises(SimulatedCrash):
        await service.activate(value)

    receipt = await service.cancel(value)

    assert receipt.state == "cancelled"
    assert receipt.effects_admitted is False
    assert effects.calls[-3:] == ["start_source", "source_health", "delete_target"]


@pytest.mark.parametrize("crash_at", ["start_source", "source_health", "delete_target"])
async def test_cancel_is_durable_and_replays_only_cancel_after_each_effect(
    tmp_path: Path, crash_at: str
):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = "cleanup_candidate"
    with pytest.raises(SimulatedCrash):
        await service.activate(value)

    effects.crash_after_once = crash_at
    with pytest.raises(SimulatedCrash):
        await service.cancel(value)

    interrupted = await service.status(value)
    assert interrupted.state == "cancelling"
    assert interrupted.effects_admitted is False

    recovered = await service.recover(value)
    assert recovered.state == "cancelled"
    assert effects.counts["start_target"] == 0
    assert effects.counts["observe"] == 1


async def test_old_health_failure_never_deletes_prepared_target(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = "stop_source"
    with pytest.raises(SimulatedCrash):
        await service.activate(value)
    effects.fail_once = "source_health"

    with pytest.raises(RuntimeError, match="source_health"):
        await service.cancel(value)

    assert "delete_target" not in effects.calls
    assert (await service.status(value)).state == "cancelling"


async def test_changed_envelope_and_stale_fence_conflict_without_mutation(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = "prepare_target"
    with pytest.raises(SimulatedCrash):
        await service.activate(value)
    call_count = len(effects.calls)

    changed = value.model_copy(update={"proof_digest": "e" * 64})
    with pytest.raises(CellIdentityConflict, match="envelope mismatch"):
        await service.activate(changed)
    assert len(effects.calls) == call_count

    stale_value = request(operation_id=UUID(int=20), activation_id=UUID(int=21))
    stale_service, stale_effects = engine(tmp_path, stale_value)

    async def stale(_value: RestorationAdaptationActivationRequest) -> ActivationObservedIdentity:
        return ActivationObservedIdentity.from_request(stale_value).model_copy(
            update={"source_fencing_epoch": stale_value.expected_source_fencing_epoch + 1}
        )

    stale_effects.observe_bound_identities = stale
    with pytest.raises(CellIdentityConflict, match="identity or fence changed"):
        await stale_service.activate(stale_value)
    assert stale_effects.counts["prepare_target"] == 0


@pytest.mark.parametrize("protected_field", ["source_code_volume", "live_database_volume"])
async def test_deterministic_target_volume_alias_is_rejected_before_effects(
    tmp_path: Path, protected_field: str
):
    value = request()
    target_volume = (
        f"omnia-machine-{value.source_workspace_id.hex}-code-{value.activation_id.hex}"
    )
    aliased = value.model_copy(update={protected_field: target_volume})
    service, effects = engine(tmp_path, aliased)

    with pytest.raises(CellIdentityConflict, match="target code volume aliases protected volume"):
        await service.activate(aliased)

    assert effects.calls == []


async def test_activation_id_is_immutably_bound_to_one_operation_envelope(tmp_path: Path):
    first = request()
    first_service, first_effects = engine(tmp_path, first)
    first_effects.crash_once = "prepare_target"
    with pytest.raises(SimulatedCrash):
        await first_service.activate(first)

    reused = request(operation_id=UUID(int=30), activation_id=first.activation_id)
    reused_service, reused_effects = engine(tmp_path, reused)
    with pytest.raises(CellIdentityConflict, match="activation envelope mismatch"):
        await reused_service.activate(reused)

    assert reused_effects.calls == []


async def test_concurrent_activation_id_reuse_is_serialized_before_effects(tmp_path: Path):
    first = request()
    first_service, first_effects = engine(tmp_path, first)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_observe = first_effects.observe_bound_identities

    async def blocking_observe(
        value: RestorationAdaptationActivationRequest,
    ) -> ActivationObservedIdentity:
        entered.set()
        await release.wait()
        return await original_observe(value)

    first_effects.observe_bound_identities = blocking_observe
    reused = request(operation_id=UUID(int=31), activation_id=first.activation_id)
    reused_service, reused_effects = engine(tmp_path, reused)

    first_task = asyncio.create_task(first_service.activate(first))
    await entered.wait()
    reused_task = asyncio.create_task(reused_service.activate(reused))
    await asyncio.sleep(0.05)
    assert not reused_task.done()

    release.set()
    assert (await first_task).state == "activated"
    with pytest.raises(CellIdentityConflict, match="activation envelope mismatch"):
        await reused_task
    assert reused_effects.calls == []


async def test_tampered_target_identity_is_rejected_before_target_start(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = "stop_source"
    with pytest.raises(SimulatedCrash):
        await service.activate(value)
    path = service._journal_path(value)
    journal = json.loads(path.read_text(encoding="utf-8"))
    journal["target"]["database_volume"] = "foreign-business-db"
    path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(CellIdentityConflict, match="target or live database identity changed"):
        await service.recover(value)

    assert effects.counts["start_target"] == 0


async def test_status_rejects_changed_operation_binding(tmp_path: Path):
    value = request()
    service, effects = engine(tmp_path, value)
    effects.crash_once = "prepare_target"
    with pytest.raises(SimulatedCrash):
        await service.activate(value)
    path = service._operation_path(value.operation_id)
    binding = json.loads(path.read_text(encoding="utf-8"))
    binding["activation_digest"] = "f" * 64
    path.write_text(json.dumps(binding), encoding="utf-8")

    with pytest.raises(CellIdentityConflict, match="envelope mismatch"):
        await service.status(value)


async def test_recover_all_uses_bound_request_and_skips_completed_effects(tmp_path: Path):
    first = request()
    first_service, first_effects = engine(tmp_path, first)
    first_effects.crash_once = "start_target"
    with pytest.raises(SimulatedCrash):
        await first_service.activate(first)

    result = await first_service.recover_all()

    assert [receipt.state for receipt in result.successes] == ["activated"]
    assert result.failures == ()
    assert first_effects.counts["start_target"] == 2


@pytest.mark.parametrize(
    ("failure_kind", "error_code", "retryable"),
    [
        ("corrupt", "invalid_journal", False),
        ("stale", "identity_conflict", False),
        ("transient", "recovery_failed", True),
    ],
)
async def test_recover_all_isolates_failure_and_completes_later_admitted_journal(
    tmp_path: Path, failure_kind: str, error_code: str, retryable: bool
):
    first = request()
    service, effects = engine(tmp_path, first)
    if failure_kind == "transient":
        effects.crash_once = "start_target"
    else:
        effects.crash_once = "prepare_target"
    with pytest.raises(SimulatedCrash):
        await service.activate(first)

    if failure_kind == "corrupt":
        service._journal_path(first).write_text("{", encoding="utf-8")
    elif failure_kind == "stale":
        binding_path = service._operation_path(first.operation_id)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["activation_digest"] = "f" * 64
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
    else:
        effects.fail_once = "start_target"

    second = request(operation_id=UUID(int=40), activation_id=UUID(int=41))
    (service.offer_artifacts_root / f"{second.activation_id}.tar").write_bytes(
        b"sealed-candidate-code"
    )
    effects.crash_once = "start_target"
    with pytest.raises(SimulatedCrash):
        await service.activate(second)

    result = await service.recover_all()

    assert [receipt.operation_id for receipt in result.successes] == [second.operation_id]
    assert len(result.failures) == 1
    assert result.failures[0].operation_id == str(first.operation_id)
    assert result.failures[0].activation_id == str(first.activation_id)
    assert result.failures[0].error_code == error_code
    assert result.failures[0].retryable is retryable
    assert (await service.status(second)).state == "activated"
    assert effects.counts["start_source"] == 0
    assert effects.counts["delete_target"] == 0

    if failure_kind == "transient":
        retry = await service.recover_all()
        assert {receipt.operation_id for receipt in retry.successes} == {
            first.operation_id,
            second.operation_id,
        }
        assert retry.failures == ()


def test_durable_mkdir_syncs_each_new_ancestor_in_parent_order(tmp_path: Path):
    syncs: list[Path] = []
    target = tmp_path / "root" / "operation" / "activation"

    _durable_mkdir(target, managed_root=tmp_path, sync_directory=syncs.append)

    assert syncs == [tmp_path, target.parent.parent, target.parent]
    assert target.is_dir()


def test_durable_mkdir_never_syncs_or_creates_outside_managed_root(tmp_path: Path):
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    syncs: list[Path] = []

    _durable_mkdir(
        managed_root / "operation" / "activation",
        managed_root=managed_root,
        sync_directory=syncs.append,
    )

    assert syncs == [managed_root, managed_root / "operation"]
    with pytest.raises(CellIdentityConflict, match="leaves managed root"):
        _durable_mkdir(
            tmp_path / "outside",
            managed_root=managed_root,
            sync_directory=syncs.append,
        )
    assert not (tmp_path / "outside").exists()


def test_durable_mkdir_retries_parent_sync_after_power_loss(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "operation"
    calls = 0

    def power_loss_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        assert path == root
        if calls == 1:
            raise SimulatedCrash("directory sync")

    with pytest.raises(SimulatedCrash):
        _durable_mkdir(target, managed_root=root, sync_directory=power_loss_once)
    assert target.is_dir()

    _durable_mkdir(target, managed_root=root, sync_directory=power_loss_once)
    assert calls == 2


async def test_fresh_engine_replays_nested_root_publication_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    base = tmp_path / "base"
    base.mkdir()
    new_parent = base / "new-parent"
    activation_root = new_parent / "activation-root"
    value = request()
    first_effects = Effects(value)
    offer_artifacts = base / "offer-artifacts"
    offer_artifacts.mkdir()
    (offer_artifacts / f"{value.activation_id}.tar").write_bytes(
        b"sealed-candidate-code"
    )

    def fail_first_parent_sync(path: Path) -> None:
        assert path == base
        raise SimulatedCrash("base directory sync")

    monkeypatch.setattr(
        RestorationAdaptationActivationEngine,
        "_fsync_directory",
        staticmethod(fail_first_parent_sync),
    )
    with pytest.raises(SimulatedCrash):
        RestorationAdaptationActivationEngine(
            root=activation_root,
            offer_artifacts_root=offer_artifacts,
            managed_root=base,
            effects=first_effects,
        )
    assert new_parent.is_dir()
    assert not activation_root.exists()
    assert first_effects.calls == []

    events: list[tuple[str, Path] | tuple[str]] = []

    def record_sync(path: Path) -> None:
        events.append(("sync", path))

    retry_effects = Effects(value)
    original_observe = retry_effects.observe_bound_identities

    async def record_observe(
        activation: RestorationAdaptationActivationRequest,
    ) -> ActivationObservedIdentity:
        events.append(("observe",))
        return await original_observe(activation)

    retry_effects.observe_bound_identities = record_observe
    monkeypatch.setattr(
        RestorationAdaptationActivationEngine,
        "_fsync_directory",
        staticmethod(record_sync),
    )
    fresh = RestorationAdaptationActivationEngine(
        root=activation_root,
        offer_artifacts_root=offer_artifacts,
        managed_root=base,
        effects=retry_effects,
    )

    assert (await fresh.activate(value)).state == "activated"

    observe_index = events.index(("observe",))
    before_effect = events[:observe_index]
    base_sync = before_effect.index(("sync", base))
    parent_sync = before_effect.index(("sync", new_parent), base_sync + 1)
    root_sync = before_effect.index(("sync", activation_root), parent_sync + 1)
    assert base_sync < parent_sync < root_sync < observe_index


async def test_directory_sync_failure_prevents_external_effects_and_retry_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    value = request()
    activation_root = tmp_path / "activation-root"
    service, effects = engine(activation_root, value)
    original_sync = service._fsync_directory
    failed = False

    def fail_operation_parent_once(path: Path) -> None:
        nonlocal failed
        if path == activation_root and not failed:
            failed = True
            raise SimulatedCrash("operation directory sync")
        original_sync(path)

    monkeypatch.setattr(service, "_fsync_directory", fail_operation_parent_once)
    with pytest.raises(SimulatedCrash):
        await service.activate(value)
    assert effects.calls == []

    monkeypatch.setattr(service, "_fsync_directory", original_sync)
    assert (await service.activate(value)).state == "activated"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync only")
def test_posix_directory_sync_accepts_real_directory(tmp_path: Path):
    target = tmp_path / "managed" / "activation"
    _durable_mkdir(
        target,
        managed_root=tmp_path,
        sync_directory=RestorationAdaptationActivationEngine._fsync_directory,
    )
    assert target.is_dir()
