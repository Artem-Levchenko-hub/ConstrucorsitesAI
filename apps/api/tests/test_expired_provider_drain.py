import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_recovery_deadlines import _clock
from tests.test_generation_recovery_deadlines import response_schema as response_schema
from tests.test_generation_recovery_execution import _bootstrap_wait
from tests.test_generation_worker import _queued_dispatch
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellProof,
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services import generation_deployment_drain as drain
from yleum_api.services.generation.provider_attempts import expired_reservation_closed
from yleum_api.services.generation.recovery_contract import (
    RecoveryContractError,
    RecoveryStateV3,
    decode_recovery,
)
from yleum_api.services.generation.recovery_tool_receipts import digest
from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity


@pytest.fixture
async def expired_release_head(db_session, test_engine):
    async with test_engine.begin() as connection:
        present = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if present:
            revisions = await connection.scalars(text("SELECT version_num FROM alembic_version"))
            original = revisions.all()
        else:
            await connection.execute(text("CREATE TABLE alembic_version(version_num text)"))
            original = None
    try:
        yield
    finally:
        await db_session.rollback()
        async with test_engine.begin() as connection:
            if original is None:
                await connection.execute(text("DROP TABLE alembic_version"))
            else:
                await connection.execute(text("DELETE FROM alembic_version"))
                for head in original:
                    await connection.execute(
                        text("INSERT INTO alembic_version VALUES(:head)"), {"head": head},
                    )


@pytest.mark.parametrize(
    ("mode", "case", "eligible", "drained"),
    [
        ("eligibility", "reserved", True, False),
        ("settled", "reserved", False, False),
        ("settled", "confirmed", True, True),
        ("settled", "bad_receipt", False, False),
        ("settled", "wrong_head", False, False),
        ("eligibility", "clock_change", False, False),
        ("eligibility", "outward", False, False),
        ("eligibility", "foreign_workspace", False, False),
        ("eligibility", "other_run", False, False),
        ("eligibility", "historical_roles", True, False),
        ("settled", "historical_roles", True, True),
        ("settled", "historical_lease_pending", False, False),
        ("settled", "historical_foreign_parent", False, False),
        ("settled", "historical_tampered_receipt", False, False),
        ("eligibility", "historical_no_previous_release", False, False),
        ("eligibility", "historical_source_changed", False, False),
        ("eligibility", "historical_tampered_proof", False, False),
        ("eligibility", "historical_wrong_role_post", False, False),
        ("settled", "historical_later_call", False, False),
        ("eligibility", "invented_wire_source", False, False),
    ],
)
async def test_expired_provider_release_classification(
    db_session, test_engine, monkeypatch, response_schema, expired_release_head,
    mode, case, eligible, drained,
):
    from yleum_api.services.generation import expired_provider_drain
    from yleum_api.services.generation.expired_provider_drain import (
        ExpiredProviderScope,
        classify_expired_provider,
        clock_sha256,
    )

    now = datetime.now(UTC)
    run = await _queued_dispatch(db_session)
    run.status, run.execution_backend, run.response_mode = "running", "worker", "build"
    workspace = ProjectCellWorkspace(
        id=uuid4(), project_id=run.project_id, owner_id=run.user_id,
        provider="docker_owner_canary", state="ready", fencing_epoch=10,
        generation_run_id=run.id if case == "foreign_workspace" else None,
    )
    db_session.add(workspace)
    await db_session.flush()
    release = ProjectCellOperation(
        id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id,
        idempotency_key="qa-released", request_digest="a" * 64,
        kind="release", status="completed", fencing_epoch=10, finished_at=now,
        result_payload=ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", provider_ref="qa",
            fencing_epoch=10, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
            has_postgres=True, has_redis=True,
        ).to_wire_json(),
    )
    db_session.add(release)
    root, token = uuid5(run.id, "recovery-provider-v1:86"), uuid4()
    wire, attempt = uuid5(root, "wire-v1:0:0"), uuid5(root, "provider-attempt-v1")
    refs = {"object_key": "qa-context", "sha256": "f" * 64}
    binding = {
        "workspace_id": str(workspace.id), "fencing_epoch": 9, "source_sha256": "c" * 64,
        "schema_sha256": "d" * 64, "environment_sha256": "e" * 64,
    }
    raw = _bootstrap_wait(run)
    state = decode_recovery({
        **raw, "version": 3, "phase": "needs_input", "next_check_at": None,
        "cursor": "finalization", "context_ref": refs, "checkpoint": refs, "binding": binding,
        "state_revision": 15, "clock": {**_clock(now - timedelta(seconds=5)),
                                       "stage": "repair", "repair_granted": True},
        "execution": {
            **raw["execution"], "stage": "finalization", "substep": "response_wait",
            "physical_state": "released", "release_operation_id": str(release.id),
            "control_workspace_id": str(workspace.id), "control_fencing_epoch": 10,
            "provider_effect_id": str(root), "pending_effect_id": str(root),
            "logical_step_seq": 86, "execution_context_ref": refs,
        },
    })
    assert isinstance(state, RecoveryStateV3)
    run.agent_state = {**run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    scope = ExpiredProviderScope(
        release_sha="a" * 40, run_id=run.id, attempt_id=attempt,
        state_revision=15, context_sha256=refs["sha256"], source_sha256="c" * 64,
        clock_sha256="a" * 64 if case == "clock_change" else clock_sha256(state),
    )
    terminal = mode == "settled" and case != "reserved"
    historical = {**binding, "fencing_epoch": 7}
    shared_roles = case.startswith("historical_")
    if shared_roles:
        historical["environment_sha256"] = "9" * 64
    if case == "historical_source_changed":
        historical["source_sha256"] = "8" * 64
    db_session.add(GenerationProviderAttempt(
        attempt_id=attempt, run_id=run.id, project_id=run.project_id, user_id=run.user_id,
        dispatch_token=uuid4(), prompt_hash=run.prompt_hash, request_sha256="b" * 64,
        phase="closed" if terminal else "reserved",
        terminal_reason="expired_before_dispatch" if terminal else None,
    ))
    await db_session.flush()
    plan = {
        "version": 1, "clock_basis": "utc", "kind": "native",
        "deadline": (now - timedelta(seconds=10)).isoformat(),
        "outer_attempt": 0, "outer_allowance": 1, "wire_allowance": 3,
    }
    receipt = {
        "version": 1, "kind": "gateway_expired_before_dispatch", "attempt_id": str(attempt),
        "root_effect_id": str(root), "wire_effect_id": str(wire), "request_sha256": "b" * 64,
        "source_sha256": "c" * 64, "deadline": plan["deadline"],
    }
    for ordinal, effect_id in enumerate((root, wire)):
        effect_state = "confirmed" if terminal else "inflight" if ordinal else "prepared"
        saved_receipt = {} if ordinal and case == "bad_receipt" else receipt
        db_session.add(GenerationRecoveryEffect(
            effect_id=effect_id, run_id=run.id, project_id=run.project_id, user_id=run.user_id,
            logical_step_seq=86, effect_ordinal=ordinal, kind="progress" if ordinal else "provider",
            input_digest="b" * 64, state=effect_state,
            attempt_seq=0, claim_token=token, provider_attempt_id=None if ordinal else attempt,
            parent_effect_id=root if ordinal else None,
            pre_binding={**({"source": historical} if not ordinal
                            or case == "invented_wire_source" else {}), "dispatch_v1": (
                {**plan, "root_effect_id": str(root), "provider_effect_id": str(root),
                 "attempt_id": str(attempt), "parent_attempt_id": None,
                 "request_sha256": "b" * 64, "wire_attempt": 0} if ordinal else plan
            )},
            post_binding={"outward_started": True} if ordinal and case == "outward" else None,
            receipt_ref=saved_receipt if terminal else None,
        ))
    if shared_roles:
        await db_session.flush()
        if case != "historical_no_previous_release":
            db_session.add(ProjectCellOperation(
                id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id,
                idempotency_key="qa-historical-release", request_digest="a" * 64,
                kind="release", status="completed", fencing_epoch=8,
                finished_at=now - timedelta(seconds=3),
                result_payload=ProjectCellResourceResponse(
                    workspace_id=workspace.id, state="resources_ready", provider_ref="qa",
                    fencing_epoch=8, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
                    has_postgres=True, has_redis=True,
                ).to_wire_json(),
            ))
        identity = ProofIdentity(
            workspace.id, run.id, 9, binding["source_sha256"], "2" * 64, binding["schema_sha256"],
            "3" * 64, binding["environment_sha256"], binding["environment_sha256"],
            "test", "4" * 64,
        )
        prior_identity = ProofIdentity(**{**asdict(identity), "dependency_digest": "5" * 64})
        proof = ProjectCellProof(id=uuid4(), **asdict(identity), proof_key=identity.proof_key)
        prior_proof = ProjectCellProof(
            id=uuid4(), **asdict(prior_identity), proof_key=prior_identity.proof_key,
        )
        db_session.add_all((proof, prior_proof))
        await db_session.flush()
        roles = (("prepare", ProofDimension.BOOTSTRAP), ("fast_check", ProofDimension.FAST_CHECK))
        for index, (phase, dimension) in enumerate(roles, start=2):
            operation_id = uuid4()
            lease = ProjectCellActivityLease(
                operation_id=operation_id, workspace_id=workspace.id, generation_run_id=run.id,
                kind="command",
                state="failed" if case == "historical_lease_pending" else "completed",
                fencing_epoch=9, phase=phase, proof_key=prior_identity.proof_key,
                started_at=now - timedelta(seconds=2), deadline_at=now,
                heartbeat_at=now - timedelta(seconds=1), finished_at=now,
            )
            db_session.add(lease)
            db_session.add(ProjectCellProofResult(
                id=uuid4(), proof_id=proof.id, workspace_id=workspace.id, dimension=dimension.value,
                dimension_key=identity.dimension_key(dimension), operation_id=operation_id,
                outcome="red" if case == "historical_tampered_proof" else "green",
                redacted_detail="ok", detail_digest=hashlib.sha256(b"ok").hexdigest(),
            ))
            tool_call = f"role:{operation_id}"
            role_root = uuid5(run.id, f"recovery-tool-v1:86:{tool_call}")
            receipt = {
                "version": 1, "operation_id": str(operation_id),
                "workspace_id": str(workspace.id), "project_id": str(run.project_id),
                "owner_id": str(run.user_id), "generation_run_id": str(run.id),
                "dispatch_fencing_epoch": 9, "physical_volume": "qa",
                "physical_volume_identity": "b" * 64, "request_sha256": "6" * 64,
                "before_revision": binding["source_sha256"],
                "after_revision": binding["source_sha256"], "response_sha256": "7" * 64,
            }
            receipt_sha256 = "1" * 64 if case == "historical_tampered_receipt" else digest(receipt)
            db_session.add(GenerationRecoveryEffect(
                effect_id=uuid5(role_root, "physical:0"), run_id=run.id, project_id=run.project_id,
                user_id=run.user_id, logical_step_seq=86, effect_ordinal=index, kind="tool",
                input_digest="d" * 64, state="confirmed", attempt_seq=0, claim_token=token,
                operation_id=operation_id, tool_call_id=tool_call,
                parent_effect_id=root if case == "historical_foreign_parent" else None,
                pre_binding={
                    "version": 1, "kind": "command_v1", "workspace_id": str(workspace.id),
                    "fencing_epoch": 9, "workspace_revision": binding["source_sha256"],
                    "request_sha256": "6" * 64, "context_ref": refs, "context_binding": binding,
                    "activity": {
                        "operation_id": str(operation_id), "proof_key": prior_identity.proof_key,
                        "phase": phase,
                    },
                },
                post_binding={
                    "workspace_revision": (
                        "1" * 64 if case == "historical_wrong_role_post"
                        else binding["source_sha256"]
                    ),
                    "physical_volume_identity": "b" * 64,
                },
                receipt_ref=receipt,
                receipt_sha256=receipt_sha256,
            ))
    await db_session.execute(text("DELETE FROM alembic_version"))
    await db_session.execute(text("INSERT INTO alembic_version VALUES(:head)"), {
        "head": "0107_expired_provider_reservation" if mode == "settled" and case != "wrong_head"
        else "0106_native_transcript_refusal",
    })
    if case == "other_run":
        await _queued_dispatch(db_session)
    if case == "historical_later_call":
        db_session.add(ProviderCall(
            id=uuid4(), provider_scope="llmgw", route="messages", requested_model="offline",
            expense_owner="user", user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            stage="repair", free=True, status="completed",
            created_at=now + timedelta(seconds=1), finished_at=now + timedelta(seconds=2),
        ))
    await drain.begin_drain(db_session, scope.release_sha)
    await db_session.commit()
    monkeypatch.setattr(
        expired_provider_drain, "require_recovery_readers", AsyncMock(return_value="a" * 64),
    )
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        report = await classify_expired_provider(session, scope, mode=mode)
        assert report.eligible is eligible
        assert report.sql_drained is drained
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        if shared_roles and terminal:
            attempt_row = await session.get(GenerationProviderAttempt, attempt)
            assert attempt_row is not None
            assert await expired_reservation_closed(session, attempt_row) is (
                drained or case == "historical_later_call"
            )


    scope_wire = {
        **asdict(scope), "version": 1, "run_id": str(scope.run_id),
        "attempt_id": str(scope.attempt_id),
    }
    incumbent = "b" * 40
    parse = drain.ExpiredProviderReleaseRequest.parse
    readers = expired_provider_drain.require_recovery_readers
    readers.reset_mock()
    request = parse(
        json.dumps({**scope_wire, "reader_release_sha": incumbent, "mode": mode}),
        release_sha=scope.release_sha, action="expired-provider-check",
    )
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        result, allowed = await drain._expired_provider_command(session, request)
        assert allowed is eligible
        assert result["drained"] is drained
        assert result["scope_sha256"] == request.scope_sha256
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        if eligible:
            assert readers.await_count == 2
            assert all(
                call.kwargs == {"release_sha": incumbent, "own": False}
                for call in readers.await_args_list
            )

    # The privileged retry can be in a mixed-reader restart. The SQL evidence
    # and both exact capture inputs still bind the selected scope.
    request = parse(
        json.dumps({
            **scope_wire, "owner_nonce": str(uuid4()), "core_capture_sha256": "5" * 64,
            "commerce_capture_sha256": "6" * 64,
        }),
        release_sha=scope.release_sha, action="resume-expired-provider-check",
    )
    readers.reset_mock()
    readers.side_effect = RecoveryContractError("no complete current reader set")
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        result, allowed = await drain._expired_provider_command(session, request)
        assert allowed is drained
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        readers.assert_not_awaited()

    # End requires compatible target readers and the exact selected scope, and
    # only the positive result deletes the existing row in the same transaction.
    request = parse(
        json.dumps(scope_wire), release_sha=scope.release_sha, action="end-expired-provider",
    )
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        result, allowed = await drain._expired_provider_command(session, request)
        assert not allowed
        assert await drain._current_drain(session) is not None
    readers.reset_mock()
    readers.side_effect = None
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        result, allowed = await drain._expired_provider_command(session, request)
        assert allowed is drained
        if drained:
            assert readers.await_count == 2
            assert all(
                call.kwargs == {"release_sha": scope.release_sha, "own": False}
                for call in readers.await_args_list
            )
        assert (await drain._current_drain(session) is None) is drained
        if drained:
            with pytest.raises(ValueError, match="requires its existing deployment drain"):
                await drain._expired_provider_command(session, request)
