"""A sealed unused request can retire the owning repair without replaying work."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from tests.test_generation_recovery_deadlines import clock_owner as clock_owner
from tests.test_generation_recovery_deadlines import response_schema as response_schema
from tests.test_max_management_generation import MISSING_SELECTION_PAGE, connection_files
from tests.test_max_management_source_repair_diagnostics import BASE
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellProof,
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from yleum_api.services.generation import (
    recovery_execution as execution,
)
from yleum_api.services.generation import (
    recovery_pipeline as pipeline,
)
from yleum_api.services.generation import recovery_requirements, recovery_store, source_checkpoint
from yleum_api.services.generation.provider_attempts import expired_reservation_closed
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    IncompleteResponseBudget,
    RecoveryBinding,
)
from yleum_api.services.generation.recovery_reconciliation import (
    _TOOL_EFFECT,
    ProviderDispatchPlan,
    provider_root_effect_id,
)
from yleum_api.services.generation.recovery_tool_receipts import digest as receipt_digest
from yleum_api.services.max_finalization import MaxFinalizationConflict, MaxFinalizationCoordinator
from yleum_api.services.max_generation_contract import max_source_completion_gap
from yleum_api.services.orchestrator_client import (
    OrchestratorMachineInventoryUnavailable,
    ProjectCellEnvironmentRecoveryProof,
    ProjectCellResourceResponse,
    ProjectCellWorkspaceIdentity,
)
from yleum_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
    ProjectCellExecutorUnavailable,
)
from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity, ProofOutcome
from yleum_api.services.project_cells import _canonical_operation_envelope
from yleum_api.services.promotion_permit import workspace_revision_digest

_PROMPT = "Собери приложение"
_OLD_DETAIL = "The previous typed managed refusal was retained."


async def _historical_roles(c, workspace, old, root, step, context_ref, scope, now, case):
    c.session.add(ProjectCellOperation(
        id=uuid4(), workspace_id=workspace.id, generation_run_id=c.run.id,
        kind="release", status="completed", fencing_epoch=8,
        idempotency_key=f"generation:{c.run.id}:release:historical:cycle:1",
        request_digest="2" * 64, finished_at=now - timedelta(hours=2),
        result_payload=ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", fencing_epoch=8,
            provider_ref="fixture", checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        ).to_wire_json(),
    ))
    role_binding = old.model_copy(update={"environment_sha256": "9" * 64}) \
        if case == "historical_wrong_roles" else old
    identity = ProofIdentity(
        workspace.id, c.run.id, old.fencing_epoch, old.source_sha256, "3" * 64,
        old.schema_sha256, "a" * 64, role_binding.environment_sha256,
        role_binding.environment_sha256, "docker-owner-cell-resources-v2", "f" * 64,
    )
    prior_identity = ProofIdentity(**{**asdict(identity), "dependency_digest": "5" * 64})
    proof = ProjectCellProof(id=uuid4(), **asdict(identity), proof_key=identity.proof_key)
    c.session.add_all([
        proof, ProjectCellProof(
            id=uuid4(), **asdict(prior_identity), proof_key=prior_identity.proof_key,
        ),
    ])
    await c.session.flush()
    checked = now - timedelta(minutes=75)
    for ordinal, (phase, dimension) in enumerate(
        (("prepare", ProofDimension.BOOTSTRAP), ("fast_check", ProofDimension.FAST_CHECK)),
        start=2,
    ):
        operation_id = uuid4()
        c.session.add_all([
            ProjectCellActivityLease(
                operation_id=operation_id, workspace_id=workspace.id, generation_run_id=c.run.id,
                kind="command", state="completed", fencing_epoch=9, phase=phase,
                proof_key=prior_identity.proof_key, started_at=checked,
                deadline_at=checked + timedelta(minutes=1), heartbeat_at=checked,
                finished_at=checked,
            ),
            ProjectCellProofResult(
                id=uuid4(), proof_id=proof.id, workspace_id=workspace.id,
                dimension=dimension.value, dimension_key=identity.dimension_key(dimension),
                operation_id=operation_id, outcome="green", redacted_detail="ok",
                detail_digest=sha256(b"ok").hexdigest(),
            ),
        ])
        tool_call = f"role:{operation_id}"
        role_root = uuid5(c.run.id, f"recovery-tool-v1:{step}:{tool_call}")
        receipt = {
            "version": 1, "operation_id": str(operation_id),
            "workspace_id": str(workspace.id), "project_id": str(c.run.project_id),
            "owner_id": str(c.run.user_id), "generation_run_id": str(c.run.id),
            "dispatch_fencing_epoch": 9, "physical_volume": "fixture",
            "physical_volume_identity": "b" * 64, "request_sha256": "6" * 64,
            "before_revision": old.source_sha256, "after_revision": old.source_sha256,
            "response_sha256": "7" * 64,
        }
        c.session.add(GenerationRecoveryEffect(
            **scope, effect_id=uuid5(role_root, "physical:0"),
            logical_step_seq=step, effect_ordinal=ordinal, kind="tool",
            input_digest="d" * 64, state="confirmed", attempt_seq=c.driver.state.attempt_seq,
            claim_token=c.driver.state.continuation_claim.token,
            operation_id=operation_id, tool_call_id=tool_call,
            parent_effect_id=root if case == "historical_extra_effect" else None,
            pre_binding={
                "version": 1, "kind": "command_v1", "workspace_id": str(workspace.id),
                "fencing_epoch": 9, "workspace_revision": old.source_sha256,
                "request_sha256": "6" * 64, "context_ref": context_ref.model_dump(mode="json"),
                "context_binding": role_binding.model_dump(mode="json"),
                "activity": {
                    "operation_id": str(operation_id), "proof_key": prior_identity.proof_key,
                    "phase": phase,
                },
            },
            post_binding={
                "workspace_revision": old.source_sha256, "physical_volume_identity": "b" * 64,
            },
            receipt_ref=receipt, receipt_sha256=receipt_digest(receipt),
        ))


async def _held_repair(c, monkeypatch, case):
    now = datetime.now(UTC)
    historical = case.startswith("historical")
    source = BASE.replace(
        "EXTRA",
        'const payload=catalog.items;'
        'fetch("/api/products",{method:"POST",body:JSON.stringify(payload)});',
    ) if historical else MISSING_SELECTION_PAGE
    files = connection_files(source, ("catalog",))
    source_hash = workspace_revision_digest(files)
    c.run.execution_backend = "worker"
    c.run.response_mode = "build"
    workspace = ProjectCellWorkspace(
        project_id=c.run.project_id, owner_id=c.run.user_id, provider="docker_owner_canary",
        state="ready", fencing_epoch=11, generation_run_id=c.run.id,
    )
    c.session.add(workspace)
    await c.session.flush()
    old = RecoveryBinding(
        workspace_id=str(workspace.id), fencing_epoch=9,
        source_sha256=source_hash, schema_sha256="b" * 64, environment_sha256="c" * 64,
    )
    changed = case in {"rebound", "lost_environment"} or (
        historical and case != "historical_stable_environment"
    )
    current = old.model_copy(update={
        "fencing_epoch": 11, "environment_sha256": "e" * 64 if changed else old.environment_sha256,
    })

    request = {} if historical else {"require_environment_proof": {
        "source_sha256": old.source_sha256, "environment_sha256": old.environment_sha256,
    }}
    payload, operation_digest = _canonical_operation_envelope(
        workspace.id, c.run.id, "release", request,
    )
    release = ProjectCellOperation(
        id=uuid4(), workspace_id=workspace.id, generation_run_id=c.run.id,
        kind="release", status="completed", fencing_epoch=10,
        idempotency_key=f"generation:{c.run.id}:release:fixture:cycle:1",
        request_payload=payload, request_digest=operation_digest,
        finished_at=now - timedelta(hours=1), result_payload=ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", fencing_epoch=10,
            provider_ref="fixture", checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        ).to_wire_json(),
    )
    c.session.add(release)
    objects = {}

    async def store(project_id, run_id, data):
        assert (project_id, run_id) == (c.run.project_id, c.run.id)
        digest = sha256(data).hexdigest()
        key = f"retired-fixture/{digest}"
        objects[key] = data
        return key, digest

    async def load(project_id, run_id, *, object_key, digest):
        data = objects.get(object_key)
        return data if data is not None and sha256(data).hexdigest() == digest else None

    async def read_identity(workspace_id, *, generation_run_id, fencing_epoch):
        assert (workspace_id, generation_run_id, fencing_epoch) == (workspace.id, c.run.id, 11)
        return ProjectCellWorkspaceIdentity(
            source_hash, "d" * 64, current.schema_sha256,
            "a" * 64, current.environment_sha256, "f" * 64,
        )

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", load)
    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", read_identity)
    monkeypatch.setattr(recovery_requirements, "project_cell_agent_identity", read_identity)

    c.driver.snapshot.return_value = (current, files)
    c.driver.runtime_control = (workspace.id, 11)
    step = 86
    root = provider_root_effect_id(c.run.id, step)
    attempt_id = uuid5(root, "provider-attempt-v1")
    wire = uuid5(root, "wire-v1:0:0")
    digest = "e" * 64
    plan = ProviderDispatchPlan(
        kind="native", deadline=now - timedelta(minutes=70), outer_attempt=0,
        outer_allowance=1, wire_allowance=1,
    ).model_dump(mode="json")
    scope = dict(run_id=c.run.id, project_id=c.run.project_id, user_id=c.run.user_id)
    context = {
        "schema_version": 1, "entry": "pipeline",
        "pipeline": {"baseline": {"files": {}}, "prompt_text": _PROMPT},
        "active_frame": {"version": 1, "kind": "native", "value": {
            "step": 3, "request": {"model": "claude-sonnet-5.5"},
            "response_budget": {"total": 3, "since_progress": 1},
        }},
        "native_outer": {"segment": 1},
        "active_tool": None,
        "completed": {
            "source_repair": pipeline.SourceRepairEntry(
                task=_OLD_DETAIL, baseline={"src/app/page.tsx": "historical source"},
                edit_deadline=now - timedelta(minutes=55), targeted=False,
                response_budget=IncompleteResponseBudget(total=1),
            ).model_dump(mode="json"),
            "finalization_progress": pipeline.FinalizationProgress(
                source_history=(source_hash,), feedback=(_OLD_DETAIL,),
                pending_detail=_OLD_DETAIL, pending_fast_check=True,
            ).model_dump(mode="json"),
        },
    }
    authority = c.driver.state
    context_ref = await execution.store_execution_context(authority.identity, context, binding=old)
    retained = source_checkpoint.encode_checkpoint(
        **scope, baseline_sha=None, files=source_checkpoint.checkpoint_sources(files),
        deleted_paths=[],
    )
    retained_key, retained_digest = await store(c.run.project_id, c.run.id, retained)
    check = {"object_key": retained_key, "sha256": retained_digest}
    released = authority.model_copy(update={
        "cursor": "finalization", "binding": old,
        "checkpoint": CheckpointRef.model_validate(check), "context_ref": context_ref,
        "execution": authority.execution.model_copy(update={
            "stage": "finalization", "substep": "response_wait",
            "physical_state": "released", "control_workspace_id": workspace.id,
            "control_fencing_epoch": 10, "release_operation_id": release.id,
            "provider_effect_id": root, "pending_effect_id": root,
            "logical_step_seq": step, "execution_context_ref": context_ref,
            "lease_cycle_seq": 2,
            "budget": authority.execution.budget.model_copy(update={
                "steps": step, "repair_calls": 1, "provider_attempts": 86,
            }),
        }),
        "clock": authority.clock.model_copy(update={
            "stage": "repair", "repair_granted": True, "segment_used_ms": 3300000,
        }),
    })
    c.run.agent_state = {
        **c.run.agent_state, execution.RECOVERY_KEY: released.model_dump(mode="json"),
    }
    attempt = GenerationProviderAttempt(
        **scope, attempt_id=attempt_id, dispatch_token=uuid4(), prompt_hash=c.run.prompt_hash,
        request_sha256=digest,
        phase="reserved" if case == "unsealed" else "closed",
        terminal_reason=None if case == "unsealed" else "expired_before_dispatch",
    )
    c.session.add(attempt)
    if case == "other_open_attempt":
        c.session.add(GenerationProviderAttempt(
            **scope, attempt_id=uuid4(), dispatch_token=uuid4(), prompt_hash=c.run.prompt_hash,
            request_sha256="1" * 64, phase="reserved",
        ))
    await c.session.flush()
    receipt = {
        "version": 1, "kind": "gateway_expired_before_dispatch",
        "attempt_id": str(attempt_id), "wire_effect_id": str(wire), "root_effect_id": str(root),
        "request_sha256": digest, "source_sha256": old.source_sha256, "deadline": plan["deadline"],
    }
    historical_binding = old.model_dump(mode="json")
    if historical:
        historical_binding.update(fencing_epoch=7, environment_sha256="9" * 64)
    if case == "foreign_binding":
        historical_binding["source_sha256"] = "0" * 64
    for ordinal, effect_id in enumerate((root, wire)):
        dispatch = plan if ordinal == 0 else {
            **plan, "root_effect_id": str(root), "provider_effect_id": str(root),
            "attempt_id": str(attempt_id), "parent_attempt_id": None,
            "request_sha256": digest, "wire_attempt": 0,
        }
        c.session.add(GenerationRecoveryEffect(
            **scope, effect_id=effect_id, logical_step_seq=step, effect_ordinal=ordinal,
            kind="provider" if ordinal == 0 else "progress",
            parent_effect_id=root if ordinal else None,
            provider_attempt_id=attempt_id if ordinal == 0 else None,
            input_digest=digest, state="confirmed", receipt_ref=receipt,
            pre_binding={
                **({"source": historical_binding} if ordinal == 0 else {}),
                "dispatch_v1": dispatch,
            },
            attempt_seq=authority.attempt_seq, claim_token=authority.continuation_claim.token,
        ))
    if historical:
        await _historical_roles(c, workspace, old, root, step, context_ref, scope, now, case)
    await c.session.commit()
    c.driver = execution.ExecutionDriver(
        c.factory, released, context, c.driver.snapshot, runtime_control=(workspace.id, 11),
    )
    if changed or historical:
        assert c.driver.prove_environment_recovery is None
        proof = ProjectCellEnvironmentRecoveryProof(
            version=1, workspace_id=workspace.id, project_id=c.run.project_id,
            owner_id=c.run.user_id, generation_run_id=c.run.id, release_operation_id=release.id,
            previous_fencing_epoch=9, release_fencing_epoch=10, fencing_epoch=11,
            source_sha256=old.source_sha256, manifest_sha256="a" * 64,
            previous_environment_sha256=old.environment_sha256,
            environment_sha256=current.environment_sha256, artifact_sha256="1" * 64,
        )
        if case in {"lost_environment", "historical_lost_environment"}:
            proof = None
        c.driver.prove_environment_recovery = AsyncMock(return_value=proof)
    assert await expired_reservation_closed(c.session, attempt) is (
        case not in {"unsealed", "foreign_binding", "historical_extra_effect"}
    )
    return released, files, workspace, attempt, root


async def _compiler_after_retirement(
    c, workspace, *, with_bootstrap, check_historical_witness=True,
):
    binding = c.driver.state.binding
    identity = ProofIdentity(
        workspace_id=workspace.id, generation_run_id=c.run.id,
        fencing_epoch=binding.fencing_epoch, workspace_revision=binding.source_sha256,
        dependency_digest="d" * 64, schema_data_digest=binding.schema_sha256,
        cell_manifest_digest="a" * 64, base_image_digest=binding.environment_sha256,
        toolchain_digest=binding.environment_sha256,
        resource_profile_version="docker-owner-cell-resources-v2", build_config_digest="f" * 64,
    )
    proof = ProjectCellProof(
        id=uuid4(), proof_key=identity.proof_key,
        **{name: getattr(identity, name) for name in identity.__dataclass_fields__},
    )
    stale = datetime.now(UTC) - timedelta(minutes=5)
    lease = ProjectCellActivityLease(
        operation_id=uuid4(), workspace_id=workspace.id, generation_run_id=c.run.id,
        kind="command", phase="fast_check", state="completed",
        fencing_epoch=binding.fencing_epoch, proof_key=identity.proof_key,
        started_at=stale, finished_at=stale, heartbeat_at=stale,
        deadline_at=stale + timedelta(minutes=1), log_bytes=0,
    )
    result = ProjectCellProofResult(
        id=uuid4(), proof_id=proof.id, workspace_id=workspace.id,
        dimension="fast_check", dimension_key=identity.dimension_key(ProofDimension.FAST_CHECK),
        outcome="green", operation_id=lease.operation_id, redacted_detail="compile passed",
        detail_digest=sha256(b"compile passed").hexdigest(),
    )
    c.session.add_all([proof, lease, result])
    await c.session.commit()
    if with_bootstrap:
        prior = uuid4()
        await pipeline.run_durable_role(
            c.driver, role="bootstrap", operation_id=prior, invoke=AsyncMock(),
        )
        await pipeline.finish_recorded_role(c.driver, prior)
    await pipeline.run_durable_role(
        c.driver, role="fast_check", operation_id=lease.operation_id, invoke=AsyncMock(),
    )
    await pipeline.finish_recorded_role(c.driver, lease.operation_id)
    if check_historical_witness:
        with pytest.raises(ValueError, match="requires a fresh compiler"):
            await pipeline.revalidate_retired_source_repair(c.driver, compiler_receipt_id=result.id)
    fresh = datetime.now(UTC)
    lease.started_at = lease.finished_at = lease.heartbeat_at = fresh
    lease.deadline_at = fresh + timedelta(minutes=1)
    await c.session.commit()
    if check_historical_witness:
        with pytest.raises(ValueError, match="compiler command binding"):
            await pipeline.revalidate_retired_source_repair(c.driver, compiler_receipt_id=result.id)
    state = c.driver.state
    seq = state.execution.logical_step_seq
    tool_call = f"role:{lease.operation_id}"
    root = uuid5(c.run.id, f"recovery-tool-v1:{seq}:{tool_call}")
    command_ack = {
        "version": 1, "operation_id": str(lease.operation_id),
        "workspace_id": str(workspace.id), "project_id": str(c.run.project_id),
        "owner_id": str(c.run.user_id), "generation_run_id": str(c.run.id),
        "dispatch_fencing_epoch": binding.fencing_epoch, "physical_volume": "fixture",
        "physical_volume_identity": "6" * 64, "request_sha256": "7" * 64,
        "before_revision": binding.source_sha256, "after_revision": binding.source_sha256,
        "response_sha256": "8" * 64,
    }
    c.session.add(GenerationRecoveryEffect(
        effect_id=uuid5(root, "physical:0"), logical_step_seq=seq, effect_ordinal=0,
        run_id=c.run.id, project_id=c.run.project_id, user_id=c.run.user_id,
        attempt_seq=state.attempt_seq, claim_token=state.continuation_claim.token,
        kind="tool", state="confirmed", operation_id=lease.operation_id, tool_call_id=tool_call,
        input_digest="1" * 64, receipt_ref=command_ack, receipt_sha256=receipt_digest(command_ack),
        pre_binding={
            "version": 1, "kind": "command_v1", "workspace_id": str(workspace.id),
            "fencing_epoch": binding.fencing_epoch, "workspace_revision": binding.source_sha256,
            "request_sha256": "7" * 64, "context_ref": state.context_ref.model_dump(mode="json"),
            "context_binding": binding.model_dump(mode="json"),
            "activity": {
                "operation_id": str(lease.operation_id), "proof_key": lease.proof_key,
                "phase": "fast_check",
            },
        },
        post_binding={
            "workspace_revision": binding.source_sha256, "physical_volume_identity": "6" * 64,
        },
    ))
    await c.session.commit()
    return result.id


@pytest.mark.parametrize("case", [
    "owned", "rebound", "lost_environment", "unsealed", "foreign_binding", "stale_claim",
    "cancel", "other_open_attempt", "historical", "historical_lost_environment",
    "historical_extra_effect", "historical_wrong_roles",
])
async def test_sealed_expired_repair_retirement_preserves_checkpoint_and_counters(
    clock_owner, response_schema, monkeypatch, case,
):
    c = clock_owner
    released, files, workspace, attempt, root = await _held_repair(c, monkeypatch, case)
    async with c.owned():
        before = c.driver.state
        original_frame = c.driver.restore_frame("native")
        if case == "stale_claim":
            released = released.model_copy(update={
                "continuation_claim": released.continuation_claim.model_copy(
                    update={"token": uuid4()},
                ),
            })
        elif case == "cancel":
            await c.session.refresh(c.run)
            c.run.status = "cancel_requested"
            await c.session.commit()
        if case not in {"owned", "rebound", "historical"}:
            with pytest.raises(ValueError):
                await pipeline.retire_expired_native_source_repair(c.driver, released=released)
            assert c.driver.state == before and c.driver.restore_frame("native") == original_frame
            return

        await pipeline.retire_expired_native_source_repair(c.driver, released=released)
        await c.session.refresh(c.run)
        persisted = execution.recovery_state(c.run)
        assert persisted == c.driver.state
        assert persisted.identity == before.identity
        assert persisted.binding == c.driver.snapshot.return_value[0]
        assert persisted.checkpoint == before.checkpoint and persisted.clock == before.clock
        assert persisted.state_revision == before.state_revision + 1
        assert persisted.active_work_ms == before.active_work_ms
        assert persisted.execution.logical_step_seq == 86
        assert persisted.execution.budget == before.execution.budget
        assert persisted.execution.substep == "checkpoint_ready"
        assert persisted.execution.pending_effect_id is None
        assert persisted.execution.provider_effect_id is None
        assert c.driver.restore_frame("native") is None
        assert c.driver.context["active_frame"] is None and "native_outer" not in c.driver.context
        entry = pipeline.pending_source_repair(c.driver)
        assert entry.status == "complete" and entry.result["done"] is False
        assert entry.result["files"] == {} and entry.response_budget.total == 3
        assert await expired_reservation_closed(c.session, attempt)

        assert (await c.session.get(GenerationRecoveryEffect, root)).state == "confirmed"
        assert c.driver.snapshot.return_value == (persisted.binding, files)
        with pytest.raises(ValueError, match="lost its exact cursor"):
            await pipeline.revalidate_retired_source_repair(c.driver, compiler_receipt_id=uuid4())
        fresh_id = await _compiler_after_retirement(
            c, workspace, with_bootstrap=case == "historical",
        )
        verified = c.driver.state
        assert verified.execution.logical_step_seq == (88 if case == "historical" else 87)
        observation = await pipeline.revalidate_retired_source_repair(
            c.driver, compiler_receipt_id=fresh_id,
        )
        assert observation.compiler.passed and observation.compiler.binding == persisted.binding
        assert observation.source_gap == max_source_completion_gap(_PROMPT, files, portable=True)
        assert ("unproven_json_stringify" if case == "historical" else "useYleumCatalogSelection") \
            in observation.source_gap
        assert await expired_reservation_closed(c.session, attempt)
        with pytest.raises(ValueError):
            await pipeline.retire_expired_native_source_repair(c.driver, released=released)
        assert c.driver.state == verified


async def test_expired_unarmed_repair_enters_call_free_finalization(
    clock_owner, response_schema, monkeypatch,
):
    c = clock_owner
    released, _, _, attempt, _ = await _held_repair(c, monkeypatch, "rebound")
    async with c.owned():
        assert await pipeline.rebind_continuation(c.driver, allow_expired_retirement=True) is None
        assert pipeline.expired_repair_proof_only(c.driver)
        assert c.driver.state.identity.run_id == released.identity.run_id
        assert c.driver.state.execution.substep == "checkpoint_ready"
        assert c.driver.state.execution.pending_effect_id is None
        assert c.driver.state.execution.provider_effect_id is None
        assert c.driver.state.execution.budget == released.execution.budget
        assert c.driver.state.clock.segment_deadline == released.clock.segment_deadline
        assert c.driver.restore_frame("native") is None
        completed = pipeline.pending_source_repair(c.driver)
        assert completed is not None and completed.status == "complete"
        assert completed.result["needs_finalization"] is True and completed.result["done"] is False
        assert attempt.provider_call_id is None


async def _attach_repair_marker(c, released, attempt, *, claim_delta, marker_change):
    from yleum_api.services.generation.expired_provider_drain import clock_sha256

    claim = released.continuation_claim
    assert claim is not None
    updated = released.model_copy(update={
        "attempt_seq": released.attempt_seq + claim_delta,
        "state_revision": released.state_revision + claim_delta,
        "continuation_claim": claim.model_copy(update={
            "attempt_seq": claim.attempt_seq + claim_delta, "token": uuid4(),
        }),
    })
    cursor = released.execution
    marker = {
        "version": 1,
        "run_id": str(c.run.id),
        "attempt_id": str(attempt.attempt_id),
        "state_revision": released.state_revision,
        "attempt_seq": released.attempt_seq,
        "logical_step_seq": cursor.logical_step_seq,
        "binding": released.binding.model_dump(mode="json"),
        "context_ref": released.context_ref.model_dump(mode="json"),
        "checkpoint": released.checkpoint.model_dump(mode="json"),
        "release_operation_id": str(cursor.release_operation_id),
        "release_fencing_epoch": cursor.control_fencing_epoch,
        "lease_cycle_seq": cursor.lease_cycle_seq - 1,
        "clock_sha256": clock_sha256(released),
        **marker_change,
    }
    c.run.agent_state = {
        **c.run.agent_state,
        execution.RECOVERY_KEY: updated.model_dump(mode="json"),
        "expired_provider_attachment_v1": marker,
    }
    await c.session.commit()
    c.driver.state = updated
    return updated


@pytest.mark.parametrize("mode", [
    "default", "allowed", "claim_ack_lost", "attempt_mismatch", "clock_mismatch",
    "bad_shape", "no_proof", "unarmed", "prepare",
])
async def test_rebind_selects_only_an_armed_retirement_before_the_unchanged_environment(
    clock_owner, response_schema, monkeypatch, mode,
):
    c = clock_owner
    released, _, _, attempt, root = await _held_repair(
        c, monkeypatch, "historical_stable_environment",
    )
    if mode != "unarmed":
        marker_change = {}
        if mode == "attempt_mismatch":
            marker_change["attempt_id"] = str(uuid4())
        if mode == "clock_mismatch":
            marker_change["clock_sha256"] = "1" * 64
        if mode == "bad_shape":
            marker_change["version"] = True
        released = await _attach_repair_marker(
            c, released, attempt, claim_delta=2 if mode == "claim_ack_lost" else 1,
            marker_change=marker_change,
        )
    if mode == "no_proof":
        c.driver.prove_environment_recovery.return_value = None
    if mode == "prepare":
        c.driver.prepare_environment = AsyncMock()
        current = c.driver.snapshot.return_value
        c.driver.snapshot.side_effect = [
            OrchestratorMachineInventoryUnavailable(
                "stopped", released.binding.source_sha256, "a" * 64,
            ),
            current, current,
        ]
    async with c.owned():
        if mode == "unarmed":
            assert await pipeline.rebind_continuation(c.driver) is None
            assert c.driver.state.execution.provider_effect_id == root
            assert c.driver.restore_frame("native") is not None
            c.driver.prove_environment_recovery.assert_not_awaited()
            return
        if mode not in {"allowed", "claim_ack_lost", "prepare"}:
            expected = {
                "default": "requires an enabled retired repair coordinator",
                "attempt_mismatch": "owning no-dispatch receipt",
                "clock_mismatch": "attachment lost its claimed cursor",
                "bad_shape": "invalid expired provider attachment",
                "no_proof": "environment is not confirmed",
            }[mode]
            with pytest.raises(ValueError, match=expected):
                if mode == "default":
                    await pipeline.rebind_continuation(c.driver)
                else:
                    await pipeline.rebind_continuation(c.driver, allow_expired_retirement=True)
            await c.session.refresh(c.run)
            assert execution.recovery_state(c.run) == released
            assert c.driver.state == released and c.driver.restore_frame("native") is not None
            return

        assert await pipeline.rebind_continuation(c.driver, allow_expired_retirement=True) is True
        await c.session.refresh(c.run)
        persisted = execution.recovery_state(c.run)
        assert persisted.state_revision == released.state_revision + 1
        assert persisted.execution.budget == released.execution.budget
        assert persisted.clock == released.clock
        assert persisted.execution.physical_state == "active"
        assert c.driver.restore_frame("native") is None
        cold = execution.ExecutionDriver(
            c.factory, persisted,
            await execution.load_execution_context(
                persisted.identity, persisted.context_ref, binding=persisted.binding,
            ),
            c.driver.snapshot, runtime_control=c.driver.runtime_control,
            prove_environment_recovery=c.driver.prove_environment_recovery,
        )
        with pytest.raises(ValueError, match="requires an enabled retired repair coordinator"):
            await pipeline.rebind_continuation(cold)
        assert await pipeline.rebind_continuation(cold, allow_expired_retirement=True) is True
        assert cold.state == persisted
        assert c.driver.prove_environment_recovery.await_count == 1
        if mode == "prepare":
            c.driver.prepare_environment.assert_awaited_once_with(
                released.binding.source_sha256, "a" * 64,
            )
        if mode == "allowed":
            role = uuid4()
            await pipeline.run_durable_role(
                cold, role="fast_check", operation_id=role, invoke=AsyncMock(),
            )
            assert cold.state.execution.substep == "proof_wait"
            assert await pipeline.rebind_continuation(cold, allow_expired_retirement=True) is True


@pytest.mark.parametrize("committed", [False, True])
async def test_historical_native_retirement_has_one_checkpoint_commit_boundary(
    clock_owner, response_schema, monkeypatch, committed,
):
    c = clock_owner
    released, _, _, attempt, _ = await _held_repair(c, monkeypatch, "historical")
    save = recovery_store.save_recovery_checkpoint

    async def interrupted(*args, **kwargs):
        if committed:
            await save(*args, **kwargs)
        raise ConnectionError("synthetic checkpoint acknowledgement lost")

    async with c.owned():
        with monkeypatch.context() as owner:
            owner.setattr(recovery_store, "save_recovery_checkpoint", interrupted)
            with pytest.raises(ConnectionError, match="acknowledgement lost"):
                await pipeline.retire_expired_native_source_repair(c.driver, released=released)
        await c.session.refresh(c.run)
        persisted = execution.recovery_state(c.run)
        cold = execution.ExecutionDriver(
            c.factory, persisted,
            await execution.load_execution_context(
                persisted.identity, persisted.context_ref, binding=persisted.binding,
            ),
            c.driver.snapshot, runtime_control=c.driver.runtime_control,
            prove_environment_recovery=c.driver.prove_environment_recovery,
        )
        if committed:
            with pytest.raises(ValueError, match="exact released cursor"):
                await pipeline.retire_expired_native_source_repair(cold, released=released)
        else:
            assert persisted == released
            await pipeline.retire_expired_native_source_repair(cold, released=released)
        assert cold.state.state_revision == released.state_revision + 1
        assert cold.state.execution.substep == "checkpoint_ready"
        assert cold.state.execution.budget == released.execution.budget
        assert cold.restore_frame("native") is None
        assert await expired_reservation_closed(c.session, attempt)


@pytest.mark.parametrize(("crash", "role"), [
    ("before_intent", ProjectCellCommandRole.FAST_CHECK),
    ("after_command", ProjectCellCommandRole.FAST_CHECK),
    ("after_proof", ProjectCellCommandRole.FAST_CHECK),
    ("unknown", ProjectCellCommandRole.FAST_CHECK),
    ("foreign_proof", ProjectCellCommandRole.FAST_CHECK),
    ("before_intent", ProjectCellCommandRole.BOOTSTRAP),
    ("after_command", ProjectCellCommandRole.BOOTSTRAP),
])
async def test_retired_fast_check_keeps_one_pending_role_and_records_the_proof(
    clock_owner, response_schema, monkeypatch, crash, role,
):
    from yleum_api.services.project_cell_proofs import MAX_BOOTSTRAP_CONTRACT_VERSION

    c = clock_owner
    released, _, workspace, _, _ = await _held_repair(c, monkeypatch, "historical")
    async with c.owned():
        await pipeline.retire_expired_native_source_repair(c.driver, released=released)
        binding = c.driver.state.binding
        identity = ProofIdentity(
            workspace.id, c.run.id, binding.fencing_epoch, binding.source_sha256, "d" * 64,
            binding.schema_sha256, "a" * 64, binding.environment_sha256,
            binding.environment_sha256, "docker-owner-cell-resources-v2", "f" * 64,
        )
        bootstrap = role is ProjectCellCommandRole.BOOTSTRAP
        dimension = ProofDimension.BOOTSTRAP if bootstrap else ProofDimension.FAST_CHECK
        phase = "prepare" if bootstrap else "fast_check"
        contract = MAX_BOOTSTRAP_CONTRACT_VERSION if bootstrap else "v1"
        operation_id = uuid5(
            c.run.id, f"command:{workspace.id}:11:{identity.proof_key}:{role.value}:{contract}",
        )
        await pipeline.run_durable_role(
            c.driver, role=role.value, operation_id=operation_id, invoke=AsyncMock(),
        )
        before = c.driver.state
        tool_call = f"role:{operation_id}"
        root = uuid5(c.run.id, f"recovery-tool-v1:{before.execution.logical_step_seq}:{tool_call}")
        effect_id = uuid5(root, "physical:0")
        lease_start = datetime.now(UTC)
        observation = ProjectCellCommandObservation(
            operation_id, role, True, False, "compile passed", identity, identity, frozenset(),
        )

        async def read_command(expected_role, expected_id):
            cursor = _TOOL_EFFECT.get()
            assert cursor.reconcile_only and cursor.root_effect_id == root
            assert (expected_role, expected_id) == (role, operation_id)
            if crash == "unknown":
                raise ProjectCellExecutorUnavailable("command outcome remains unknown")
            return observation

        handle = SimpleNamespace(
            workspace_id=workspace.id, current_identity=AsyncMock(return_value=identity),
            operation_status=AsyncMock(), runtime_probe=AsyncMock(),
            run_role=AsyncMock(side_effect=read_command), prove_restoration_adaptation=None,
        )
        coordinator = MaxFinalizationCoordinator(
            session_factory=c.factory, generation_run_id=c.run.id, project_id=c.run.project_id,
            project_slug="fixture", executor=handle, continuation=c.driver,
        )

        async def first_command(**kwargs):
            cursor = _TOOL_EFFECT.get()
            assert not cursor.reconcile_only and cursor.root_effect_id == root
            assert kwargs["operation_id"] == observation.operation_id
            assert kwargs["identity"] == observation.before and kwargs["dimension"] is dimension
            assert kwargs["role"] is role and kwargs["phase"].value == phase
            return observation

        dispatch = AsyncMock(side_effect=first_command)
        monkeypatch.setattr(coordinator, "_execute_role_activity", dispatch)

        async def after_bootstrap():
            assert bootstrap and pipeline.pending_role(c.driver) is None
            outcome = await coordinator._find(await coordinator._proof(identity), dimension)
            assert outcome is not None and outcome.operation_id == operation_id
            return outcome

        monkeypatch.setattr(coordinator, "fast_check", AsyncMock(side_effect=after_bootstrap))

        if crash != "before_intent":
            command_ack = {
                "version": 1, "operation_id": str(operation_id), "workspace_id": str(workspace.id),
                "project_id": str(c.run.project_id), "owner_id": str(c.run.user_id),
                "generation_run_id": str(c.run.id), "dispatch_fencing_epoch": 11,
                "physical_volume": "fixture", "physical_volume_identity": "b" * 64,
                "request_sha256": "7" * 64, "response_sha256": "c" * 64,
                "before_revision": binding.source_sha256, "after_revision": binding.source_sha256,
            }
            c.session.add_all([
                ProjectCellActivityLease(
                    operation_id=operation_id, workspace_id=workspace.id,
                    generation_run_id=c.run.id, kind="command",
                    state="active" if crash == "unknown" else "completed",
                    fencing_epoch=11, phase=phase, proof_key=identity.proof_key,
                    started_at=lease_start, deadline_at=lease_start + timedelta(minutes=1),
                    heartbeat_at=lease_start,
                    finished_at=None if crash == "unknown" else lease_start,
                ),
                GenerationRecoveryEffect(
                    effect_id=effect_id, run_id=c.run.id, project_id=c.run.project_id,
                    user_id=c.run.user_id, logical_step_seq=before.execution.logical_step_seq,
                    effect_ordinal=0, kind="tool", input_digest="6" * 64,
                    state="inflight" if crash == "unknown" else "confirmed",
                    receipt_ref=command_ack if crash != "unknown" else None,
                    receipt_sha256=receipt_digest(command_ack) if crash != "unknown" else None,
                    post_binding={
                        "workspace_revision": binding.source_sha256,
                        "physical_volume_identity": "b" * 64,
                    } if crash != "unknown" else None,
                    attempt_seq=before.attempt_seq, claim_token=before.continuation_claim.token,
                    operation_id=operation_id, tool_call_id=tool_call, pre_binding={
                        "version": 1, "kind": "command_v1", "workspace_id": str(workspace.id),
                        "fencing_epoch": 11, "workspace_revision": binding.source_sha256,
                        "request_sha256": "7" * 64, "context_ref": before.context_ref.model_dump(
                            mode="json",
                        ),
                        "context_binding": binding.model_dump(mode="json"), "activity": {
                            "operation_id": str(operation_id), "proof_key": identity.proof_key,
                            "phase": phase,
                        },
                    },
                ),
            ])
            await c.session.commit()
        existing = None
        if crash in {"after_proof", "foreign_proof"}:
            existing = await coordinator._record(
                proof=await coordinator._proof(identity), dimension=dimension,
                outcome=ProofOutcome.GREEN, detail=observation.redacted_detail, artifact_ref=None,
                operation_id=operation_id if crash == "after_proof" else uuid4(),
            )

        c.driver = execution.ExecutionDriver(
            c.factory, before,
            await execution.load_execution_context(
                before.identity, before.context_ref, binding=before.binding,
            ),
            c.driver.snapshot, runtime_control=c.driver.runtime_control,
        )
        coordinator.continuation = c.driver
        if crash in {"unknown", "foreign_proof"}:
            with pytest.raises((ProjectCellExecutorUnavailable, MaxFinalizationConflict)):
                await coordinator.retired_fast_check()
            assert c.driver.state == before
        else:
            result = await coordinator.retired_fast_check()
            assert result.operation_id == operation_id and result.outcome == "green"
            assert existing is None or result.id == existing.id
            assert c.driver.state.execution.substep == "checkpoint_ready"
            assert pipeline.pending_role(c.driver) is None
            if not bootstrap and crash != "before_intent":
                verified = await pipeline.revalidate_retired_source_repair(
                    c.driver, compiler_receipt_id=result.id,
                )
                assert verified.compiler.passed and "unproven_json_stringify" in verified.source_gap
        assert c.driver.state.execution.logical_step_seq == before.execution.logical_step_seq
        assert c.driver.state.execution.budget == before.execution.budget
        assert c.driver.state.binding == before.binding
        assert dispatch.await_count == (1 if crash == "before_intent" else 0)
        assert handle.run_role.await_count == (0 if crash == "before_intent" else 1)
