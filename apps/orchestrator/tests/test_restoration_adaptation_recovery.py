"""Recovery holds preserve the existing candidate, including across GC and restart."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.test_restoration_adaptation_workspace import _Engine, _request
from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.schemas import restoration_adaptation as contract
from yleum_orchestrator.schemas.restoration_adaptation import RestorationAdaptationCleanup
from yleum_orchestrator.services.restoration_adaptation_workspace import (
    RestorationAdaptationWorkspaceService,
)


def _identity(request, candidate):
    return dict(
        version=1,
        transition_id=uuid4(),
        workspace_id=request.workspace_id,
        generation_run_id=request.generation_run_id,
        operation_id=request.operation_id,
        project_id=request.project_id,
        owner_id=request.owner_id,
        candidate_workspace_id=candidate,
        request_digest=request.digest(),
    )


def test_recovery_wire_denies_unknown_and_incomplete_terminal():
    request = _request()
    identity = _identity(request, uuid4())
    hold = contract.RestorationAdaptationRecoveryHold(
        **identity, expected_cycle=0, control_fencing_epoch=7, candidate_fencing_epoch=1
    )
    assert hold.expected_cycle == 0
    with pytest.raises(ValidationError):
        contract.RestorationAdaptationRecoveryHold(**{**hold.model_dump(), "version": 2})
    with pytest.raises(ValidationError):
        contract.RestorationAdaptationRecoveryHold(
            **{**hold.model_dump(), "transition_id": request.operation_id}
        )
    receipt = dict(
        **identity,
        state="hold_pending",
        cycle=0,
        original_control_fencing_epoch=7,
        current_control_fencing_epoch=7,
        original_candidate_fencing_epoch=1,
        current_candidate_fencing_epoch=1,
        barrier_operation_id=uuid4(),
    )
    assert contract.RestorationAdaptationRecoveryReceipt(**receipt).terminal_receipt_digest is None
    with pytest.raises(ValidationError):
        contract.RestorationAdaptationRecoveryReceipt(**{**receipt, "state": "held"})


@pytest.mark.parametrize(
    "state", ["hold_pending", "held", "rebinding", "resumable", "future_unknown"]
)
async def test_existing_candidate_with_recovery_metadata_never_ttl_gc_or_ordinary_cleanup(
    tmp_path, state
):
    engine = _Engine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    request = _request()
    ready = await service.prepare(request)
    saved = service._read(request.workspace_id, request.generation_run_id)
    saved["expires_at"] = "2000-01-01T00:00:00+00:00"
    saved["recovery_hold"] = {"version": 1, "state": state}
    service._write(saved)
    assert (
        await service._recover_candidate(
            service._path(request.workspace_id, request.generation_run_id)
        )
        == 0
    )
    with pytest.raises(CellIdentityConflict):
        await service.cleanup(
            RestorationAdaptationCleanup(
                workspace_id=request.workspace_id,
                generation_run_id=request.generation_run_id,
                candidate_workspace_id=ready.candidate_workspace_id,
                candidate_fencing_epoch=1,
                proof_digest=ready.proof_digest,
            )
        )
    assert engine.cleanup_calls == 0
    assert (
        service._read(request.workspace_id, request.generation_run_id)["recovery_hold"]
        == saved["recovery_hold"]
    )


async def test_prepare_response_exposes_only_saved_original_request_digest(tmp_path):
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=_Engine())
    request = _request()
    first = await service.prepare(request)
    assert first.request_digest == request.digest()
    saved = service._read(request.workspace_id, request.generation_run_id)
    saved["response"].pop("request_digest", None)
    service._write(saved)
    replay = await service.prepare(request)
    assert replay.request_digest == request.digest()


class _RecoveryEngine(_Engine):
    hold_calls = 0
    rebind_calls = 0
    inspect_calls = 0
    publish_calls = 0
    current_control = 7
    pending = False

    async def recovery_current_control(self, original, epoch):
        if epoch != self.current_control:
            raise CellIdentityConflict("control lease changed")

    async def recovery_hold(self, original, request, pending):
        self.hold_calls += 1
        if self.pending:
            return pending
        return self._terminal(pending, "held")

    async def recovery_rebind(self, original, request, pending):
        self.rebind_calls += 1
        if self.pending:
            return pending
        return self._terminal(pending, "resumable")

    async def recovery_inspect(self, original, receipt):
        self.inspect_calls += 1
        return receipt

    async def recovery_publish(self, original, receipt):
        self.publish_calls += 1

    @staticmethod
    def _terminal(pending, state):
        from yleum_orchestrator.services.restoration_binding import canonical_digest

        raw = pending.model_dump(mode="json")
        raw.update(
            state=state,
            barrier_receipt_digest="1" * 64,
            checkpoint_ref_digest="2" * 64,
            physical_incarnation_digest="3" * 64,
            physical_proof_digest="4" * 64,
        )
        raw["terminal_receipt_digest"] = canonical_digest(
            {k: v for k, v in raw.items() if k != "terminal_receipt_digest"}
        )
        return contract.RestorationAdaptationRecoveryReceipt.model_validate(raw)


@pytest.mark.parametrize("unknown", [None, False, {}, {"version": 2}])
async def test_present_unknown_hold_preserves_bytes_without_writes_or_physical_calls(
    tmp_path, monkeypatch, unknown
):
    engine = _RecoveryEngine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    request = _request()
    ready = await service.prepare(request)
    saved = service._read(request.workspace_id, request.generation_run_id)
    saved["recovery_hold"] = unknown
    service._write(saved)
    path = service._path(request.workspace_id, request.generation_run_id)
    before = path.read_bytes()
    writes = []
    actual_write = service._write

    def observed_write(value):
        writes.append(value)
        actual_write(value)

    monkeypatch.setattr(service, "_write", observed_write)
    hold = contract.RestorationAdaptationRecoveryHold(
        **_identity(request, ready.candidate_workspace_id), expected_cycle=0,
        control_fencing_epoch=7, candidate_fencing_epoch=1,
    )
    with pytest.raises(CellIdentityConflict, match="unknown recovery hold"):
        await service.recovery_hold(hold)
    assert writes == [] and path.read_bytes() == before
    assert (engine.hold_calls, engine.rebind_calls, engine.inspect_calls, engine.publish_calls) == (
        0, 0, 0, 0
    )


async def test_hold_and_rebind_preserve_original_candidate_and_status_never_dispatches(tmp_path):
    engine = _RecoveryEngine()
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    original = _request()
    ready = await service.prepare(original)
    hold = contract.RestorationAdaptationRecoveryHold(
        **_identity(original, ready.candidate_workspace_id),
        expected_cycle=0,
        control_fencing_epoch=7,
        candidate_fencing_epoch=1,
    )
    receipt = await service.recovery_hold(hold)
    assert receipt.state == "held"
    assert await service.recovery_hold(hold) == receipt
    status = contract.RestorationAdaptationRecoveryIdentity.model_validate(
        {
            key: getattr(hold, key)
            for key in contract.RestorationAdaptationRecoveryIdentity.model_fields
        }
    )
    assert await service.recovery_status(status) == receipt
    assert engine.hold_calls == 1
    with pytest.raises(CellIdentityConflict):
        await service.recovery_hold(hold.model_copy(update={"candidate_fencing_epoch": 2}))
    engine.current_control = 9
    rebind = contract.RestorationAdaptationRecoveryRebind(
        **{**_identity(original, ready.candidate_workspace_id), "transition_id": uuid4()},
        expected_cycle=0,
        expected_hold_receipt_digest=receipt.terminal_receipt_digest,
        new_control_fencing_epoch=9,
    )
    resumed = await service.recovery_rebind(rebind)
    assert resumed.state == "resumable" and resumed.cycle == 1
    assert resumed.current_candidate_fencing_epoch == 2
    assert await service.recovery_rebind(rebind) == resumed
    assert engine.rebind_calls == 1 and engine.prepare_calls == 1 and engine.cleanup_calls == 0
    saved = service._read(original.workspace_id, original.generation_run_id)
    assert saved["request"] == original.model_dump(mode="json")
    assert saved["request_digest"] == original.digest()
    assert saved["response"]["source_database_digest"] == ready.source_database_digest


async def test_pending_restart_status_does_not_retry_unknown_physical_transition(tmp_path):
    engine = _RecoveryEngine()
    engine.pending = True
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    original = _request()
    ready = await service.prepare(original)
    hold = contract.RestorationAdaptationRecoveryHold(
        **_identity(original, ready.candidate_workspace_id),
        expected_cycle=0,
        control_fencing_epoch=7,
        candidate_fencing_epoch=1,
    )
    first = await service.recovery_hold(hold)
    assert first.state == "hold_pending"
    restarted = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    status = contract.RestorationAdaptationRecoveryIdentity.model_validate(
        {
            key: getattr(hold, key)
            for key in contract.RestorationAdaptationRecoveryIdentity.model_fields
        }
    )
    assert await restarted.recovery_status(status) == first
    assert engine.hold_calls == 1 and engine.rebind_calls == 0


def test_cleanup_recovery_pair_is_both_or_neither():
    base = dict(
        workspace_id=uuid4(),
        generation_run_id=uuid4(),
        candidate_workspace_id=uuid4(),
        candidate_fencing_epoch=1,
        proof_digest="a" * 64,
    )
    assert contract.RestorationAdaptationCleanup(**base).recovery_cycle is None
    for fields in ({"recovery_cycle": 0}, {"recovery_receipt_digest": "b" * 64}):
        with pytest.raises(ValidationError):
            contract.RestorationAdaptationCleanup(**base, **fields)
    value = contract.RestorationAdaptationCleanup(
        **base, recovery_cycle=0, recovery_receipt_digest="b" * 64
    )
    assert value.recovery_cycle == 0


@pytest.mark.parametrize("state", ["hold_pending", "held", "rebinding", "future_unknown"])
def test_incomplete_recovery_never_exports_activation_authority(state):
    saved = {"recovery_hold": {"version": 1, "state": state}}
    with pytest.raises(CellIdentityConflict, match="recovery"):
        RestorationAdaptationWorkspaceService._activation_proof(saved, object())


@pytest.mark.parametrize("failure", [False, True])
async def test_terminal_cleanup_is_current_cycle_bound_and_unknown_never_redispatched(
    tmp_path, failure
):
    engine = _RecoveryEngine()

    async def cleanup_recovery(original, receipt):
        engine.cleanup_calls += 1
        if failure:
            raise RuntimeError("unknown destroy")

    engine.recovery_cleanup = cleanup_recovery
    service = RestorationAdaptationWorkspaceService(root=tmp_path, engine=engine)
    original = _request()
    ready = await service.prepare(original)
    hold = contract.RestorationAdaptationRecoveryHold(
        **_identity(original, ready.candidate_workspace_id),
        expected_cycle=0,
        control_fencing_epoch=7,
        candidate_fencing_epoch=1,
    )
    held = await service.recovery_hold(hold)
    cleanup = contract.RestorationAdaptationCleanup(
        workspace_id=original.workspace_id,
        generation_run_id=original.generation_run_id,
        candidate_workspace_id=ready.candidate_workspace_id,
        candidate_fencing_epoch=1,
        proof_digest=ready.proof_digest,
        recovery_cycle=0,
        recovery_receipt_digest=held.terminal_receipt_digest,
    )
    with pytest.raises(CellIdentityConflict):
        await service.cleanup(cleanup)
    await service.record_owner_status(
        contract.RestorationAdaptationOwnerStatus(
            workspace_id=original.workspace_id,
            generation_run_id=original.generation_run_id,
            operation_id=original.operation_id,
            project_id=original.project_id,
            owner_id=original.owner_id,
            state="terminal",
        )
    )
    with pytest.raises(CellIdentityConflict):
        await service.cleanup(cleanup.model_copy(update={"recovery_cycle": 1}))
    if failure:
        with pytest.raises(RuntimeError):
            await service.cleanup(cleanup)
        with pytest.raises(CellIdentityConflict):
            await service.cleanup(cleanup)
    else:
        await service.cleanup(cleanup)
        await service.cleanup(cleanup)  # lost ACK, no second physical destroy
    assert engine.cleanup_calls == 1
    assert (
        await service._recover_candidate(
            service._path(original.workspace_id, original.generation_run_id)
        )
        == 0
    )


@pytest.mark.parametrize("action", ["hold", "status", "rebind"])
async def test_recovery_routes_auth_route_identity_and_readonly_status(monkeypatch, action):
    from types import SimpleNamespace

    from yleum_orchestrator.core.errors import OrchestratorError
    from yleum_orchestrator.routers import code_restorations as routes

    identity = _identity(_request(), uuid4())
    if action == "hold":
        body = contract.RestorationAdaptationRecoveryHold(
            **identity, expected_cycle=0, control_fencing_epoch=7, candidate_fencing_epoch=1
        )
    elif action == "rebind":
        body = contract.RestorationAdaptationRecoveryRebind(
            **identity,
            expected_cycle=0,
            expected_hold_receipt_digest="a" * 64,
            new_control_fencing_epoch=8,
        )
    else:
        body = contract.RestorationAdaptationRecoveryIdentity(**identity)
    calls = []

    async def operation(value):
        calls.append(value)
        return "exact-result"

    def auth(token):
        if token != "fixture":
            raise OrchestratorError(code="unauthorized", message="denied", status_code=401)

    monkeypatch.setattr(routes, "verify_internal_token", auth)
    monkeypatch.setattr(
        routes,
        "get_restoration_adaptation_workspace_service",
        lambda: SimpleNamespace(**{"recovery_" + action: operation}),
    )
    route = getattr(routes, "recovery_" + action + "_restoration_adaptation")
    with pytest.raises(OrchestratorError):
        await route(body.workspace_id, body.generation_run_id, body, None)
    with pytest.raises(OrchestratorError):
        await route(uuid4(), body.generation_run_id, body, "fixture")
    assert calls == []
    assert await route(body.workspace_id, body.generation_run_id, body, "fixture") == "exact-result"
    assert calls == [body]
