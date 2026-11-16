from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4, uuid5

import pytest
from minio.error import S3Error
from sqlalchemy import select
from urllib3.exceptions import HTTPError as ObjectTransportError

from tests.test_generation_recovery_deadlines import clock_owner as clock_owner
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.services import project_cell_recovery_release as release
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_tool_receipts as tools
from yleum_api.services.generation import source_checkpoint
from yleum_api.services.generation.recovery_contract import CheckpointRef, RecoveryBinding
from yleum_api.services.generation.recovery_reconciliation import provider_root_effect_id
from yleum_api.services.generation_execution_context import execution_run_id
from yleum_api.services.orchestrator_client import (
    ProjectCellAgentExecResponse,
    ProjectCellCommandReceipt,
    ProjectCellCommandStatus,
    ProjectCellResourceResponse,
    ProjectCellSourceWriteReceipt,
    ProjectCellWorkspaceIdentity,
    command_wire_digest,
)
from yleum_api.services.project_cell_proofs import (
    MAX_BOOTSTRAP_CONTRACT_VERSION,
    ProofDimension,
    ProofIdentity,
    ProofOutcome,
    create_proof_identity,
    record_proof_result,
)
from yleum_api.services.project_cells import _canonical_operation_envelope
from yleum_api.services.promotion_permit import workspace_revision_digest
from yleum_api.workers.generation import _maintenance_candidates_query

_BASE_CASES = (
    "terminal", "unclaimed_release", "previously_confirmed", "confirmed_active", "executor_defer",
    "terminal_red", "terminal_timeout", "pending", "foreign", "changed_identity", "wrong_prefix",
    "future_effect", "wrong_tool", "prior_source", "unsettled_prior_source",
    "minio_error", "minio_transport", "minio_timeout",
)
_CACHE_REJECTIONS = (
    "cached_missing", "cached_red", "cached_schema", "cached_environment", "cached_fence",
    "cached_contract", "cached_current_proof", "cached_ordinal_gap",
    "cached_changes_during_receipt",
)


@pytest.mark.parametrize(("case", "cached_bootstrap"), [
    *[(case, cached) for case in _BASE_CASES for cached in (False, True)],
    *[(case, True) for case in _CACHE_REJECTIONS],
])
async def test_release_after_tracked_agent_compiler_ack_lost(
    clock_owner, monkeypatch, case, cached_bootstrap,
):
    c = clock_owner
    now = datetime.now(UTC)
    state, run = c.driver.state, c.run
    run.execution_backend = "worker"
    run.response_mode = "build"
    run.execution_started_at = now
    executor_defer = case == "executor_defer"
    unclaimed = case == "unclaimed_release" or executor_defer
    had_prior = case in {"prior_source", "unsettled_prior_source"}
    tool_index = int(had_prior)
    workspace = ProjectCellWorkspace(
        project_id=run.project_id, owner_id=run.user_id, provider="docker_owner_canary",
        state="ready", fencing_epoch=3 if unclaimed else 4, generation_run_id=run.id,
        provider_metadata={"profile_version": "docker-owner-cell-resources-v2"},
    )
    c.session.add(workspace)
    await c.session.flush()
    files = {"src/app/page.tsx": "export default function Page() { return null; }"}
    source = workspace_revision_digest(files)
    identity = ProjectCellWorkspaceIdentity(
        source, "a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64,
    )
    binding = RecoveryBinding(
        workspace_id=str(workspace.id), fencing_epoch=3, source_sha256=source,
        schema_sha256=identity.schema_data_digest, environment_sha256=identity.environment_digest,
    )
    proof = ProofIdentity(
        workspace_id=workspace.id, generation_run_id=run.id, fencing_epoch=3,
        workspace_revision=source, dependency_digest=identity.dependency_digest,
        schema_data_digest=identity.schema_data_digest,
        cell_manifest_digest=identity.cell_manifest_digest,
        base_image_digest=identity.environment_digest, toolchain_digest=identity.environment_digest,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest=identity.build_config_digest,
    )
    if cached_bootstrap:
        if case != "cached_current_proof":
            await create_proof_identity(c.session, identity=proof)
        previous_identity = replace(proof, workspace_revision="9" * 64)
        if case == "cached_schema":
            previous_identity = replace(previous_identity, schema_data_digest="f" * 64)
        elif case == "cached_environment":
            previous_identity = replace(previous_identity, base_image_digest="f" * 64)
        elif case == "cached_fence":
            previous_identity = replace(previous_identity, fencing_epoch=2)
        previous_proof = await create_proof_identity(
            c.session, identity=previous_identity,
        )
        if case != "cached_missing":
            cached_result = await record_proof_result(
                c.session, proof=previous_proof, dimension=ProofDimension.BOOTSTRAP,
                outcome=ProofOutcome.RED if case == "cached_red" else ProofOutcome.GREEN,
                operation_id=uuid4(), artifact_ref=None,
                detail="verified platform from the preceding source revision",
            )
            if case == "cached_contract":
                cached_result.dimension_key = "f" * 64
    key = "legacy-action" if case == "wrong_tool" else "legacy-deadline-build"
    step = 83
    tool_id = uuid5(run.id, f"recovery-tool-v1:{step}:{key}")
    provider_id = provider_root_effect_id(run.id, step)
    context = {
        "schema_version": 1, "entry": "pipeline",
        "pipeline": {"baseline": {"files": {}}},
        "completed": {},
        "active_frame": {"version": 1, "kind": "legacy", "value": {}},
        "active_tool": {
            "version": 1, "index": tool_index, "key": key,
            "input_digest": tools.digest({"name": "build", "args": {}}),
        },
    }
    ref = await execution.store_execution_context(state.identity, context, binding=binding)
    release_payload, release_digest = _canonical_operation_envelope(
        workspace.id, run.id, "release", {},
    )
    release_op = ProjectCellOperation(
        id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id,
        execution_run_id=None if unclaimed else run.id,
        kind="release", status="pending" if unclaimed else "indeterminate",
        fencing_epoch=None if unclaimed else 4, attempt_count=int(not unclaimed),
        idempotency_key=f"generation:{run.id}:release:docker-owner-cell-resources-v2:cycle:2",
        request_digest=release_digest, request_payload=release_payload,
    )
    if not executor_defer:
        c.session.add(release_op)
    state = state.model_copy(update={
        "phase": "waiting_dependency" if case == "unclaimed_release" else "working",
        "next_check_at": now if case == "unclaimed_release" else None, "cursor": "agent",
        "binding": binding, "context_ref": ref,
        "checkpoint": CheckpointRef(object_key="synthetic-checkpoint", sha256="f" * 64),
        "execution": state.execution.model_copy(update={
            "stage": "agent", "substep": "tool_wait", "logical_step_seq": step,
            "next_tool_index": tool_index,
            "provider_effect_id": provider_id, "pending_effect_id": tool_id,
            "execution_context_ref": ref,
            "physical_state": "active" if executor_defer else "release_pending",
            "control_workspace_id": workspace.id, "control_fencing_epoch": 3,
            "release_operation_id": None if executor_defer else release_op.id, "lease_cycle_seq": 2,
        }),
    })
    run.agent_state = {**run.agent_state, execution.RECOVERY_KEY: state.model_dump(mode="json")}
    if had_prior:
        previous_root = uuid5(run.id, f"recovery-tool-v1:{step}:legacy-action")
        previous_effect = uuid5(previous_root, "physical:0")
        previous_op = uuid5(previous_effect, "controller-operation-v1")
        previous_binding = tools.SourceWriteBinding(
            workspace_id=workspace.id, fencing_epoch=3, workspace_revision="0" * 64,
            request_sha256="1" * 64, context_ref=ref, context_binding=binding,
        )
        previous_receipt = ProjectCellSourceWriteReceipt(
            version=1, operation_id=previous_op, workspace_id=workspace.id,
            project_id=run.project_id, owner_id=run.user_id, generation_run_id=run.id,
            fencing_epoch=3, physical_volume="owned", physical_volume_identity="a" * 64,
            request_sha256=previous_binding.request_sha256,
            before_revision=previous_binding.workspace_revision,
            after_revision=source, written=1, deleted=0,
        )
        encoded = {
            field: str(value) if isinstance(value, UUID) else value
            for field, value in asdict(previous_receipt).items()
        }
        previous_confirmed = case == "prior_source"
        previous_input = tools.digest({"name": "write_file", "args": {}})
        previous_result = await execution.store_execution_context(
            state.identity, {
                "schema_version": 1, "entry": "result", "kind": "tool_result_v1",
                "input_digest": previous_input, "observation": {"ok": True},
            }, binding=binding,
        )
        c.session.add_all([
            GenerationRecoveryEffect(
                effect_id=previous_effect, run_id=run.id, project_id=run.project_id,
                user_id=run.user_id, logical_step_seq=step, effect_ordinal=2, kind="tool",
                input_digest=previous_input,
                state="confirmed" if previous_confirmed else "inflight",
                operation_id=previous_op, attempt_seq=state.attempt_seq,
                claim_token=state.continuation_claim.token, tool_call_id="legacy-action",
                pre_binding=previous_binding.model_dump(mode="json"),
                receipt_ref=encoded if previous_confirmed else None,
                receipt_sha256=tools.digest(encoded) if previous_confirmed else None,
                post_binding={"workspace_revision": source, "physical_volume_identity": "a" * 64}
                if previous_confirmed else None,
            ),
            GenerationRecoveryEffect(
                effect_id=uuid5(previous_root, "result-v1"),
                run_id=run.id, project_id=run.project_id,
                user_id=run.user_id, logical_step_seq=step, effect_ordinal=3, kind="tool",
                input_digest=previous_input, state="confirmed", attempt_seq=state.attempt_seq,
                claim_token=state.continuation_claim.token, tool_call_id="legacy-action",
                pre_binding=tools.ToolResultBinding(
                    index=0, context_binding=binding,
                ).model_dump(mode="json"),
                checkpoint_ref=previous_result.model_dump(mode="json"),
            ),
        ])
    roles = (
        (("fast_check", "fast_check"),) if cached_bootstrap
        else (("bootstrap", "prepare"), ("fast_check", "fast_check"))
    )
    for ordinal, (role, phase) in enumerate(roles):
        bootstrap = role == "bootstrap"
        contract = MAX_BOOTSTRAP_CONTRACT_VERSION if bootstrap else "v1"
        op_id = uuid5(run.id, f"command:{workspace.id}:3:{proof.proof_key}:{role}:{contract}")
        command = tools.CommandContext(
            cmd=f"omnia:{role}", timeout_seconds=900 if bootstrap else 480,
            task_role=role, before_files=files,
        )
        command_ref = await execution.store_execution_context(
            state.identity, command.model_dump(mode="json"), binding=binding,
        )
        bound = tools.CommandBinding(
            workspace_id=workspace.id, fencing_epoch=3, workspace_revision=source,
            request_sha256="0" * 64, context_ref=command_ref, context_binding=binding,
            activity=tools.CommandActivityBinding(
                operation_id=op_id, phase=phase, proof_key=proof.proof_key,
            ),
        )
        bound = bound.model_copy(update={
            "request_sha256": tools._command_request_hash(bound, op_id, run.id, command),
        })
        timed_out = not bootstrap and case == "terminal_timeout"
        exit_code = 124 if timed_out else int(not bootstrap and case == "terminal_red")
        response = ProjectCellAgentExecResponse(
            ok=exit_code == 0, exit_code=exit_code, detail="synthetic compiler terminal",
            timed_out=timed_out,
            workspace_revision=source, operation_id=op_id, before_identity=identity,
            after_identity=identity,
        )
        receipt = ProjectCellCommandReceipt(
            version=1, operation_id=op_id, workspace_id=workspace.id,
            project_id=run.project_id, owner_id=run.user_id, generation_run_id=run.id,
            dispatch_fencing_epoch=3, physical_volume="owned", physical_volume_identity="a" * 64,
            request_sha256=bound.request_sha256, before_revision=source, after_revision=source,
            response_sha256=command_wire_digest(response.command_wire_json()),
        )
        ref_data = {
            field: str(value) if isinstance(value, UUID) else value
            for field, value in asdict(receipt).items()
        }
        finished = bootstrap or case == "previously_confirmed"
        confirmed = finished or case == "confirmed_active"
        effect_id = uuid5(tool_id, f"physical:{ordinal}")
        if case == "cached_ordinal_gap":
            effect_id = uuid5(tool_id, "physical:1")
        c.session.add(GenerationRecoveryEffect(
            effect_id=uuid4()
            if case == "wrong_prefix" and (bootstrap or cached_bootstrap) else effect_id,
            run_id=run.id, project_id=run.project_id, user_id=run.user_id,
            logical_step_seq=step, effect_ordinal=ordinal + 2 + 2 * tool_index, kind="tool",
            input_digest=tools.digest({
                "cmd": command.cmd, "timeout_seconds": command.timeout_seconds, "task_role": role,
            }),
            state="confirmed" if confirmed else "inflight", operation_id=op_id,
            attempt_seq=state.attempt_seq, claim_token=state.continuation_claim.token,
            tool_call_id=key, pre_binding=bound.model_dump(mode="json"),
            receipt_ref=ref_data if confirmed else None,
            receipt_sha256=tools.digest(ref_data) if confirmed else None,
            post_binding={"workspace_revision": source, "physical_volume_identity": "a" * 64}
            if confirmed else None,
        ))
        c.session.add(ProjectCellActivityLease(
            operation_id=op_id, workspace_id=workspace.id, generation_run_id=run.id,
            kind="command", phase=phase, proof_key=proof.proof_key, fencing_epoch=3,
            state="completed" if finished else "active",
            started_at=now - timedelta(seconds=50), heartbeat_at=now - timedelta(seconds=40),
            finished_at=now - timedelta(seconds=40) if finished else None,
            deadline_at=now + timedelta(minutes=1), log_bytes=0,
        ))
    if case == "future_effect":
        c.session.add(GenerationRecoveryEffect(
            effect_id=uuid4(), run_id=run.id, project_id=run.project_id, user_id=run.user_id,
            logical_step_seq=step + 1, effect_ordinal=0, kind="progress", input_digest="a" * 64,
            state="inflight", attempt_seq=state.attempt_seq,
            claim_token=state.continuation_claim.token, pre_binding={},
        ))
    await c.session.commit()

    async def read_command(workspace_id, *, operation_id, generation_run_id, fencing_epoch):
        assert (workspace_id, operation_id, generation_run_id, fencing_epoch) == (
            workspace.id, op_id, run.id, 3,
        )
        if case == "pending":
            return ProjectCellCommandStatus(op_id, "pending", None, None)
        if case == "cached_changes_during_receipt":
            cached_result.outcome = ProofOutcome.RED.value
            await c.session.commit()
        wire_receipt = receipt
        if case == "foreign":
            wire_receipt = replace(receipt, dispatch_fencing_epoch=2)
        return ProjectCellCommandStatus(op_id, "terminal", wire_receipt, response)

    async def read_identity(workspace_id, *, generation_run_id, fencing_epoch):
        assert (workspace_id, generation_run_id, fencing_epoch) == (workspace.id, run.id, 3)
        if case == "changed_identity":
            return replace(identity, schema_data_digest="c" * 64)
        return identity

    async def release_resources(request):
        assert request.workspace_id == workspace.id
        if not executor_defer:
            assert request.operation_id == release_op.id and request.fencing_epoch == 4
        assert request.request_digest == release_digest
        assert request.require_environment_proof is None
        return ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", provider_ref="owned",
            fencing_epoch=4, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
            has_postgres=True, has_redis=True,
        )

    from yleum_api.services import orchestrator_client

    command_reader = AsyncMock(side_effect=read_command)
    dispatch_release = AsyncMock(side_effect=release_resources)
    monkeypatch.setattr(orchestrator_client, "project_cell_agent_command_operation", command_reader)
    monkeypatch.setattr(orchestrator_client, "project_cell_agent_identity", read_identity)
    monkeypatch.setattr(
        release.HttpProjectCellOrchestratorClient, "control", dispatch_release,
    )
    storage_error = {
        "minio_error": S3Error("ServiceUnavailable", "synthetic", "/", "r", "h", None),
        "minio_transport": ObjectTransportError("synthetic network failure"),
        "minio_timeout": TimeoutError("synthetic read timeout"),
    }.get(case)
    if storage_error is not None:
        def unavailable(*args):
            raise storage_error

        monkeypatch.setattr(source_checkpoint, "_get_checkpoint", unavailable)

    scheduled = (await c.session.execute(_maintenance_candidates_query([]))).all()
    async with c.owned():
        token = execution_run_id.set(run.id)
        try:
            if executor_defer:
                from yleum_api.services.project_cell_executor import (
                    ProjectCellExecutorUnavailable,
                    _release_generation_lease,
                )

                try:
                    await _release_generation_lease(
                        session_factory=c.factory, workspace_id=workspace.id,
                        generation_run_id=run.id, profile_version="docker-owner-cell-resources-v2",
                    )
                except ProjectCellExecutorUnavailable:
                    pass
            else:
                await release.resume_recovery_release(c.factory, run.id, state)
        finally:
            execution_run_id.reset(token)
    await c.session.refresh(run)
    saved = execution.recovery_state(run)
    activity = await c.session.scalar(select(ProjectCellActivityLease).where(
        ProjectCellActivityLease.operation_id == op_id,
    ))
    if executor_defer:
        dispatch_release.assert_not_awaited()
        command_reader.assert_not_awaited()
        await c.session.refresh(workspace)
        saved_release = await c.session.get(
            ProjectCellOperation, saved.execution.release_operation_id,
        )
        assert saved_release is not None and saved_release.status == "pending"
        assert saved_release.request_digest == release_digest
        assert saved_release.fencing_epoch is None
        assert workspace.fencing_epoch == 3 and workspace.generation_run_id == run.id
        assert activity.state == "active"
        assert saved.execution.physical_state == "release_pending"
        assert saved.execution.control_fencing_epoch == 3
        assert saved.execution.pending_effect_id == tool_id
    elif case in {
        "terminal", "unclaimed_release", "previously_confirmed", "confirmed_active",
        "terminal_red", "terminal_timeout", "prior_source",
    }:
        assert saved.execution.physical_state == "released"
        assert any(candidate[0] == run.id for candidate in scheduled)
        assert saved.execution.control_fencing_epoch == 4
        assert saved.execution.pending_effect_id == tool_id
        assert saved.execution.logical_step_seq == step
        assert saved.execution.next_tool_index == tool_index
        assert saved.execution.budget == state.execution.budget and saved.clock == state.clock
        assert saved.context_ref == ref and saved.binding == binding
        expected_activity = {"terminal_red": "failed", "terminal_timeout": "timed_out"}
        assert activity.state == expected_activity.get(case, "completed")
        if case == "previously_confirmed":
            command_reader.assert_not_awaited()
        else:
            command_reader.assert_awaited_once()
        dispatch_release.assert_awaited_once()
    else:
        assert saved == state and activity.state == "active"
        dispatch_release.assert_not_awaited()
        if case == "wrong_tool":
            assert all(candidate[0] != run.id for candidate in scheduled)
        if storage_error is not None:
            assert any(candidate[0] == run.id for candidate in scheduled)
            command_reader.assert_not_awaited()
            await c.session.refresh(release_op)
            assert datetime.now(UTC) < release_op.next_attempt_at <= now + timedelta(seconds=35)
            assert release_op.status == "indeterminate" and release_op.fencing_epoch == 4
            assert release_op.request_digest == release_digest and release_op.attempt_count == 1
            retry_scan = (await c.session.execute(_maintenance_candidates_query([]))).all()
            assert all(candidate[0] != run.id for candidate in retry_scan)
