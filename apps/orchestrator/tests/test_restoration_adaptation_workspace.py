from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationCleanup,
    RestorationAdaptationOwnerStatus,
    RestorationAdaptationPrepare,
    RestorationAdaptationProofRequest,
)
from omnia_orchestrator.schemas.restoration_adaptation_activation import (
    RestorationAdaptationActivationOfferRequest,
)
from omnia_orchestrator.services.restoration_adaptation_workspace import (
    AdaptationProofMaterialization,
    AdaptationWorkspaceMaterialization,
    DockerAdaptationWorkspaceEngine,
    RestorationAdaptationWorkspaceService,
    content_inventory_partition_digests,
)
from omnia_orchestrator.services.versioning.contracts import InventoryObject, InventoryReport
from tests._versioning_pg import pg  # noqa: F401


def _request() -> RestorationAdaptationPrepare:
    return RestorationAdaptationPrepare(
        workspace_id=uuid4(),
        operation_id=uuid4(),
        project_id=uuid4(),
        owner_id=uuid4(),
        generation_run_id=uuid4(),
        fencing_epoch=7,
        source_workspace_revision="0" * 64,
        source_snapshot_id=uuid4(),
        base_draft_snapshot_id=uuid4(),
        source_commit_sha="a" * 40,
        adaptation_bundle_digest="b" * 64,
    )


@dataclass
class _Engine:
    source_digest: str = "1" * 64
    candidate_digest: str = "1" * 64
    prepare_calls: int = 0
    cleanup_calls: int = 0
    prove_calls: int = 0
    cleanup_fails: bool = False
    proof_state: str = "proof_ready"
    proof_reason: str | None = None
    owner_active: bool = False

    async def prepare(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
    ) -> AdaptationWorkspaceMaterialization:
        self.prepare_calls += 1
        return AdaptationWorkspaceMaterialization(
            candidate_workspace_id=candidate_workspace_id,
            candidate_fencing_epoch=1,
            source_database_digest=self.source_digest,
            candidate_database_digest=self.candidate_digest,
            source_schema_digest="2" * 64,
            candidate_schema_digest="2" * 64,
            source_business_digest="3" * 64,
            candidate_business_digest="3" * 64,
            source_technical_digest="4" * 64,
            candidate_technical_digest="4" * 64,
        )

    async def cleanup(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
        candidate_fencing_epoch: int,
    ) -> None:
        self.cleanup_calls += 1
        if self.cleanup_fails:
            raise RuntimeError("cleanup unavailable")

    async def prove(
        self,
        request: RestorationAdaptationPrepare,
        proof: RestorationAdaptationProofRequest,
    ) -> AdaptationProofMaterialization:
        self.prove_calls += 1
        return AdaptationProofMaterialization(
            state=self.proof_state,
            reason_code=self.proof_reason,
            source_workspace_revision=request.source_workspace_revision,
            candidate_workspace_revision=proof.candidate_workspace_revision,
            candidate_proof_key=proof.candidate_proof_key,
            candidate_artifact_digest=proof.candidate_artifact_digest,
            candidate_source_manifest_digest="8" * 64,
            probe_contract_digest="9" * 64,
            probe_rehearsal_digest=("a" * 64 if self.proof_state == "proof_ready" else None),
            probe_rehearsal_database_digest=(
                "1" * 64 if self.proof_state == "proof_ready" else None
            ),
            source_database_digest="1" * 64,
            candidate_database_digest=("1" * 64 if self.proof_state == "proof_ready" else "9" * 64),
            source_schema_digest="2" * 64,
            candidate_schema_digest=(
                "2" * 64 if self.proof_reason != "candidate_schema_changed" else "8" * 64
            ),
            source_business_digest="3" * 64,
            candidate_business_digest=(
                "3" * 64 if self.proof_reason != "candidate_business_data_changed" else "7" * 64
            ),
            source_technical_digest="4" * 64,
            candidate_technical_digest=(
                "4" * 64 if self.proof_reason != "candidate_technical_data_changed" else "6" * 64
            ),
        )

    async def owner_run_active(self, request: RestorationAdaptationPrepare) -> bool:
        return self.owner_active


class _BlockingEngine(_Engine):
    def __init__(self, *, prove_error: bool = False) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release_proof = asyncio.Event()
        self.prove_error = prove_error

    async def prove(
        self,
        request: RestorationAdaptationPrepare,
        proof: RestorationAdaptationProofRequest,
    ) -> AdaptationProofMaterialization:
        self.started.set()
        await self.release_proof.wait()
        if self.prove_error:
            raise RuntimeError("proof failed late")
        return await super().prove(request, proof)


class _BlockingPrepareEngine(_Engine):
    def __init__(self) -> None:
        super().__init__()
        self.prepare_started = asyncio.Event()
        self.release_prepare = asyncio.Event()

    async def prepare(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
    ) -> AdaptationWorkspaceMaterialization:
        self.prepare_started.set()
        await self.release_prepare.wait()
        return await super().prepare(request, candidate_workspace_id)


class _LateCandidateEngine(_BlockingPrepareEngine):
    def __init__(self) -> None:
        super().__init__()
        self.candidate_exists = False
        self.fail_late_cleanup = True
        self.cleanup_epochs: list[int] = []

    async def prepare(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
    ) -> AdaptationWorkspaceMaterialization:
        result = replace(
            await super().prepare(request, candidate_workspace_id),
            candidate_fencing_epoch=9,
        )
        self.candidate_exists = True
        return result

    async def cleanup(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
        candidate_fencing_epoch: int,
    ) -> None:
        self.cleanup_calls += 1
        self.cleanup_epochs.append(candidate_fencing_epoch)
        if self.candidate_exists and self.fail_late_cleanup:
            raise RuntimeError("late cleanup unavailable")
        self.candidate_exists = False


def _inventory() -> InventoryReport:
    return InventoryReport(
        presence="present",
        coverage="complete",
        schema_analysis="complete",
        observed_on="source",
        objects=[
            InventoryObject(
                object="public.items",
                kind="table",
                classification="business",
                presence="present",
                row_count=1,
                count_kind="exact",
            ),
            InventoryObject(
                object="drizzle.__drizzle_migrations",
                kind="table",
                classification="technical",
                presence="present",
                row_count=1,
                count_kind="exact",
            ),
        ],
    )


def _expire(
    service: RestorationAdaptationWorkspaceService,
    request: RestorationAdaptationPrepare,
) -> None:
    saved = service._read(request.workspace_id, request.generation_run_id)
    assert saved is not None
    service._write(
        {
            **saved,
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )


def _proof_request(
    request: RestorationAdaptationPrepare,
    prepared_proof_digest: str,
    *,
    proof_attempt: int = 1,
) -> RestorationAdaptationProofRequest:
    return RestorationAdaptationProofRequest(
        workspace_id=request.workspace_id,
        operation_id=request.operation_id,
        project_id=request.project_id,
        owner_id=request.owner_id,
        generation_run_id=request.generation_run_id,
        candidate_workspace_id=uuid5(
            request.operation_id,
            f"restoration-adaptation:{request.generation_run_id}",
        ),
        candidate_fencing_epoch=1,
        preparation_proof_digest=prepared_proof_digest,
        source_database_digest="1" * 64,
        candidate_workspace_revision="5" * 64,
        candidate_proof_key="6" * 64,
        candidate_artifact_digest="7" * 64,
        proof_attempt=proof_attempt,
    )


def _offer_request(proof) -> RestorationAdaptationActivationOfferRequest:
    return RestorationAdaptationActivationOfferRequest(
        workspace_id=proof.source_workspace_id,
        operation_id=proof.operation_id,
        project_id=proof.project_id,
        owner_id=proof.owner_id,
        generation_run_id=proof.generation_run_id,
        candidate_workspace_id=proof.candidate_workspace_id,
        candidate_fencing_epoch=proof.candidate_fencing_epoch,
        candidate_workspace_revision=proof.candidate_workspace_revision,
        proof_attempt=proof.proof_attempt,
        proof_digest=proof.proof_digest,
    )


@pytest.mark.parametrize("changed_partition", ["business", "technical"])
def test_content_digest_detects_same_count_update_without_returning_rows(
    monkeypatch: pytest.MonkeyPatch,
    changed_partition: str,
) -> None:
    from omnia_orchestrator.services import restoration_adaptation_workspace as module

    calls: list[str] = []
    values = [
        [{"rows": 1, "sha256": "1" * 64}, {"rows": 1, "sha256": "2" * 64}],
        [
            {
                "rows": 1,
                "sha256": "3" * 64 if changed_partition == "technical" else "1" * 64,
            },
            {
                "rows": 1,
                "sha256": "4" * 64 if changed_partition == "business" else "2" * 64,
            },
        ],
    ]

    def fake_admin_sql(_backend, sql: str, *, max_bytes: int) -> bytes:
        calls.append(sql)
        return json.dumps(values[len(calls) - 1]).encode()

    monkeypatch.setattr(module, "admin_sql", fake_admin_sql)
    before = content_inventory_partition_digests(object(), _inventory())
    after = content_inventory_partition_digests(object(), _inventory())

    index = 0 if changed_partition == "business" else 1
    assert before[index] != after[index]
    assert all("to_jsonb(t)" in sql and "sha256" in sql for sql in calls)
    assert all("REPEATABLE READ READ ONLY" in sql for sql in calls)
    assert all("secret" not in sql for sql in calls)


def test_content_digest_detects_same_count_delete_insert_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_orchestrator.services import restoration_adaptation_workspace as module

    evidence = iter(
        (
            [{"rows": 1, "sha256": "b" * 64}, {"rows": 2, "sha256": "a" * 64}],
            [{"rows": 1, "sha256": "b" * 64}, {"rows": 2, "sha256": "c" * 64}],
        )
    )
    monkeypatch.setattr(
        module,
        "admin_sql",
        lambda *_args, **_kwargs: json.dumps(next(evidence)).encode(),
    )

    copied = content_inventory_partition_digests(object(), _inventory())
    replaced = content_inventory_partition_digests(object(), _inventory())

    assert copied[0] != replaced[0]
    assert copied[1] == replaced[1]


def test_content_digest_real_database_detects_same_count_source_and_candidate_changes(
    pg,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_orchestrator.services import restoration_adaptation_workspace as module

    pg.run(
        "CREATE TABLE public.items (id bigint PRIMARY KEY, visible text, hidden text);"
        "CREATE SCHEMA drizzle;"
        "CREATE TABLE drizzle.__drizzle_migrations (id bigint PRIMARY KEY, hash text);"
        "INSERT INTO public.items VALUES (1, 'kept', 'secret-a');"
        "INSERT INTO drizzle.__drizzle_migrations VALUES (1, 'migration-a');"
    )
    monkeypatch.setattr(
        module,
        "admin_sql",
        lambda _backend, sql, *, max_bytes: pg.run(sql),
    )
    baseline = content_inventory_partition_digests(object(), _inventory())

    pg.run("UPDATE public.items SET hidden='secret-b' WHERE id=1;")
    source_updated = content_inventory_partition_digests(object(), _inventory())
    assert source_updated[0] != baseline[0]
    assert source_updated[1] == baseline[1]

    pg.run(
        "DELETE FROM drizzle.__drizzle_migrations WHERE id=1;"
        "INSERT INTO drizzle.__drizzle_migrations VALUES (2, 'migration-b');"
    )
    candidate_replaced = content_inventory_partition_digests(object(), _inventory())
    assert candidate_replaced[0] == source_updated[0]
    assert candidate_replaced[1] != source_updated[1]


async def test_prepare_persists_replayable_operation_bound_copy_proof(tmp_path: Path) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)

    first = await service.prepare(request)
    replay = await service.prepare(request)

    expected_candidate = uuid5(
        request.operation_id,
        f"restoration-adaptation:{request.generation_run_id}",
    )
    assert first == replay
    assert first.candidate_workspace_id == expected_candidate
    assert first.source_workspace_id == request.workspace_id
    assert first.generation_run_id == request.generation_run_id
    assert first.source_database_digest == "1" * 64
    assert first.capabilities == {
        "portable_machine": True,
        "database_admin": "isolated_copy",
        "restoration_adaptation_database_copy_v1": True,
    }
    assert len(first.proof_digest) == 64
    assert engine.prepare_calls == 1
    receipt = tmp_path / str(request.workspace_id) / f"{request.generation_run_id}.json"
    assert receipt.is_file()
    assert request.digest() in receipt.read_text(encoding="utf-8")

    await service.cleanup(
        RestorationAdaptationCleanup(
            workspace_id=request.workspace_id,
            generation_run_id=request.generation_run_id,
            candidate_workspace_id=first.candidate_workspace_id,
            candidate_fencing_epoch=first.candidate_fencing_epoch,
            proof_digest=first.proof_digest,
        )
    )
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"

    await service.cleanup(
        RestorationAdaptationCleanup(
            workspace_id=request.workspace_id,
            generation_run_id=request.generation_run_id,
            candidate_workspace_id=first.candidate_workspace_id,
            candidate_fencing_epoch=first.candidate_fencing_epoch,
            proof_digest=first.proof_digest,
        )
    )
    assert engine.cleanup_calls == 1


async def test_prepare_rejects_and_cleans_nonidentical_database_copy(tmp_path: Path) -> None:
    request = _request()
    engine = _Engine(candidate_digest="9" * 64)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)

    with pytest.raises(CellIdentityConflict, match="database copy digest mismatch"):
        await service.prepare(request)

    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"


async def test_post_agent_proof_is_candidate_bound_immutable_and_replayable(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof_request = _proof_request(request, prepared.proof_digest)

    first = await service.prove(proof_request)
    replay = await service.prove(proof_request)

    assert first == replay
    assert first.state == "proof_ready"
    assert first.reason_code is None
    assert first.candidate_workspace_id == proof_request.candidate_workspace_id
    assert first.candidate_proof_key == proof_request.candidate_proof_key
    assert first.candidate_artifact_digest == proof_request.candidate_artifact_digest
    assert first.source_workspace_revision == request.source_workspace_revision
    assert first.capabilities["restoration_adaptation_proof_v1"] is True
    assert engine.prove_calls == 1


async def test_activation_offer_claim_pins_candidate_against_expired_gc(tmp_path: Path) -> None:
    request = _request()
    engine = _Engine(owner_active=False)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()

    claim = await service.claim_activation_offer(offer_request, activation_id)
    _expire(service, request)

    assert claim.proof == proof
    assert claim.source_fencing_epoch == request.fencing_epoch
    assert await service.recover() == 0
    assert engine.cleanup_calls == 0
    with pytest.raises(CellIdentityConflict, match="proof expired"):
        await service.validate_activation_offer(offer_request, activation_id, "8" * 64)


@pytest.mark.parametrize("sealed", [False, True], ids=["offering", "offered"])
async def test_explicit_cleanup_cannot_bypass_activation_pin(
    tmp_path: Path,
    sealed: bool,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)
    if sealed:
        await service.seal_activation_offer(offer_request, activation_id, "8" * 64)

    with pytest.raises(CellIdentityConflict, match="activation owns candidate cleanup"):
        await service.cleanup(
            RestorationAdaptationCleanup(
                workspace_id=request.workspace_id,
                generation_run_id=request.generation_run_id,
                candidate_workspace_id=prepared.candidate_workspace_id,
                candidate_fencing_epoch=prepared.candidate_fencing_epoch,
                proof_digest=prepared.proof_digest,
            )
        )

    assert engine.cleanup_calls == 0
    assert await service.status(request.workspace_id, request.generation_run_id) == (
        "activation_offered" if sealed else "activation_offering"
    )


async def test_explicit_cleanup_waits_for_activated_pin_terminal_policy(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)
    await service.seal_activation_offer(offer_request, activation_id, "8" * 64)
    await service.release_activation_offer(
        offer_request, activation_id, terminal_state="activated"
    )

    with pytest.raises(CellIdentityConflict, match="activation owns candidate cleanup"):
        await service.cleanup(
            RestorationAdaptationCleanup(
                workspace_id=request.workspace_id,
                generation_run_id=request.generation_run_id,
                candidate_workspace_id=prepared.candidate_workspace_id,
                candidate_fencing_epoch=prepared.candidate_fencing_epoch,
                proof_digest=prepared.proof_digest,
            )
        )
    assert engine.cleanup_calls == 0

    assert await service.recover_activation_orphans(frozenset()) == frozenset()
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"


async def test_orphan_activation_pin_cleanup_respects_live_facade_binding(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(owner_active=False)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)
    await service.seal_activation_offer(offer_request, activation_id, "8" * 64)
    _expire(service, request)

    retained = await service.recover_activation_orphans(frozenset({activation_id}))

    assert retained == frozenset({activation_id})
    assert engine.cleanup_calls == 0
    assert (
        await service.status(request.workspace_id, request.generation_run_id)
        == "activation_offered"
    )

    retained = await service.recover_activation_orphans(frozenset())

    assert retained == frozenset()
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"


async def test_reclaimed_activation_pin_refreshes_ttl_before_orphan_sweep(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(owner_active=False)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)
    _expire(service, request)

    await service.claim_activation_offer(offer_request, activation_id)
    retained = await service.recover_activation_orphans(frozenset())

    assert retained == frozenset({activation_id})
    assert engine.cleanup_calls == 0
    assert (
        await service.status(request.workspace_id, request.generation_run_id)
        == "activation_offering"
    )


async def test_orphan_activation_cleanup_replays_after_crash_from_durable_tombstone(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(owner_active=False, cleanup_fails=True)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)
    _expire(service, request)

    retained = await service.recover_activation_orphans(frozenset())

    assert retained == frozenset({activation_id})
    assert (
        await service.status(request.workspace_id, request.generation_run_id)
        == "cleanup_pending"
    )
    engine.cleanup_fails = False

    assert await service.recover() == 1
    assert engine.cleanup_calls == 2
    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"


async def test_completed_activation_candidate_uses_terminal_cleanup_policy(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)
    await service.seal_activation_offer(offer_request, activation_id, "8" * 64)
    await service.release_activation_offer(
        offer_request, activation_id, terminal_state="activated"
    )

    retained = await service.recover_activation_orphans(frozenset())

    assert retained == frozenset()
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"


async def test_failed_offer_claim_becomes_cleanup_tombstone(tmp_path: Path) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof = await service.prove(_proof_request(request, prepared.proof_digest))
    offer_request = _offer_request(proof)
    activation_id = uuid4()
    await service.claim_activation_offer(offer_request, activation_id)

    await service.abort_activation_offer(offer_request, activation_id)

    assert await service.recover() == 1
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"
    with pytest.raises(CellIdentityConflict, match="no longer active"):
        await service.claim_activation_offer(offer_request, activation_id)


async def test_post_agent_database_change_returns_typed_migration_required(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(
        proof_state="migration_required",
        proof_reason="candidate_business_data_changed",
    )
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)

    result = await service.prove(_proof_request(request, prepared.proof_digest))

    assert result.state == "migration_required"
    assert result.reason_code == "candidate_business_data_changed"
    assert "restoration_adaptation_proof_v1" not in result.capabilities


async def test_migration_required_repair_creates_new_immutable_proof_attempt(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(
        proof_state="migration_required",
        proof_reason="candidate_business_data_changed",
    )
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    stale = _proof_request(request, prepared.proof_digest)
    first = await service.prove(stale)
    assert first.state == "migration_required"

    engine.proof_state = "proof_ready"
    engine.proof_reason = None
    repaired = stale.model_copy(update={"proof_attempt": 2})
    second = await service.prove(repaired)
    replay = await service.prove(repaired)

    assert second == replay
    assert second.state == "proof_ready"
    assert second.candidate_artifact_digest == stale.candidate_artifact_digest
    assert second.proof_attempt == 2
    assert engine.prove_calls == 2
    with pytest.raises(CellIdentityConflict, match=r"stale .* proof attempt"):
        await service.prove(stale)


async def test_same_proof_attempt_replay_is_idempotent_and_changed_envelope_is_rejected(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof_request = _proof_request(request, prepared.proof_digest)

    first = await service.prove(proof_request)
    assert await service.prove(proof_request) == first
    assert engine.prove_calls == 1
    with pytest.raises(CellIdentityConflict, match="attempt changed"):
        await service.prove(
            proof_request.model_copy(update={"candidate_artifact_digest": "8" * 64})
        )


async def test_failed_proof_drive_retries_same_trusted_attempt(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _BlockingEngine(prove_error=True)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof_request = _proof_request(request, prepared.proof_digest)
    first = asyncio.create_task(service.prove(proof_request))
    await engine.started.wait()
    engine.release_proof.set()
    with pytest.raises(RuntimeError, match="proof failed late"):
        await first

    engine.prove_error = False
    result = await service.prove(proof_request)

    assert result.state == "proof_ready"
    saved = service._read(request.workspace_id, request.generation_run_id)
    assert saved is not None and saved["proof_drive"] == 2


async def test_post_agent_source_change_returns_typed_failure_without_proof(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(
        proof_state="source_changed",
        proof_reason="source_database_changed",
    )
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)

    result = await service.prove(_proof_request(request, prepared.proof_digest))

    assert result.state == "source_changed"
    assert result.reason_code == "source_database_changed"
    assert "restoration_adaptation_proof_v1" not in result.capabilities


async def test_startup_recovery_retries_fail_closed_cleanup(tmp_path: Path) -> None:
    request = _request()
    broken = _Engine(candidate_digest="9" * 64, cleanup_fails=True)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=broken)
    with pytest.raises(CellIdentityConflict):
        await service.prepare(request)
    assert (
        await service.status(request.workspace_id, request.generation_run_id) == "cleanup_pending"
    )

    recovered = _Engine()
    restarted = RestorationAdaptationWorkspaceService(root=tmp_path, engine=recovered)
    assert await restarted.recover() == 1
    assert recovered.cleanup_calls == 1
    assert await restarted.status(request.workspace_id, request.generation_run_id) == "failed"


@pytest.mark.parametrize("proof_complete", [False, True])
async def test_recovery_cleans_expired_abandoned_ready_and_proof_ready_candidates(
    tmp_path: Path,
    proof_complete: bool,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    if proof_complete:
        await service.prove(_proof_request(request, prepared.proof_digest))
    _expire(service, request)

    restarted_engine = _Engine(owner_active=False)
    restarted = RestorationAdaptationWorkspaceService(root=tmp_path, engine=restarted_engine)
    assert await restarted.recover() == 1
    assert restarted_engine.cleanup_calls == 1
    assert await restarted.status(request.workspace_id, request.generation_run_id) == "cleaned"
    assert await restarted.recover() == 0
    assert restarted_engine.cleanup_calls == 1


async def test_recovery_preserves_expired_candidate_while_owner_run_is_active(
    tmp_path: Path,
) -> None:
    request = _request()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=_Engine())
    await service.prepare(request)
    _expire(service, request)

    active = _Engine(owner_active=True)
    restarted = RestorationAdaptationWorkspaceService(root=tmp_path, engine=active)
    assert await restarted.recover() == 0
    assert active.cleanup_calls == 0
    assert await restarted.status(request.workspace_id, request.generation_run_id) == "ready"


async def test_expired_sealed_proof_is_bounded_even_with_stale_active_source_lease(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(owner_active=True)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    await service.prove(_proof_request(request, prepared.proof_digest))
    _expire(service, request)

    assert await service.recover() == 1
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"


async def test_terminal_owner_status_cleans_even_when_source_lease_is_stale_active(
    tmp_path: Path,
) -> None:
    request = _request()
    active = _Engine(owner_active=True)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=active)
    await service.prepare(request)

    await service.record_owner_status(
        RestorationAdaptationOwnerStatus(
            workspace_id=request.workspace_id,
            operation_id=request.operation_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            generation_run_id=request.generation_run_id,
            state="terminal",
        )
    )

    assert await service.recover() == 1
    assert active.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"


async def test_expired_preparing_crash_is_cleaned_and_does_not_stay_in_progress(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(owner_active=False)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    service._write(
        {
            "version": 1,
            "workspace_id": str(request.workspace_id),
            "generation_run_id": str(request.generation_run_id),
            "request": request.model_dump(mode="json"),
            "request_digest": request.digest(),
            "candidate_workspace_id": str(
                uuid5(
                    request.operation_id,
                    f"restoration-adaptation:{request.generation_run_id}",
                )
            ),
            "state": "preparing",
            "owner_run_status": "active",
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )

    assert await service.recover() == 1
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"


async def test_recover_isolates_malformed_journal_and_cleans_later_candidate(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine(owner_active=False)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    _expire(service, request)
    malformed = tmp_path / str(UUID(int=0)) / f"{UUID(int=0)}.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("{broken", encoding="utf-8")

    assert await service.recover() == 1
    assert engine.cleanup_calls == 1
    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"
    assert malformed.read_text(encoding="utf-8") == "{broken"
    assert prepared.candidate_workspace_id != UUID(int=0)


@pytest.mark.parametrize("kind", ["release", "destroy"])
async def test_docker_cleanup_continues_after_successful_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from tests.test_docker_cell_resources import _make_manager, _spec

    request = _request().model_copy(
        update={
            "project_id": UUID(int=2),
            "owner_id": UUID(int=3),
            "fencing_epoch": 1,
        }
    )
    candidate_id = uuid5(
        request.operation_id,
        f"restoration-adaptation:{request.generation_run_id}",
    )
    manager, docker, state_store, _lock = _make_manager(tmp_path / "real-manager")
    spec = replace(
        _spec(candidate_id),
        generation_run_id=request.generation_run_id,
    )
    await manager.ensure(
        spec,
        LifecycleMutation(UUID(int=100), 1, request.digest()),
    )
    state = state_store.load(candidate_id)
    assert state is not None and state.resource_names is not None
    release_id = uuid5(request.generation_run_id, "restoration-adaptation-release")
    release = LifecycleMutation(release_id, 2, request.digest())
    if kind == "release":
        state_store.begin(
            spec,
            release,
            kind="release",
            phase="planned",
            resource_names=state.resource_names,
        )
        state_store.mark_indeterminate(candidate_id, mutation=release)
    else:
        await manager.release_generation(
            candidate_id,
            release,
            generation_run_id=request.generation_run_id,
        )
        released = state_store.load(candidate_id)
        assert released is not None and released.resource_names is not None
        destroy = LifecycleMutation(
            uuid5(request.generation_run_id, "restoration-adaptation-destroy"),
            3,
            request.digest(),
        )
        state_store.begin(
            replace(spec, generation_run_id=None),
            destroy,
            kind="destroy",
            phase="planned",
            resource_names=released.resource_names,
        )
        state_store.mark_indeterminate(candidate_id, mutation=destroy)

    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication_capacity.production_manager",
        lambda _manager, _settings: manager,
    )
    monkeypatch.setattr(
        "omnia_orchestrator.core.config.get_settings",
        lambda: object(),
    )
    engine = DockerAdaptationWorkspaceEngine()
    monkeypatch.setattr(engine, "_manager", lambda _workspace_id: object())

    await engine.cleanup(request, candidate_id, 1)

    final = state_store.load(candidate_id)
    assert final is not None
    latest = final.operation(final.last_operation_id)
    assert latest is not None and latest.kind == "destroy"
    assert latest.status == "completed"
    assert final.active_generation_run_id is None
    assert await docker.list_workspace_volumes(candidate_id) == []


@pytest.mark.parametrize("kind", ["release", "destroy"])
async def test_docker_cleanup_reconciles_own_indeterminate_lifecycle_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from tests.test_docker_cell_resources import _make_manager, _spec

    request = _request().model_copy(
        update={
            "project_id": UUID(int=2),
            "owner_id": UUID(int=3),
            "fencing_epoch": 1,
        }
    )
    candidate_id = uuid5(
        request.operation_id,
        f"restoration-adaptation:{request.generation_run_id}",
    )
    manager, docker, state_store, _lock = _make_manager(tmp_path / "real-manager")
    spec = replace(
        _spec(candidate_id),
        generation_run_id=request.generation_run_id,
    )
    await manager.ensure(
        spec,
        LifecycleMutation(UUID(int=100), 1, request.digest()),
    )
    state = state_store.load(candidate_id)
    assert state is not None and state.resource_names is not None
    release_id = uuid5(request.generation_run_id, "restoration-adaptation-release")
    release = LifecycleMutation(release_id, 2, request.digest())
    if kind == "release":
        cleanup_operation_id = release_id
        state_store.begin(
            spec,
            release,
            kind="release",
            phase="planned",
            resource_names=state.resource_names,
        )
        state_store.mark_indeterminate(candidate_id, mutation=release)
    else:
        await manager.release_generation(
            candidate_id,
            release,
            generation_run_id=request.generation_run_id,
        )
        released = state_store.load(candidate_id)
        assert released is not None and released.resource_names is not None
        destroy_id = uuid5(request.generation_run_id, "restoration-adaptation-destroy")
        cleanup_operation_id = destroy_id
        destroy = LifecycleMutation(destroy_id, 3, request.digest())
        state_store.begin(
            replace(spec, generation_run_id=None),
            destroy,
            kind="destroy",
            phase="planned",
            resource_names=released.resource_names,
        )
        state_store.mark_indeterminate(candidate_id, mutation=destroy)

    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication_capacity.production_manager",
        lambda _manager, _settings: manager,
    )
    monkeypatch.setattr(
        "omnia_orchestrator.core.config.get_settings",
        lambda: object(),
    )
    engine = DockerAdaptationWorkspaceEngine()
    monkeypatch.setattr(engine, "_manager", lambda _workspace_id: object())

    class CrashAfterReconcile(BaseException):
        pass

    manager_type = type(manager)
    reconcile = manager_type.reconcile

    async def reconcile_then_crash(
        self: object,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ):
        await reconcile(self, workspace_id, mutation)
        monkeypatch.setattr(manager_type, "reconcile", reconcile)
        raise CrashAfterReconcile

    monkeypatch.setattr(manager_type, "reconcile", reconcile_then_crash)
    with pytest.raises(CrashAfterReconcile):
        await engine.cleanup(request, candidate_id, 1)

    reconciled = state_store.load(candidate_id)
    assert (
        reconciled is not None
        and reconciled.phase != "indeterminate"
        and reconciled.resource_names is not None
    )
    retry_id = uuid5(
        cleanup_operation_id,
        "restoration-adaptation-cleanup-retry",
    )
    retry = LifecycleMutation(
        retry_id,
        reconciled.fencing_epoch + 1,
        request.digest(),
    )
    retry_spec = spec if kind == "release" else replace(spec, generation_run_id=None)
    state_store.begin(
        retry_spec,
        retry,
        kind=kind,
        phase="planned",
        resource_names=reconciled.resource_names,
    )
    state_store.mark_indeterminate(candidate_id, mutation=retry)

    monkeypatch.setattr(manager_type, "reconcile", reconcile_then_crash)
    with pytest.raises(CrashAfterReconcile):
        await engine.cleanup(request, candidate_id, 1)

    reconciled_twice = state_store.load(candidate_id)
    assert reconciled_twice is not None and reconciled_twice.phase != "indeterminate"
    await engine.cleanup(request, candidate_id, 1)

    final = state_store.load(candidate_id)
    assert final is not None
    latest = final.operation(final.last_operation_id)
    assert latest is not None and latest.kind == "destroy"
    assert latest.status == "completed"
    assert final.active_generation_run_id is None
    assert await docker.list_workspace_volumes(candidate_id) == []
    current_id = cleanup_operation_id
    prior_fence = -1
    for _attempt in range(2):
        cleanup = final.operation(current_id)
        assert cleanup is not None and cleanup.kind == kind
        assert cleanup.status == "indeterminate"
        assert cleanup.request_digest == request.digest()
        assert cleanup.fencing_epoch > prior_fence
        assert cleanup.generation_run_id == (
            request.generation_run_id if kind == "release" else None
        )
        reconcile_id = uuid5(
            current_id,
            "restoration-adaptation-cleanup-reconcile",
        )
        reconcile_record = final.operation(reconcile_id)
        assert reconcile_record is not None and reconcile_record.kind == "reconcile"
        assert reconcile_record.status == "completed"
        assert reconcile_record.request_digest == request.digest()
        assert reconcile_record.fencing_epoch > cleanup.fencing_epoch
        prior_fence = reconcile_record.fencing_epoch
        current_id = uuid5(
            current_id,
            "restoration-adaptation-cleanup-retry",
        )
    completed_retry = final.operation(current_id)
    assert completed_retry is not None and completed_retry.kind == kind
    assert completed_retry.status == "completed"
    assert completed_retry.request_digest == request.digest()
    assert completed_retry.fencing_epoch > prior_fence


async def test_cleanup_reconcile_chain_rejects_foreign_retry() -> None:
    from types import SimpleNamespace

    from omnia_orchestrator.services.cell_state import CellOperationRecord, CellWorkspaceState

    request = _request()
    candidate_id = uuid4()
    operation_id = uuid5(request.generation_run_id, "restoration-adaptation-release")
    reconcile_id = uuid5(
        operation_id,
        "restoration-adaptation-cleanup-reconcile",
    )
    retry_id = uuid5(
        operation_id,
        "restoration-adaptation-cleanup-retry",
    )
    records = (
        CellOperationRecord(
            operation_id=operation_id,
            kind="release",
            status="indeterminate",
            phase="indeterminate",
            request_digest=request.digest(),
            fencing_epoch=2,
            generation_run_id=request.generation_run_id,
        ),
        CellOperationRecord(
            operation_id=reconcile_id,
            kind="reconcile",
            status="completed",
            phase="completed",
            request_digest=request.digest(),
            fencing_epoch=3,
            generation_run_id=request.generation_run_id,
        ),
        CellOperationRecord(
            operation_id=retry_id,
            kind="release",
            status="indeterminate",
            phase="indeterminate",
            request_digest="f" * 64,
            fencing_epoch=4,
            generation_run_id=request.generation_run_id,
        ),
    )
    state = CellWorkspaceState(
        workspace_id=candidate_id,
        project_id=request.project_id,
        owner_id=request.owner_id,
        profile_version="1",
        phase="indeterminate",
        bundle_state="indeterminate",
        fencing_epoch=4,
        active_generation_run_id=request.generation_run_id,
        active_generation_fencing_epoch=4,
        last_operation_id=retry_id,
        provider_ref=None,
        resource_names=None,
        operations=records,
    )
    manager = SimpleNamespace(
        state_store=SimpleNamespace(load=lambda _workspace_id: state),
        reconcile=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(CellIdentityConflict, match="cleanup retry chain changed"):
        await DockerAdaptationWorkspaceEngine._reconcile_cleanup_operation(
            manager,
            request,
            candidate_id,
            state,
            kind="release",
            operation_id=operation_id,
        )


async def test_cleanup_reconcile_chain_has_bounded_depth() -> None:
    from types import SimpleNamespace

    from omnia_orchestrator.services.cell_state import CellOperationRecord, CellWorkspaceState

    request = _request()
    candidate_id = uuid4()
    current_id = uuid5(request.generation_run_id, "restoration-adaptation-release")
    records: list[CellOperationRecord] = []
    fencing_epoch = 1
    for _attempt in range(16):
        fencing_epoch += 1
        records.append(
            CellOperationRecord(
                operation_id=current_id,
                kind="release",
                status="indeterminate",
                phase="indeterminate",
                request_digest=request.digest(),
                fencing_epoch=fencing_epoch,
                generation_run_id=request.generation_run_id,
            )
        )
        reconcile_id = uuid5(
            current_id,
            "restoration-adaptation-cleanup-reconcile",
        )
        fencing_epoch += 1
        records.append(
            CellOperationRecord(
                operation_id=reconcile_id,
                kind="reconcile",
                status="completed",
                phase="completed",
                request_digest=request.digest(),
                fencing_epoch=fencing_epoch,
                generation_run_id=request.generation_run_id,
            )
        )
        current_id = uuid5(
            current_id,
            "restoration-adaptation-cleanup-retry",
        )
    state = CellWorkspaceState(
        workspace_id=candidate_id,
        project_id=request.project_id,
        owner_id=request.owner_id,
        profile_version="1",
        phase="completed",
        bundle_state="resources_ready",
        fencing_epoch=fencing_epoch,
        active_generation_run_id=request.generation_run_id,
        active_generation_fencing_epoch=fencing_epoch,
        last_operation_id=records[-1].operation_id,
        provider_ref=None,
        resource_names=None,
        operations=tuple(records),
    )
    manager = SimpleNamespace(
        state_store=SimpleNamespace(load=lambda _workspace_id: state),
        reconcile=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(CellIdentityConflict, match="cleanup retry chain is too deep"):
        await DockerAdaptationWorkspaceEngine._reconcile_cleanup_operation(
            manager,
            request,
            candidate_id,
            state,
            kind="release",
            operation_id=records[0].operation_id,
        )


async def test_late_prepare_cannot_overwrite_recovered_terminal_state(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _BlockingPrepareEngine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepare_task = asyncio.create_task(service.prepare(request))
    await engine.prepare_started.wait()
    _expire(service, request)

    assert await service.recover() == 1
    engine.release_prepare.set()
    with pytest.raises(CellIdentityConflict):
        await prepare_task

    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"


async def test_late_materialized_candidate_cleanup_is_durable_and_retryable(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _LateCandidateEngine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepare_task = asyncio.create_task(service.prepare(request))
    await engine.prepare_started.wait()
    _expire(service, request)

    assert await service.recover() == 1
    assert engine.cleanup_calls == 1
    engine.release_prepare.set()
    with pytest.raises(CellIdentityConflict):
        await prepare_task

    assert engine.candidate_exists is True
    assert engine.cleanup_calls == 2
    assert engine.cleanup_epochs == [1, 9]
    saved = service._read(request.workspace_id, request.generation_run_id)
    assert saved is not None and saved["candidate_fencing_epoch"] == 9
    assert (
        await service.status(request.workspace_id, request.generation_run_id) == "cleanup_pending"
    )

    engine.fail_late_cleanup = False
    assert await service.recover() == 1
    assert engine.cleanup_calls == 3
    assert engine.cleanup_epochs == [1, 9, 9]
    assert engine.candidate_exists is False
    assert await service.status(request.workspace_id, request.generation_run_id) == "failed"


async def test_expired_proving_attempt_is_redriven_with_private_fence(
    tmp_path: Path,
) -> None:
    request = _request()
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof_request = _proof_request(request, prepared.proof_digest)
    saved = service._read(request.workspace_id, request.generation_run_id)
    assert saved is not None
    service._write(
        {
            **saved,
            "state": "proving",
            "proof_request": proof_request.model_dump(mode="json"),
            "proof_request_digest": proof_request.digest(),
            "proof_attempt": 1,
            "proof_drive": 1,
            "proof_attempts": {
                "1": {
                    "attempt": 1,
                    "drive": 1,
                    "request_digest": proof_request.digest(),
                    "request": proof_request.model_dump(mode="json"),
                    "result": None,
                }
            },
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )

    result = await service.prove(proof_request)

    assert result.state == "proof_ready"
    assert engine.prove_calls == 1
    saved = service._read(request.workspace_id, request.generation_run_id)
    assert saved is not None and saved["proof_drive"] == 2


async def test_recovery_retries_transient_abandoned_candidate_cleanup(
    tmp_path: Path,
) -> None:
    request = _request()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=_Engine())
    await service.prepare(request)
    _expire(service, request)

    recovered = _Engine(cleanup_fails=True)
    restarted = RestorationAdaptationWorkspaceService(root=tmp_path, engine=recovered)
    assert await restarted.recover() == 0
    assert (
        await restarted.status(request.workspace_id, request.generation_run_id) == "cleanup_pending"
    )
    recovered.cleanup_fails = False
    assert await restarted.recover() == 1
    assert recovered.cleanup_calls == 2
    assert await restarted.status(request.workspace_id, request.generation_run_id) == "cleaned"


@pytest.mark.parametrize("prove_error", [False, True])
async def test_late_proof_cannot_overwrite_cleanup_terminal_state(
    tmp_path: Path,
    prove_error: bool,
) -> None:
    request = _request()
    engine = _BlockingEngine(prove_error=prove_error)
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    prepared = await service.prepare(request)
    proof_request = _proof_request(request, prepared.proof_digest)
    proof_task = asyncio.create_task(service.prove(proof_request))
    await engine.started.wait()

    await service.cleanup(
        RestorationAdaptationCleanup(
            workspace_id=request.workspace_id,
            generation_run_id=request.generation_run_id,
            candidate_workspace_id=prepared.candidate_workspace_id,
            candidate_fencing_epoch=prepared.candidate_fencing_epoch,
            proof_digest=prepared.proof_digest,
        )
    )
    engine.release_proof.set()
    expected = RuntimeError if prove_error else CellIdentityConflict
    with pytest.raises(expected):
        await proof_task

    assert await service.status(request.workspace_id, request.generation_run_id) == "cleaned"
    saved = service._read(request.workspace_id, request.generation_run_id)
    assert saved is not None and saved.get("proof_result") is None
