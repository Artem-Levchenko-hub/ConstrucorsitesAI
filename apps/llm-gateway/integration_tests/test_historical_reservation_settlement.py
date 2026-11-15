"""Exercise the attempt-row fence against the same local PostgreSQL used by Gateway."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

import pytest
import test_billing_settlement_postgres as fixture_source
from test_billing_settlement_postgres import _owner

from yleum_gateway.services import generation_attempts as attempts
from yleum_gateway.services.generation_identity import GenerationProviderIdentity

database_url = fixture_source.database_url
pool = fixture_source.pool


async def _parked_intent(pool, *, later_commands=False):
    user, project, _ = await _owner(pool)
    run, workspace, release, claim = uuid4(), uuid4(), uuid4(), uuid4()
    root = uuid5(run, "recovery-provider-v1:86")
    wire = uuid5(root, "wire-v1:0:0")
    attempt = uuid5(root, "provider-attempt-v1")
    identity = GenerationProviderIdentity(run, project, user, attempt, "a" * 64, "b" * 64)
    source = "c" * 64
    release_sha = "d" * 40
    context_ref = {"object_key": "synthetic-private-context", "sha256": "f" * 64}
    binding = {
        "workspace_id": str(workspace), "fencing_epoch": 9,
        "source_sha256": source, "schema_sha256": "e" * 64, "environment_sha256": "f" * 64,
    }
    historical = {**binding, "fencing_epoch": 7}
    if later_commands:
        historical["environment_sha256"] = "7" * 64
    plan = {
        "version": 1, "clock_basis": "utc", "kind": "native",
        "deadline": (datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
        "outer_attempt": 0, "outer_allowance": 1, "wire_allowance": 3,
    }
    state = {
        "version": 3, "state_revision": 105, "attempt_seq": 2,
        "phase": "needs_input", "next_check_at": None, "unresolved_operation_ids": [],
        "identity": {
            "run_id": str(run), "project_id": str(project), "user_id": str(user),
            "prompt_hash": identity.prompt_hash, "accepted_snapshot_id": None,
        },
        "binding": binding, "context_ref": context_ref, "checkpoint": context_ref,
        "execution": {
            "stage": "finalization", "substep": "response_wait", "physical_state": "released",
            "provider_effect_id": str(root), "pending_effect_id": str(root), "logical_step_seq": 86,
            "control_workspace_id": str(workspace), "control_fencing_epoch": 10,
            "release_operation_id": str(release), "execution_context_ref": context_ref,
        },
    }
    wire_plan = {
        **plan, "root_effect_id": str(root), "provider_effect_id": str(root),
        "attempt_id": str(attempt), "parent_attempt_id": None,
        "request_sha256": identity.request_sha256, "wire_attempt": 0,
    }
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO deployment_drains(scope,release_sha) VALUES('generation',$1) "
            "ON CONFLICT(scope) DO UPDATE SET release_sha=excluded.release_sha", release_sha,
        )
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,idempotency_key,prompt_hash,status,"
            "execution_backend,response_mode,agent_state) VALUES($1,$2,$3,$4,$5,'running',"
            "'worker','build',$6::jsonb)",
            run, project, user, str(run), identity.prompt_hash, json.dumps({"recovery_v1": state}),
        )
        await conn.execute(
            "INSERT INTO project_cell_workspaces(id,project_id,owner_id,provider,state,fencing_epoch)"
            " VALUES($1,$2,$3,'docker_owner_canary','ready',10)",
            workspace, project, user,
        )
        await conn.execute(
            "INSERT INTO project_cell_operations(id,workspace_id,generation_run_id,idempotency_key,"
            "request_digest,kind,status,fencing_epoch,finished_at,result_payload) "
            "VALUES($1,$2,$3,$4,$5,'release','completed',10,now(),$6::jsonb)",
            release, workspace, run, f"generation:{run}:release:qa:cycle:2", "a" * 64,
            json.dumps({
                "workspace_id": str(workspace), "fencing_epoch": 10, "state": "resources_ready",
                "has_workspace": True, "has_agent_home": True, "has_postgres": True, "has_redis": True,
            }),
        )
        await conn.execute(
            "INSERT INTO generation_provider_attempts "
            "(attempt_id,run_id,project_id,user_id,prompt_hash,request_sha256,dispatch_token) "
            "VALUES($1,$2,$3,$4,$5,$6,$7)", attempt, run, project, user,
            identity.prompt_hash, identity.request_sha256, uuid4(),
        )
        sql = (
            "INSERT INTO generation_recovery_effects(effect_id,run_id,project_id,user_id,"
            "logical_step_seq,effect_ordinal,kind,input_digest,state,attempt_seq,claim_token,"
            "provider_attempt_id,parent_effect_id,pre_binding) "
            "VALUES($1,$2,$3,$4,86,$5,$6,$7,$8,1,$9,$10,$11,$12::jsonb)"
        )
        await conn.execute(sql, root, run, project, user, 0, "provider", identity.request_sha256,
                           "prepared", claim, attempt, None,
                           json.dumps({"source": historical, "dispatch_v1": plan}))
        await conn.execute(sql, wire, run, project, user, 1, "progress", identity.request_sha256,
                           "inflight", claim, None, root,
                           json.dumps({"dispatch_v1": wire_plan}))
        if later_commands:
            await conn.execute(
                "INSERT INTO project_cell_operations(id,workspace_id,generation_run_id,"
                "idempotency_key,request_digest,kind,status,fencing_epoch,finished_at,result_payload) "
                "VALUES($1,$2,$3,$4,$5,'release','completed',8,now()-interval '20 seconds',$6::jsonb)",
                uuid4(), workspace, run, f"generation:{run}:release:qa:cycle:1", "a" * 64,
                json.dumps({
                    "workspace_id": str(workspace), "fencing_epoch": 8, "state": "resources_ready",
                    "has_workspace": True, "has_agent_home": True, "has_postgres": True,
                    "has_redis": True,
                }),
            )
            await _insert_terminal_commands(conn, identity, binding, context_ref, uuid4())
    return identity, release_sha, source, root, wire


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


async def _insert_terminal_commands(conn, identity, binding, context_ref, claim):
    from uuid import UUID

    workspace = UUID(binding["workspace_id"])
    run = identity.run_id
    proof = uuid4()
    proof_parts = {
        "fencing_epoch": 9, "workspace_revision": binding["source_sha256"],
        "dependency_digest": "1" * 64, "schema_data_digest": binding["schema_sha256"],
        "cell_manifest_digest": "2" * 64, "base_image_digest": binding["environment_sha256"],
        "toolchain_digest": binding["environment_sha256"], "resource_profile_version": "qa",
        "build_config_digest": "3" * 64,
    }
    proof_key = _digest(proof_parts)
    await conn.execute(
        "INSERT INTO project_cell_proofs(id,workspace_id,generation_run_id,fencing_epoch,"
        "proof_key,workspace_revision,dependency_digest,schema_data_digest,cell_manifest_digest,"
        "base_image_digest,toolchain_digest,resource_profile_version,build_config_digest) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
        proof, workspace, run, 9, proof_key, *list(proof_parts.values())[1:],
    )
    for ordinal, role, phase, version in (
        (2, "bootstrap", "prepare", "max-platform-bootstrap-v2"),
        (3, "fast_check", "fast_check", "v1"),
    ):
        op = uuid5(run, f"command:{workspace}:9:{proof_key}:{role}:{version}")
        call = f"role:{op}"
        effect = uuid5(uuid5(run, f"recovery-tool-v1:86:{call}"), "physical:0")
        req, rev, vol_id = "5" * 64, binding["source_sha256"], "6" * 64
        pre = {
            "version": 1, "kind": "command_v1",
            "workspace_id": str(workspace), "fencing_epoch": 9, "workspace_revision": rev,
            "request_sha256": req, "context_ref": context_ref, "context_binding": binding,
            "activity": {"operation_id": str(op), "proof_key": proof_key, "phase": phase},
        }
        receipt = {
            "version": 1, "operation_id": str(op), "workspace_id": str(workspace),
            "project_id": str(identity.project_id), "owner_id": str(identity.user_id),
            "generation_run_id": str(run), "dispatch_fencing_epoch": 9,
            "physical_volume": "synthetic-volume", "physical_volume_identity": vol_id,
            "request_sha256": req, "before_revision": rev, "after_revision": rev,
            "response_sha256": "8" * 64,
        }
        post = {"workspace_revision": rev, "physical_volume_identity": vol_id}
        await conn.execute(
            "INSERT INTO project_cell_activity_leases(operation_id,workspace_id,generation_run_id,"
            "kind,state,fencing_epoch,proof_key,phase,started_at,deadline_at,heartbeat_at,finished_at)"
            " VALUES($1,$2,$3,'command','completed',9,$4,$5,now()-interval '10 seconds',"
            "now()+interval '1 minute',now()-interval '5 seconds',now()-interval '1 second')",
            op, workspace, run, proof_key, phase,
        )
        proof_common = {k: v for k, v in proof_parts.items() if k not in {
            "workspace_revision", "build_config_digest",
        }}
        proof_meta = (
            {"contract": version} if role == "bootstrap" else {
                "workspace_revision": rev, "build_config_digest": proof_parts["build_config_digest"],
            }
        )
        dimension_key = _digest({"dimension": role, **proof_common, **proof_meta})
        await conn.execute(
            "INSERT INTO project_cell_proof_results(id,proof_id,workspace_id,dimension,"
            "dimension_key,outcome,operation_id,detail_digest,redacted_detail)"
            " VALUES($1,$2,$3,$4,$5,'green',$6,$7,'synthetic terminal result')",
            uuid4(), proof, workspace, role, dimension_key, op, "8" * 64,
        )
        await conn.execute(
            "INSERT INTO generation_recovery_effects(effect_id,run_id,project_id,user_id,"
            "logical_step_seq,effect_ordinal,kind,input_digest,state,attempt_seq,claim_token,"
            "operation_id,tool_call_id,pre_binding,post_binding,receipt_ref,receipt_sha256)"
            " VALUES($1,$2,$3,$4,86,$5,'tool',$6,'confirmed',2,$7,$8,$9,$10::jsonb,$11::jsonb,"
            "$12::jsonb,$13)",
            effect, run, identity.project_id, identity.user_id, ordinal, req, claim, op, call,
            json.dumps(pre), json.dumps(post), json.dumps(receipt), _digest(receipt),
        )


@pytest.mark.parametrize(
    "damage",
    [None, "proof_red", "bad_receipt", "provider_child", "schema", "old_release", "wire_type"],
)
async def test_seals_expired_native_wire_with_owned_later_proofs(pool, monkeypatch, damage):
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    identity, release_sha, source, root, wire = await _parked_intent(pool, later_commands=True)
    scope = attempts.ExpiredReservationScope(identity, release_sha, 105, source)
    async with pool.acquire() as conn:
        if damage == "proof_red":
            await conn.execute(
                "UPDATE project_cell_proof_results SET outcome='red' WHERE operation_id=("
                "SELECT operation_id FROM generation_recovery_effects "
                "WHERE run_id=$1 AND effect_ordinal=3)", identity.run_id,
            )
        elif damage == "bad_receipt":
            await conn.execute(
                "UPDATE generation_recovery_effects SET receipt_sha256=$2 "
                "WHERE run_id=$1 AND effect_ordinal=3", identity.run_id, "0" * 64,
            )
        elif damage == "provider_child":
            await conn.execute(
                "UPDATE generation_recovery_effects SET parent_effect_id=$2 "
                "WHERE run_id=$1 AND effect_ordinal=3", identity.run_id, root,
            )
        elif damage == "wire_type":
            await conn.execute(
                "UPDATE generation_recovery_effects SET pre_binding=jsonb_set(pre_binding,"
                "'{dispatch_v1,wire_attempt}','false'::jsonb) WHERE effect_id=$1", wire,
            )
        elif damage == "old_release":
            await conn.execute(
                "UPDATE project_cell_operations SET result_payload=jsonb_set(result_payload,"
                "'{state}',to_jsonb('unowned'::text)) "
                "WHERE generation_run_id=$1 AND fencing_epoch=8", identity.run_id,
            )
        elif damage == "schema":
            await conn.execute(
                "UPDATE generation_recovery_effects SET pre_binding=jsonb_set(pre_binding,"
                "'{source,schema_sha256}',to_jsonb($2::text)) WHERE effect_id=$1", root, "9" * 64,
            )
        roles = await conn.fetch(
            "SELECT * FROM generation_recovery_effects WHERE run_id=$1 AND effect_ordinal>=2 "
            "ORDER BY effect_ordinal", identity.run_id,
        )

    if damage is None:
        receipt = await attempts.close_expired_before_dispatch(scope)
        assert receipt == await attempts.close_expired_before_dispatch(scope)
        assert receipt["wire_effect_id"] == str(wire)
    else:
        with pytest.raises(attempts.AttemptConflict):
            await attempts.close_expired_before_dispatch(scope)

    async with pool.acquire() as conn:
        assert roles == await conn.fetch(
            "SELECT * FROM generation_recovery_effects WHERE run_id=$1 AND effect_ordinal>=2 "
            "ORDER BY effect_ordinal", identity.run_id,
        )
        assert await conn.fetchval(
            "SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1", identity.attempt_id,
        ) == ("closed" if damage is None else "reserved")


@pytest.mark.parametrize(
    "damage",
    [None, "scope", "source", "clock", "outward", "dispatched", "receiver", "worker", "gateway_lock"],
)
async def test_close_exact_expired_reservation_fences_provider(pool, monkeypatch, damage):
    from yleum_gateway.services import generation_response_ledger as receiver

    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    monkeypatch.setattr(receiver, "get_pool", lambda: pool)
    identity, release_sha, source, root, wire = await _parked_intent(pool)
    admitted = await attempts.reserve_generation_attempt(identity)
    assert admitted.action == "dispatch"

    async def mark():
        return await attempts.mark_attempt_dispatching(
            identity.attempt_id, dispatch_token=admitted.dispatch_token,
            route="/v1/messages", model="synthetic", run_id=identity.run_id,
            project_id=identity.project_id, user_id=identity.user_id,
        )

    async def receive():
        return await receiver.admit_response(identity, receiver.CapacitySnapshot(
            bucket="synthetic-expiry-" + identity.attempt_id.hex,
            quota_bytes=2 ** 27, free_bytes=2 ** 27, epoch=uuid4(), nonce=uuid4(),
            expires_at=int(datetime.now(UTC).timestamp()) + 60,
        ))

    scope = attempts.ExpiredReservationScope(identity, release_sha, 105, source)
    if damage == "scope":
        scope = replace(scope, release_sha="1" * 40)
    elif damage == "source":
        scope = replace(scope, source_sha256="1" * 64)
    elif damage == "clock":
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE generation_recovery_effects SET pre_binding=jsonb_set(pre_binding,"
                "'{dispatch_v1,deadline}',to_jsonb($1::text)) WHERE effect_id IN($2,$3)",
                (datetime.now(UTC) + timedelta(minutes=5)).isoformat(), root, wire,
            )
    elif damage == "outward":
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE generation_recovery_effects SET post_binding='{\"outward_started\":true}'"
                " WHERE effect_id=$1", wire,
            )
    elif damage == "dispatched":
        await mark()
    elif damage == "receiver":
        await receive()
    elif damage == "worker":
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(1195724366,$1)",
                int.from_bytes(identity.run_id.bytes[-4:], "big", signed=True),
            )
            with pytest.raises(attempts.AttemptConflict):
                await attempts.close_expired_before_dispatch(scope)
        return
    elif damage == "gateway_lock":
        async with pool.acquire() as conn, conn.transaction():
            assert await attempts._owned(conn, identity.attempt_id) is not None
            with pytest.raises(attempts.AttemptConflict):
                await attempts.close_expired_before_dispatch(scope)
        # The transaction holding the gateway lock keeps its original authority.
        await mark()
        return

    if damage is None:
        receipt = await attempts.close_expired_before_dispatch(scope)
        assert receipt == await attempts.close_expired_before_dispatch(scope)
        with pytest.raises(attempts.AttemptConflict):
            await mark()
        with pytest.raises(receiver.ResponseIndeterminate):
            await receive()
        assert (await attempts.reserve_generation_attempt(identity)).action == "pending"
    else:
        with pytest.raises(attempts.AttemptConflict):
            await attempts.close_expired_before_dispatch(scope)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT phase,terminal_reason,provider_call_id FROM generation_provider_attempts "
            "WHERE attempt_id=$1", identity.attempt_id,
        )
        assert row["phase"] == (
            "closed" if damage is None else "dispatching" if damage == "dispatched" else "reserved"
        )
        assert row["terminal_reason"] == ("expired_before_dispatch" if damage is None else None)
        assert (row["provider_call_id"] is not None) is (damage == "dispatched")
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM generation_responses WHERE attempt_id=$1)",
            identity.attempt_id,
        ) is (damage == "receiver")


async def test_expired_receipt_requires_gateway_effects_to_stop_api_accounting(
    pool, database_url, monkeypatch,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_quiescence import _provider_stopped

    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    owner, release_sha, source, _, wire = await _parked_intent(pool)
    scope = attempts.ExpiredReservationScope(owner, release_sha, 105, source)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))

    async def stopped():
        async with async_sessionmaker(engine)() as session:
            run = await session.get(GenerationRun, owner.run_id)
            return await _provider_stopped(session, run)

    try:
        assert await stopped() is False
        await attempts.close_expired_before_dispatch(scope)
        assert await stopped() is True
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE generation_recovery_effects SET receipt_ref='{}' WHERE effect_id=$1", wire,
            )
        assert await stopped() is False
        with pytest.raises(attempts.AttemptConflict):
            await attempts.close_expired_before_dispatch(scope)
    finally:
        await engine.dispose()


async def test_cli_seals_under_actual_0107_schema_and_allows_writer_rows(pool, monkeypatch):
    from unittest.mock import AsyncMock

    import asyncpg

    from yleum_gateway.ops import expired_provider_reservation as cli

    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    monkeypatch.setattr(cli.db, "init_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(cli.db, "close_pool", AsyncMock())
    identity, release_sha, source, _, _ = await _parked_intent(pool, later_commands=True)
    scope = attempts.ExpiredReservationScope(identity, release_sha, 105, source)
    writer = attempts.close_expired_before_dispatch

    async def settled(owned):
        async with pool.acquire() as conn:
            for query in (
                "LOCK TABLE public.alembic_version IN ROW EXCLUSIVE MODE NOWAIT",
                "LOCK TABLE public.generation_provider_attempts IN ACCESS EXCLUSIVE MODE NOWAIT",
            ):
                with pytest.raises(asyncpg.LockNotAvailableError):
                    async with conn.transaction():
                        await conn.execute(query)
        return await writer(owned)

    monkeypatch.setattr(cli, "close_expired_before_dispatch", settled)
    sha = await cli._settle(scope)
    receipt = await attempts.close_expired_before_dispatch(scope)
    assert sha == _digest(receipt)
