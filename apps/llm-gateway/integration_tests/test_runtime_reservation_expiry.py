"""Local PostgreSQL tests for the private ordinary-release expiry action."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import httpx
import pytest
from fastapi import FastAPI
from test_historical_reservation_settlement import _digest, _parked_intent
from test_historical_reservation_settlement import database_url as database_url
from test_historical_reservation_settlement import pool as pool

from yleum_gateway.core import generation_response_auth as auth
from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.ops import expired_provider_reservation as cli
from yleum_gateway.routers import generation_responses
from yleum_gateway.services import generation_attempts as attempts

_SECRET = "synthetic-local-gateway-runtime-expiry-only"
_PROFILE = "docker-owner-cell-resources-v2"
_CONTEXT_DIGEST = "d" * 64


def _token(identity, *, purpose="generation_attempt.expire", revision=107):
    now = int(datetime.now(UTC).timestamp())
    return auth._signed(
        {
            "aud": "llm-gateway", "purpose": purpose, "identity": identity.to_wire(),
            "state_revision": revision, "context_sha256": _CONTEXT_DIGEST,
            "source_sha256": "c" * 64, "iat": now, "exp": now + 60,
        },
        _SECRET,
        auth._DOMAIN,
    )


async def _runtime_intent(
    pool, *, is_due=True, scheduler_due=True, changed_bridge=False, advanced=False, catalog=None,
):
    old, release_sha, source, _, _ = await _parked_intent(pool)
    await attempts.close_expired_before_dispatch(
        attempts.ExpiredReservationScope(old, release_sha, 105, source),
    )
    root_id = uuid5(old.run_id, "recovery-provider-v1:93")
    wire_id = uuid5(root_id, "wire-v1:0:0")
    identity = replace(old, attempt_id=uuid5(root_id, "provider-attempt-v1"))
    now = datetime.now(UTC)
    next_check = now + timedelta(seconds=-2 if scheduler_due else 300)
    claim = uuid4()
    release = uuid4()
    final_fence = 16 if advanced else 14
    final_cycle = 5 if advanced else 4
    plan = {
        "version": 1, "clock_basis": "utc", "kind": "native",
        "deadline": (now + timedelta(seconds=-30 if is_due else 300)).isoformat(),
        "outer_attempt": 0, "outer_allowance": 1, "wire_allowance": 3,
    }
    wire = {
        **plan, "root_effect_id": str(root_id), "provider_effect_id": str(root_id),
        "attempt_id": str(identity.attempt_id), "parent_attempt_id": None,
        "request_sha256": identity.request_sha256, "wire_attempt": 0,
    }

    async with pool.acquire() as conn, conn.transaction():
        raw = await conn.fetchval("SELECT agent_state FROM generation_runs WHERE id=$1", old.run_id)
        agent = json.loads(raw)
        state = agent["recovery_v1"]
        historical = {**state["binding"], "fencing_epoch": 11}
        current = {**historical, "fencing_epoch": final_fence - 1,
                   "environment_sha256": "4" * 64}
        workspace = historical["workspace_id"]
        context = {"object_key": "synthetic-runtime-context", "sha256": _CONTEXT_DIGEST}
        state.update(
            attempt_seq=4, state_revision=107, binding=current, context_ref=context,
            phase="waiting_dependency",
            next_check_at=next_check.isoformat(),
            clock={"wait_dependency": None},
            continuation_claim={
                "run_id": str(old.run_id), "attempt_seq": 4, "token": str(uuid4()),
                "worker_id": "generation-worker", "expires_at": now.isoformat(),
            },
        )
        state["execution"].update(
            logical_step_seq=93, provider_effect_id=str(root_id), pending_effect_id=str(root_id),
            release_operation_id=str(release), control_fencing_epoch=final_fence,
            execution_context_ref=context, lease_cycle_seq=final_cycle, next_tool_index=0,
            response_sha256=None, budget={"repair_calls": 3 if catalog is not None else 2},
        )
        if catalog is not None:
            recovery_deadline = now - timedelta(seconds=20)
            state["clock"].update(
                stage="repair", repair_granted=True, segment_seq=3,
                segment_deadline=recovery_deadline.isoformat(),
            )
            if catalog != "catalog_unarmed":
                agent["retired_catalog_repair_resume_v1"] = {
                    "version": 1,
                    "segment_seq": 3 if catalog == "catalog_armed" else 2,
                    "deadline": recovery_deadline.isoformat(),
                    "predecessor_attachment": None,
                }
        await conn.execute("DELETE FROM deployment_drains WHERE scope='generation'")
        await conn.execute(
            "UPDATE generation_runs SET agent_state=$2::jsonb,execution_started_at=$3,started_at=$3 "
            "WHERE id=$1", old.run_id, json.dumps(agent), now - timedelta(hours=2),
        )
        await conn.execute(
            "UPDATE project_cell_workspaces SET fencing_epoch=$2 WHERE id=$1", workspace, final_fence,
        )

        async def finished_op(kind, fence, offset, operation, request, cycle):
            payload = {
                "workspace_id": workspace, "generation_run_id": str(old.run_id),
                "kind": kind, "request": request,
            }
            reply = {
                "workspace_id": workspace, "fencing_epoch": fence, "state": "resources_ready",
                "provider_ref": "fixture", "has_workspace": True, "has_agent_home": True,
                "has_postgres": True, "has_redis": True,
            }
            finished = now - timedelta(seconds=offset)
            await conn.execute(
                "INSERT INTO project_cell_operations("
                "id,workspace_id,generation_run_id,execution_run_id,idempotency_key,request_digest,"
                "request_payload,kind,status,fencing_epoch,started_at,finished_at,attempt_count,"
                "result_payload) VALUES("
                "$1,$2,$3,$3,$4,$5,$6::jsonb,$7,'completed',$8,$9,$10,1,$11::jsonb)",
                operation, workspace, old.run_id, f"generation:{old.run_id}:{kind}:{_PROFILE}:cycle:{cycle}",
                _digest(payload), json.dumps(payload), kind, fence,
                finished - timedelta(seconds=1), finished, json.dumps(reply),
            )

        await finished_op("release", 12, 45, uuid4(), {
            "require_environment_proof": {
                "source_sha256": source,
                "environment_sha256": "5" * 64 if changed_bridge else historical["environment_sha256"],
            },
        }, 3)
        await finished_op("ensure", 13, 35, uuid4(), {"profile_version": _PROFILE}, 4)
        if advanced:
            await finished_op("release", 14, 25, uuid4(), {
                "require_environment_proof": {
                    "source_sha256": source, "environment_sha256": "3" * 64,
                },
            }, 4)
            await finished_op("ensure", 15, 15, uuid4(), {"profile_version": _PROFILE}, 5)
        await finished_op("release", final_fence, 10, release, {
            "require_environment_proof": {
                "source_sha256": source, "environment_sha256": current["environment_sha256"],
            },
        }, final_cycle)
        await conn.execute(
            "INSERT INTO generation_provider_attempts"
            "(attempt_id,run_id,project_id,user_id,prompt_hash,request_sha256,dispatch_token,created_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
            identity.attempt_id, old.run_id, old.project_id, old.user_id, old.prompt_hash,
            identity.request_sha256, uuid4(), now - timedelta(seconds=55),
        )
        sql = (
            "INSERT INTO generation_recovery_effects(effect_id,run_id,project_id,user_id,"
            "logical_step_seq,effect_ordinal,kind,input_digest,state,attempt_seq,claim_token,"
            "provider_attempt_id,parent_effect_id,pre_binding,created_at) "
            "VALUES($1,$2,$3,$4,93,$5,$6,$7,$8,3,$9,$10,$11,$12::jsonb,$13)"
        )
        args = identity.run_id, identity.project_id, identity.user_id
        await conn.execute(
            sql, root_id, *args, 0, "provider", identity.request_sha256, "prepared", claim,
            identity.attempt_id, None, json.dumps({"source": historical, "dispatch_v1": plan}),
            now - timedelta(seconds=55),
        )
        await conn.execute(
            sql, wire_id, *args, 1, "progress", identity.request_sha256, "inflight", claim,
            None, root_id, json.dumps({"dispatch_v1": wire}), now - timedelta(seconds=54),
        )
    return identity, old, root_id, wire_id


async def _historical_reconciliation(pool, identity, *, confirmed, executor="original"):
    old_operation, reconcile = uuid4(), uuid4()
    async with pool.acquire() as conn:
        workspace = await conn.fetchval(
            "SELECT id FROM project_cell_workspaces WHERE project_id=$1", identity.project_id,
        )
        original_run = identity.run_id
        unrelated_run = None
        if executor != "original":
            original_run = uuid4()
            run_ids = [original_run]
            if executor == "unrelated":
                unrelated_run = uuid4()
                run_ids.append(unrelated_run)
            for run_id in run_ids:
                await conn.execute(
                    "INSERT INTO generation_runs(id,project_id,user_id,idempotency_key,prompt_hash,"
                    "status,execution_backend,response_mode) VALUES($1,$2,$3,$4,$5,'failed',"
                    "'worker','build')",
                    run_id, identity.project_id, identity.user_id, str(run_id), identity.prompt_hash,
                )
        for operation, kind, fence, status in (
            (old_operation, "ensure", 5, "indeterminate"),
            (reconcile, "reconcile", 6, "completed"),
        ):
            request = (
                {"profile_version": _PROFILE} if kind == "ensure"
                else {"indeterminate_operation_id": str(old_operation)}
            )
            envelope = {
                "workspace_id": str(workspace), "generation_run_id": str(original_run),
                "kind": kind, "request": request,
            }
            reply = {
                "workspace_id": str(workspace), "fencing_epoch": fence, "state": "resources_ready",
                "provider_ref": "fixture", "has_workspace": True, "has_agent_home": True,
                "has_postgres": True, "has_redis": True,
                "reconciles_operation_id": str(old_operation if confirmed else uuid4()),
            }
            await conn.execute(
                "INSERT INTO project_cell_operations("
                "id,workspace_id,generation_run_id,execution_run_id,idempotency_key,request_digest,"
                "request_payload,kind,status,fencing_epoch,started_at,finished_at,attempt_count,"
                "result_payload) VALUES($1,$2,$3,$13,$4,$5,$6::jsonb,$7,$8,$9,"
                "now()-make_interval(secs=>$10),now()-make_interval(secs=>$11),1,$12::jsonb)",
                operation, workspace, original_run, f"historic:{kind}:{operation}",
                _digest(envelope), json.dumps(envelope), kind, status, fence,
                105 if kind == "ensure" else 90, 100 if kind == "ensure" else 80,
                json.dumps(reply) if kind == "reconcile" else None,
                original_run if kind == "ensure" else unrelated_run or identity.run_id,
            )


@pytest.mark.parametrize("case", [
    "expired", "advanced", "future", "dispatch_lock", "bridge", "wrong_scope", "read",
    "blocked_deploy", "replayed_release", "retried_ensure", "reconciled", "unmatched",
    "future_scheduler", "capacity_reconciled", "unrelated_reconcile",
    "catalog_armed", "catalog_unarmed", "catalog_wrong_segment",
])
async def test_private_released_expiry_settles_once_without_enabling_dispatch(
    pool, monkeypatch, case,
):
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_RETENTION_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_SIGNING_SECRET", _SECRET)
    reset_settings_cache()
    success = case in {
        "expired", "advanced", "replayed_release", "reconciled", "capacity_reconciled", "catalog_armed",
    }
    identity, old, root, wire = await _runtime_intent(
        pool, is_due=case != "future", changed_bridge=case == "bridge",
        advanced=case == "advanced", scheduler_due=case != "future_scheduler",
        catalog=case if case.startswith("catalog_") else None,
    )
    history_executor = {
        "reconciled": "original", "unmatched": "original",
        "capacity_reconciled": "requesting", "unrelated_reconcile": "unrelated",
    }
    if case in history_executor:
        await _historical_reconciliation(
            pool, identity, confirmed=case != "unmatched", executor=history_executor[case],
        )
    if case in {"replayed_release", "retried_ensure"}:
        kind = "release" if case == "replayed_release" else "ensure"
        async with pool.acquire() as conn:
            assert await conn.execute(
                "UPDATE project_cell_operations SET attempt_count=2 "
                "WHERE generation_run_id=$1 AND kind=$2 AND fencing_epoch>=12",
                identity.run_id, kind,
            ) == ("UPDATE 2" if kind == "release" else "UPDATE 1")
    if case == "blocked_deploy":
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO deployment_drains(scope,release_sha) VALUES('generation',$1)",
                "a" * 40,
            )
    app = FastAPI()
    app.include_router(generation_responses.router)
    path = f"/_internal/generation-responses/{identity.attempt_id}/expire"
    headers = {
        auth.HEADER: (
            auth.mint_generation_response_token(
                identity, purpose="generation_response.read", secret=_SECRET,
            ) if case == "read" else _token(identity, revision=106 if case == "wrong_scope" else 107)
        ),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
    ) as client:
        if case == "dispatch_lock":
            async with pool.acquire() as conn, conn.transaction():
                row = await attempts._owned(conn, identity.attempt_id)
                reply = await client.post(path, headers=headers)
            assert reply.status_code == 409
            await attempts.mark_attempt_dispatching(
                identity.attempt_id, dispatch_token=row["dispatch_token"], route="/v1/messages",
                model="synthetic", run_id=identity.run_id, project_id=identity.project_id,
                user_id=identity.user_id,
            )
        else:
            reply = await client.post(path, headers=headers)
        if success:
            assert reply.status_code == 200
            again = await client.post(path, headers=headers)
            assert again.status_code == 200 and reply.json() == again.json()
            assert reply.json()["identity"] == identity.to_wire()
            assert reply.json()["state"] == "expired_before_dispatch"
            assert (await attempts.reserve_generation_attempt(identity)).action == "pending"
        elif case == "read":
            assert reply.status_code == 403
        else:
            assert reply.status_code == 409

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT phase,terminal_reason FROM generation_provider_attempts WHERE attempt_id=$1",
            identity.attempt_id,
        )
        prior = await conn.fetchval(
            "SELECT terminal_reason FROM generation_provider_attempts WHERE attempt_id=$1",
            old.attempt_id,
        )
        assert prior == "expired_before_dispatch"
        if success:
            assert tuple(row.values()) == ("closed", "expired_before_dispatch")
            effects = await conn.fetch(
                "SELECT state,receipt_ref FROM generation_recovery_effects WHERE effect_id=ANY($1)",
                [root, wire],
            )
            assert len(effects) == 2
            assert all(
                effect["state"] == "confirmed"
                and json.loads(effect["receipt_ref"]) == reply.json()["receipt"]
                for effect in effects
            )
        else:
            assert tuple(row.values()) == ("dispatching" if case == "dispatch_lock" else "reserved", None)
        assert await conn.fetchval(
            "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id,
        ) == int(case == "dispatch_lock")
    reset_settings_cache()



@pytest.mark.parametrize("drain,is_due", [
    ("exact", True), ("exact", False), ("foreign", True), ("absent", True),
])
async def test_runtime_expiry_release_cli_preserves_exclusive_operator_scope(
    pool, monkeypatch, drain, is_due,
):
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    monkeypatch.setattr(cli.db, "init_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(cli.db, "close_pool", AsyncMock())
    identity, old, root, wire = await _runtime_intent(pool, is_due=is_due, scheduler_due=False)
    succeeds = drain == "exact" and is_due
    target = "a" * 40
    if drain != "absent":
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO deployment_drains(scope,release_sha) VALUES('generation',$1)",
                target if drain == "exact" else "f" * 40,
            )
    payload = {
        "version": 1, "action": "close_released_before_dispatch", "release_sha": target,
        "reader_release_sha": "b" * 40, "state_revision": 107,
        "source_sha256": "c" * 64, "context_sha256": _CONTEXT_DIGEST,
        "identity": identity.to_wire(),
    }
    scope = cli._scope(json.dumps(payload).encode(), target)
    old_scope = attempts.ExpiredReservationScope(identity, target, 107, "c" * 64)
    with pytest.raises(attempts.AttemptConflict):
        await attempts.close_expired_before_dispatch(old_scope)
    if succeeds:
        digest = await cli._settle(scope)
        assert await cli._settle(scope) == digest
    else:
        with pytest.raises(attempts.AttemptConflict):
            await cli._settle(scope)

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1",
            identity.attempt_id,
        )
        assert status == ("closed" if succeeds else "reserved")
        assert await conn.fetchval(
            "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT terminal_reason FROM generation_provider_attempts WHERE attempt_id=$1",
            old.attempt_id,
        ) == "expired_before_dispatch"
        if succeeds:
            effects = await conn.fetch(
                "SELECT state,receipt_ref FROM generation_recovery_effects WHERE effect_id=ANY($1)",
                [root, wire],
            )
            assert len(effects) == 2
            assert all(
                effect["state"] == "confirmed" and _digest(json.loads(effect["receipt_ref"])) == digest
                for effect in effects
            )
