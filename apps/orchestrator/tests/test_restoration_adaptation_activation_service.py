import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationPrepare,
    RestorationAdaptationProof,
)
from yleum_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    ActivationObservedIdentity,
    ActivationPreparedTarget,
    RestorationAdaptationActivationCommand,
    RestorationAdaptationActivationOfferRequest,
    RestorationAdaptationActivationReceipt,
    RestorationAdaptationActivationRecoveryBatch,
    RestorationAdaptationActivationRequest,
)
from yleum_orchestrator.services.restoration_adaptation_activation_service import (
    ActivationOfferMaterialization,
    RestorationAdaptationActivationService,
)
from yleum_orchestrator.services.restoration_adaptation_workspace import (
    RestorationAdaptationWorkspaceService,
)


def offer_request(**updates: object) -> RestorationAdaptationActivationOfferRequest:
    return RestorationAdaptationActivationOfferRequest(
        **{
            "workspace_id": UUID(int=1),
            "operation_id": UUID(int=2),
            "project_id": UUID(int=3),
            "owner_id": UUID(int=4),
            "generation_run_id": UUID(int=5),
            "candidate_workspace_id": UUID(int=6),
            "candidate_fencing_epoch": 7,
            "candidate_workspace_revision": "a" * 64,
            "proof_attempt": 2,
            "proof_digest": "b" * 64,
            **updates,
        }
    )


def proof(value: RestorationAdaptationActivationOfferRequest) -> RestorationAdaptationProof:
    return RestorationAdaptationProof(
        state="proof_ready",
        reason_code=None,
        source_workspace_id=value.workspace_id,
        candidate_workspace_id=value.candidate_workspace_id,
        operation_id=value.operation_id,
        project_id=value.project_id,
        owner_id=value.owner_id,
        generation_run_id=value.generation_run_id,
        candidate_fencing_epoch=value.candidate_fencing_epoch,
        proof_attempt=value.proof_attempt,
        source_workspace_revision="c" * 64,
        candidate_workspace_revision=value.candidate_workspace_revision,
        candidate_proof_key="d" * 64,
        candidate_artifact_digest="e" * 64,
        candidate_source_manifest_digest="8" * 64,
        probe_contract_digest=business_probe().contract_digest,
        probe_rehearsal_digest="9" * 64,
        probe_rehearsal_database_digest="a" * 64,
        source_database_digest="f" * 64,
        candidate_database_digest="f" * 64,
        source_schema_digest="1" * 64,
        candidate_schema_digest="1" * 64,
        source_business_digest="2" * 64,
        candidate_business_digest="2" * 64,
        source_technical_digest="3" * 64,
        candidate_technical_digest="3" * 64,
        proof_digest=value.proof_digest,
        capabilities={
            "portable_machine": True,
            "database_admin": "isolated_copy",
            "restoration_adaptation_database_copy_v1": True,
            "restoration_adaptation_proof_v1": True,
        },
    )


def business_probe() -> ActivationBusinessProbe:
    payload = {
        "version": 1,
        "endpoint": "/api/tasks",
        "data_contract_digest": "a" * 64,
        "witnesses": [
            {
                "entity": "tasks",
                "id_column": "id",
                "owner_column": "owner_id",
                "value_column": "marker",
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
    import hashlib
    import json

    payload["contract_digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ActivationBusinessProbe.model_validate(payload)


class WorkspaceProofStore:
    def __init__(self, value: RestorationAdaptationProof) -> None:
        self.value = value
        self.claims: list[UUID] = []
        self.validations = 0
        self.terminal: list[str] = []
        self.expired = False
        self.orphan_recoveries: list[frozenset[UUID]] = []
        self.seal_failures = 0
        self.release_failures = 0

    async def claim_activation_offer(self, request, activation_id):
        if self.expired:
            raise CellIdentityConflict("restoration adaptation proof expired")
        self.claims.append(activation_id)
        return self.value

    async def seal_activation_offer(self, request, activation_id, offer_digest):
        if self.seal_failures:
            self.seal_failures -= 1
            raise RuntimeError("seal crash")
        return None

    async def release_activation_offer(self, request, activation_id, *, terminal_state):
        if self.release_failures:
            self.release_failures -= 1
            raise RuntimeError("release crash")
        self.terminal.append(terminal_state)

    async def validate_activation_offer(self, request, activation_id, offer_digest):
        self.validations += 1
        if self.expired:
            raise CellIdentityConflict("restoration adaptation proof expired")
        return self.value

    async def abort_activation_offer(self, request, activation_id):
        self.terminal.append("aborted")

    async def recover_activation_orphans(self, protected_activation_ids):
        self.orphan_recoveries.append(protected_activation_ids)
        return protected_activation_ids


class OfferAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.payload = b"sealed candidate"

    async def materialize(
        self, request, saved_proof, source_fencing_epoch, activation_id, destination
    ):
        self.calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)
        return ActivationOfferMaterialization(
            expected_source_fencing_epoch=8,
            target_fencing_epoch=9,
            source_workspace_revision=saved_proof.source_workspace_revision,
            source_code_volume="source-code",
            live_database_volume="live-database",
            live_database_identity_digest="4" * 64,
            candidate_artifact_digest=saved_proof.candidate_artifact_digest,
            candidate_source_manifest_digest=saved_proof.candidate_source_manifest_digest,
            candidate_files_digest="5" * 64,
            archive_digest=__import__("hashlib").sha256(self.payload).hexdigest(),
            business_probe=business_probe(),
            probe_contract_digest=business_probe().contract_digest,
            probe_rehearsal_digest=saved_proof.probe_rehearsal_digest or "9" * 64,
            probe_rehearsal_database_digest=(
                saved_proof.probe_rehearsal_database_digest or "a" * 64
            ),
        )


class ActivationEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.protected: frozenset[UUID] = frozenset()
        self.registry_fails = False

    @staticmethod
    def _receipt(request, state="activated"):
        from yleum_orchestrator.services.restoration_binding import canonical_digest

        source = ActivationPreparedTarget(
            workspace_id=request.source_workspace_id,
            fencing_epoch=request.expected_source_fencing_epoch,
            code_volume=request.source_code_volume,
            code_digest=request.source_workspace_revision,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
        )
        target = source.model_copy(
            update={
                "fencing_epoch": request.target_fencing_epoch,
                "code_volume": "target-code",
                "code_digest": request.candidate_code_digest,
            }
        )
        draft = RestorationAdaptationActivationReceipt(
            state=state,
            effects_admitted=state in {"target_writers_admitted", "activated"},
            operation_id=request.operation_id,
            generation_run_id=request.generation_run_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            activation_id=request.activation_id,
            activation_digest=request.activation_digest,
            proof_digest=request.proof_digest,
            fencing_epoch=request.target_fencing_epoch,
            source_volume_identity=source,
            target_volume_identity=target,
            health_digest="8" * 64 if state == "activated" else None,
            receipt_digest=None,
        )
        return draft.model_copy(
            update={
                "receipt_digest": canonical_digest(
                    draft.model_dump(mode="json", exclude={"receipt_digest"})
                )
            }
        )

    async def activate(self, request):
        self.calls.append(("activate", request))
        return self._receipt(request)

    async def status(self, request):
        self.calls.append(("status", request))
        return self._receipt(request)

    async def cancel(self, request):
        self.calls.append(("cancel", request))
        return self._receipt(request, state="cancelled")

    async def recover(self, request):
        self.calls.append(("recover", request))
        return self._receipt(request)

    async def recover_all(self):
        return RestorationAdaptationActivationRecoveryBatch(successes=(), failures=())

    def protected_activation_ids(self):
        if self.registry_fails:
            raise RuntimeError("registry unavailable")
        return self.protected


class WorkspaceCleanupEngine:
    def __init__(self) -> None:
        self.cleanup_calls: list[tuple[UUID, int]] = []

    async def cleanup(self, request, candidate_workspace_id, candidate_fencing_epoch):
        self.cleanup_calls.append((candidate_workspace_id, candidate_fencing_epoch))


class ComponentEffects:
    def __init__(self) -> None:
        self.prepared_artifacts: list[bytes] = []

    @asynccontextmanager
    async def hold_transition(self, request):
        yield

    async def observe_bound_identities(self, request):
        return ActivationObservedIdentity.from_request(request)

    async def prepare_target_code(self, request, sealed_artifact, target_code_volume):
        self.prepared_artifacts.append(sealed_artifact.read_bytes())
        return ActivationPreparedTarget(
            workspace_id=request.source_workspace_id,
            fencing_epoch=request.target_fencing_epoch,
            code_volume=target_code_volume,
            code_digest=request.candidate_code_digest,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
        )

    async def verify_prepared_target(self, request, target):
        return "8" * 64

    async def cleanup_candidate(self, request, sealed_artifact):
        assert sealed_artifact.read_bytes() == self.prepared_artifacts[-1]

    async def stop_source_writers(self, request):
        return None

    async def start_target_writers(self, request, target):
        return None

    async def start_source_writers(self, request):
        return None

    async def verify_source_health(self, request):
        return "1" * 64

    async def delete_target_code(self, request, target):
        return None

    async def verify_service_readiness(self, request):
        return "2" * 64

    async def verify_signed_owner_read(self, request):
        return "3" * 64

    async def verify_signed_owner_write(self, request):
        return "4" * 64

    async def verify_signed_owner_reload(self, request):
        return "5" * 64

    async def verify_cross_owner_denial(self, request):
        return "6" * 64

    async def verify_unauthenticated_denial(self, request):
        return "7" * 64


def service(tmp_path: Path, store=None, adapter=None, engine=None):
    request = offer_request()
    return RestorationAdaptationActivationService(
        root=tmp_path,
        workspace_service=store or WorkspaceProofStore(proof(request)),
        offer_adapter=adapter or OfferAdapter(),
        activation_engine=engine or ActivationEngine(),
    )


def component_service(
    root: Path,
    *,
    store: WorkspaceProofStore,
    adapter: OfferAdapter,
    effects: ComponentEffects,
) -> RestorationAdaptationActivationService:
    from yleum_orchestrator.services.restoration_adaptation_activation import (
        RestorationAdaptationActivationEngine,
    )

    RestorationAdaptationActivationService._prepare_root(root)
    engine = RestorationAdaptationActivationEngine(
        root=root / "engine",
        offer_artifacts_root=root / "offer-artifacts",
        effects=effects,
        managed_root=root,
    )
    return RestorationAdaptationActivationService(
        root=root,
        workspace_service=store,
        offer_adapter=adapter,
        activation_engine=engine,
    )


async def test_offer_is_stable_and_different_binding_conflicts(tmp_path: Path):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    adapter = OfferAdapter()
    activation = service(tmp_path, store, adapter)

    first = await activation.offer(value)
    second = await activation.offer(value)

    assert second == first
    assert adapter.calls == 1
    assert len(store.claims) == 1
    assert first.source_workspace_revision == "c" * 64
    assert first.candidate_files_digest == "5" * 64
    assert first.archive_digest == __import__("hashlib").sha256(b"sealed candidate").hexdigest()
    assert first.probe_contract_digest == business_probe().contract_digest
    with pytest.raises(CellIdentityConflict, match="offer envelope mismatch"):
        await activation.offer(value.model_copy(update={"proof_attempt": 3}))


async def test_apply_reloads_controller_proof_and_binds_planned_commit(tmp_path: Path):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    engine = ActivationEngine()
    activation = service(tmp_path, store, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )

    receipt = await activation.apply(command)

    assert receipt.state == "activated"
    assert receipt.offer == offered
    assert receipt.planned_commit_sha == "a" * 40
    assert store.validations == 1
    driven = engine.calls[0][1]
    assert driven.planned_commit_sha == "a" * 40
    assert driven.activation_digest == receipt.activation_digest

    changed = command.model_copy(update={"planned_commit_sha": "b" * 40})
    with pytest.raises(CellIdentityConflict, match="activation envelope mismatch"):
        await activation.apply(changed)


async def test_activation_digest_is_canonical_and_rejects_substitution(tmp_path: Path):
    from yleum_orchestrator.services.restoration_binding import canonical_digest

    activation = service(tmp_path)
    offered = await activation.offer(offer_request())
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    request = activation._engine_request(command)

    assert request.activation_digest == canonical_digest(request.activation_binding_payload())
    changed = request.model_dump(mode="json")
    changed["activation_digest"] = "f" * 64
    with pytest.raises(ValueError, match="binding digest mismatch"):
        RestorationAdaptationActivationRequest.model_validate(changed)


async def test_apply_rejects_expired_saved_proof_before_engine_admission(tmp_path: Path):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    engine = ActivationEngine()
    activation = service(tmp_path, store, engine=engine)
    offered = await activation.offer(value)
    store.expired = True

    with pytest.raises(CellIdentityConflict, match="proof expired"):
        await activation.apply(
            RestorationAdaptationActivationCommand(
                offer=offered,
                planned_commit_sha="a" * 40,
            )
        )
    assert engine.calls == []


async def test_cancel_tombstone_serializes_with_delayed_apply(tmp_path: Path):
    value = offer_request()
    engine = ActivationEngine()
    activation = service(tmp_path, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )

    cancelled = await activation.cancel(command)
    replay = await activation.cancel(command)

    assert cancelled.state == replay.state == "cancelled"
    assert [name for name, _request in engine.calls] == ["cancel"]
    with pytest.raises(CellIdentityConflict, match="cancelled"):
        await activation.apply(command)


async def test_recovery_isolates_bad_journal_from_valid_activation(tmp_path: Path):
    first_value = offer_request()
    engine = ActivationEngine()
    activation = service(tmp_path, engine=engine)
    first_offer = await activation.offer(first_value)
    command = RestorationAdaptationActivationCommand(
        offer=first_offer,
        planned_commit_sha="a" * 40,
    )
    await activation._record_command(command)
    bad = tmp_path / "offers" / str(UUID(int=99)) / str(UUID(int=100)) / "offer.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("not-json", encoding="utf-8")

    recovered = await activation.recover_all()

    assert len(recovered.successes) == 1
    assert len(recovered.failures) == 1
    assert recovered.failures[0].error_code == "invalid_journal"
    assert [name for name, _request in engine.calls] == ["activate"]


async def test_artifact_gc_keeps_old_journal_bound_offer_and_removes_orphan(
    tmp_path: Path,
):
    activation = service(tmp_path)
    offered = await activation.offer(offer_request())
    bound = activation.artifacts_root / f"{offered.activation_id}.tar"
    orphan_id = UUID(int=77)
    orphan = activation.artifacts_root / f"{orphan_id}.tar"
    orphan.write_bytes(b"orphan")
    old = time.time() - 25 * 60 * 60
    os.utime(bound, (old, old))
    os.utime(orphan, (old, old))

    removed = activation.gc_artifacts()

    assert bound.is_file()
    assert not orphan.exists()
    assert removed == (orphan.name,)


async def test_corrupt_facade_journal_reclaims_expired_unprotected_artifact(
    tmp_path: Path,
):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    engine = ActivationEngine()
    activation = service(tmp_path, store=store, engine=engine)
    offered = await activation.offer(value)
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    old = time.time() - 25 * 60 * 60
    os.utime(artifact, (old, old))
    journal = activation._path(value.workspace_id, value.generation_run_id)
    journal.write_text("not-json", encoding="utf-8")

    recovered = await activation.recover_all()

    assert len(recovered.failures) == 1
    assert recovered.failures[0].error_code == "invalid_journal"
    assert store.orphan_recoveries == [frozenset()]
    assert not artifact.exists()


async def test_structurally_invalid_facade_journal_cannot_pin_artifact_forever(
    tmp_path: Path,
):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    activation = service(tmp_path, store=store)
    offered = await activation.offer(value)
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    old = time.time() - 25 * 60 * 60
    os.utime(artifact, (old, old))
    activation._path(value.workspace_id, value.generation_run_id).write_text(
        json.dumps({"activation_id": str(offered.activation_id), "state": "offered"}),
        encoding="utf-8",
    )

    recovered = await activation.recover_all()

    assert len(recovered.failures) == 1
    assert recovered.failures[0].error_code == "invalid_journal"
    assert not artifact.exists()


async def test_corrupt_facade_journal_keeps_artifact_bound_to_active_engine(
    tmp_path: Path,
):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    engine = ActivationEngine()
    activation = service(tmp_path, store=store, engine=engine)
    offered = await activation.offer(value)
    engine.protected = frozenset({offered.activation_id})
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    old = time.time() - 25 * 60 * 60
    os.utime(artifact, (old, old))
    activation._path(value.workspace_id, value.generation_run_id).write_text(
        "not-json", encoding="utf-8"
    )

    await activation.recover_all()

    assert store.orphan_recoveries == [frozenset({offered.activation_id})]
    assert artifact.is_file()


async def test_engine_registry_failure_skips_workspace_and_artifact_gc(
    tmp_path: Path,
):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    engine = ActivationEngine()
    activation = service(tmp_path, store=store, engine=engine)
    offered = await activation.offer(value)
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    old = time.time() - 25 * 60 * 60
    os.utime(artifact, (old, old))
    activation._path(value.workspace_id, value.generation_run_id).write_text(
        "not-json", encoding="utf-8"
    )
    engine.registry_fails = True

    recovered = await activation.recover_all()

    assert any(
        failure.operation_id == "engine"
        and failure.activation_id == "activation-registry"
        and failure.retryable is True
        for failure in recovered.failures
    )
    assert store.orphan_recoveries == []
    assert artifact.is_file()


async def test_corrupt_facade_journal_reclaims_real_expired_workspace_pin(
    tmp_path: Path,
):
    value = offer_request()
    value = value.model_copy(
        update={
            "candidate_workspace_id": uuid5(
                value.operation_id,
                f"restoration-adaptation:{value.generation_run_id}",
            )
        }
    )
    saved_proof = proof(value)
    prepare = RestorationAdaptationPrepare(
        workspace_id=value.workspace_id,
        operation_id=value.operation_id,
        project_id=value.project_id,
        owner_id=value.owner_id,
        generation_run_id=value.generation_run_id,
        fencing_epoch=8,
        source_workspace_revision=saved_proof.source_workspace_revision,
        source_snapshot_id=UUID(int=20),
        base_draft_snapshot_id=UUID(int=21),
        source_commit_sha="a" * 40,
        adaptation_bundle_digest="b" * 64,
    )
    cleanup_engine = WorkspaceCleanupEngine()
    workspace = RestorationAdaptationWorkspaceService(
        root=tmp_path / "workspace",
        engine=cleanup_engine,
    )
    workspace._write(
        {
            "version": 1,
            "workspace_id": str(value.workspace_id),
            "generation_run_id": str(value.generation_run_id),
            "request": prepare.model_dump(mode="json"),
            "request_digest": prepare.digest(),
            "candidate_workspace_id": str(value.candidate_workspace_id),
            "candidate_fencing_epoch": value.candidate_fencing_epoch,
            "state": "proof_ready",
            "owner_run_status": "active",
            "proof_result": saved_proof.model_dump(mode="json"),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )
    activation = RestorationAdaptationActivationService(
        root=tmp_path / "activation",
        workspace_service=workspace,
        offer_adapter=OfferAdapter(),
        activation_engine=ActivationEngine(),
    )
    offered = await activation.offer(value)
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    current = workspace._read(value.workspace_id, value.generation_run_id)
    assert current is not None
    workspace._write(
        {
            **current,
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )
    old = time.time() - 25 * 60 * 60
    os.utime(artifact, (old, old))
    activation._path(value.workspace_id, value.generation_run_id).write_text(
        "not-json", encoding="utf-8"
    )

    recovered = await activation.recover_all()

    assert len(recovered.failures) == 1
    assert recovered.failures[0].error_code == "invalid_journal"
    assert cleanup_engine.cleanup_calls == [
        (value.candidate_workspace_id, value.candidate_fencing_epoch)
    ]
    assert (
        await workspace.status(value.workspace_id, value.generation_run_id) == "failed"
    )
    assert not artifact.exists()


async def test_recovery_expires_offer_before_removing_exact_sealed_artifact(
    tmp_path: Path,
):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    activation = service(tmp_path, store=store)
    offered = await activation.offer(value)
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    assert artifact.is_file()
    store.expired = True

    recovered = await activation.recover_all()

    assert recovered.successes == ()
    assert recovered.failures == ()
    assert not artifact.exists()
    assert store.terminal == ["aborted"]
    saved = activation._read(value.workspace_id, value.generation_run_id)
    assert saved is not None
    assert saved["state"] == "offer_aborted"


async def test_expired_offer_cleanup_replays_from_durable_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    activation = service(tmp_path, store=store)
    offered = await activation.offer(value)
    artifact = activation.artifacts_root / f"{offered.activation_id}.tar"
    store.expired = True
    original = activation._delete_offer_artifact
    attempts = 0

    def fail_once(saved_offer):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected cleanup failure")
        original(saved_offer)

    monkeypatch.setattr(activation, "_delete_offer_artifact", fail_once)

    first = await activation.recover_all()
    saved = activation._read(value.workspace_id, value.generation_run_id)
    assert saved is not None
    assert saved["state"] == "offer_aborted"
    assert artifact.is_file()
    assert len(first.failures) == 1
    assert first.failures[0].retryable is True

    second = await activation.recover_all()
    assert second.failures == ()
    assert not artifact.exists()
    assert store.terminal == ["aborted"]


async def test_apply_and_cancel_share_one_workspace_serialization_boundary(tmp_path: Path):
    value = offer_request()
    engine = ActivationEngine()
    activation = service(tmp_path, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_activate(request):
        entered.set()
        await release.wait()
        return engine._receipt(request)

    engine.activate = delayed_activate
    applying = asyncio.create_task(activation.apply(command))
    await entered.wait()
    cancelling = asyncio.create_task(activation.cancel(command))
    await asyncio.sleep(0)
    assert not cancelling.done()
    release.set()
    assert (await applying).state == "activated"
    with pytest.raises(CellIdentityConflict, match="target writer admission"):
        await cancelling


async def test_offer_seal_crash_replays_same_export_without_unpinning(tmp_path: Path):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    store.seal_failures = 1
    adapter = OfferAdapter()
    activation = service(tmp_path, store=store, adapter=adapter)

    with pytest.raises(RuntimeError, match="seal crash"):
        await activation.offer(value)

    saved = activation._read(value.workspace_id, value.generation_run_id)
    assert saved is not None
    assert saved["state"] == "offer_sealing"
    assert store.terminal == []
    offered = await activation.offer(value)
    assert adapter.calls == 1
    assert (activation.artifacts_root / f"{offered.activation_id}.tar").read_bytes() == (
        b"sealed candidate"
    )


async def test_terminal_release_crash_replays_release_without_rerunning_engine(tmp_path: Path):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    store.release_failures = 1
    engine = ActivationEngine()
    activation = service(tmp_path, store=store, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )

    with pytest.raises(RuntimeError, match="release crash"):
        await activation.apply(command)

    replay = await activation.apply(command)
    assert replay.state == "activated"
    assert [name for name, _request in engine.calls] == ["activate"]
    assert store.terminal == ["activated"]
    saved = activation._read(value.workspace_id, value.generation_run_id)
    assert saved is not None and saved["workspace_released"] is True


async def test_cancelling_intent_is_sticky_after_engine_crash(tmp_path: Path):
    value = offer_request()
    engine = ActivationEngine()
    activation = service(tmp_path, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    original_cancel = engine.cancel
    calls = 0

    async def crash_once(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cancel crash")
        return await original_cancel(request)

    engine.cancel = crash_once
    with pytest.raises(RuntimeError, match="cancel crash"):
        await activation.cancel(command)
    with pytest.raises(CellIdentityConflict, match="cancelled"):
        await activation.apply(command)

    recovered = await activation.recover_all()
    assert len(recovered.successes) == 1
    assert recovered.successes[0].state == "cancelled"
    assert calls == 2


async def test_status_reissues_durable_cancel_instead_of_persisting_stale_engine_intent(
    tmp_path: Path,
) -> None:
    value = offer_request()
    engine = ActivationEngine()
    activation = service(tmp_path, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    original_cancel = engine.cancel
    cancel_calls = 0

    async def crash_once(request):
        nonlocal cancel_calls
        cancel_calls += 1
        if cancel_calls == 1:
            raise RuntimeError("cancel crash before engine tombstone")
        return await original_cancel(request)

    async def stale_status(request):
        engine.calls.append(("status", request))
        return engine._receipt(request, state="intent")

    engine.cancel = crash_once
    engine.status = stale_status
    with pytest.raises(RuntimeError, match="cancel crash before engine tombstone"):
        await activation.cancel(command)

    status = await activation.status(command)

    assert status.state == "cancelled"
    assert cancel_calls == 2
    assert [name for name, _request in engine.calls] == ["cancel"]
    with pytest.raises(CellIdentityConflict, match="cancelled"):
        await activation.apply(command)


async def test_recovery_cas_does_not_overwrite_changed_facade_journal(tmp_path: Path):
    value = offer_request()
    engine = ActivationEngine()
    activation = service(tmp_path, engine=engine)
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    await activation._record_command(command)

    async def mutate_during_activate(request):
        saved = activation._read(value.workspace_id, value.generation_run_id)
        assert saved is not None
        activation._write(
            value.workspace_id,
            value.generation_run_id,
            {**saved, "journal_revision": saved["journal_revision"] + 1, "tampered": True},
        )
        return engine._receipt(request)

    engine.activate = mutate_during_activate
    recovered = await activation.recover_all()

    assert recovered.successes == ()
    assert recovered.failures[0].error_code == "identity_conflict"
    saved = activation._read(value.workspace_id, value.generation_run_id)
    assert saved is not None and saved["tampered"] is True and saved["receipt"] is None


async def test_real_engine_recovers_missing_journal_from_exact_offer_archive(tmp_path: Path):
    value = offer_request()
    store = WorkspaceProofStore(proof(value))
    adapter = OfferAdapter()
    effects = ComponentEffects()
    root = tmp_path / "fresh" / "restoration-adaptation-activations"
    activation = component_service(root, store=store, adapter=adapter, effects=effects)
    offered = await activation.offer(value)
    adapter.payload = b"mutated candidate cache"
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    await activation._record_command(command)
    engine_journal = (
        root
        / "engine"
        / str(value.operation_id)
        / str(offered.activation_id)
        / "activation.json"
    )
    assert not engine_journal.exists()

    restarted = component_service(root, store=store, adapter=adapter, effects=effects)
    recovered = await restarted.recover_all()

    assert len(recovered.successes) == 1
    assert recovered.successes[0].state == "activated"
    assert effects.prepared_artifacts == [b"sealed candidate"]
    assert store.terminal == ["activated"]


async def test_internal_activation_routes_require_auth_and_exact_path_binding(
    tmp_path: Path, monkeypatch
):
    import httpx
    from fastapi import FastAPI

    from yleum_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
    from yleum_orchestrator.routers import code_restorations

    activation = service(tmp_path)
    value = offer_request()
    offered = await activation.offer(value)
    command = RestorationAdaptationActivationCommand(
        offer=offered,
        planned_commit_sha="a" * 40,
    )
    monkeypatch.setattr(
        code_restorations,
        "get_restoration_adaptation_activation_service",
        lambda: activation,
    )

    def authenticate(token):
        if token != "internal":
            raise OrchestratorError(code="unauthorized", message="no", status_code=401)

    monkeypatch.setattr(code_restorations, "verify_internal_token", authenticate)
    app = FastAPI()
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.include_router(code_restorations.router)
    base = (
        f"/internal/workspaces/{value.workspace_id}/restoration-adaptations/"
        f"{value.generation_run_id}"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(base + "/activation-offer", json=value.model_dump(mode="json"))
        ).status_code == 401
        client.headers["X-Internal-Token"] = "internal"
        assert (
            await client.post(
                base.replace(str(value.workspace_id), str(UUID(int=99))) + "/activation-offer",
                json=value.model_dump(mode="json"),
            )
        ).status_code == 409
        path = base + f"/activations/{offered.activation_id}"
        applied = await client.post(path + "/apply", json=command.model_dump(mode="json"))
        assert applied.status_code == 200
        assert applied.json()["planned_commit_sha"] == "a" * 40
        assert (
            await client.post(
                base + f"/activations/{UUID(int=99)}/status",
                json=command.model_dump(mode="json"),
            )
        ).status_code == 409
