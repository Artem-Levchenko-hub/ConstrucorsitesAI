"""The global journal switch cannot enroll ordinary generations into recovery."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text

from tests.test_generation_recovery_execution import _provider_context
from yleum_api.core import config
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.services import llm_client
from yleum_api.services.generation import provider_attempts, recovery_execution
from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity,
    request_digest,
)
from yleum_api.services.generation.recovery_contract import RecoveryClock, RecoveryState
from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope
from yleum_api.services.generation.response_auth import HEADER

ROUTE = "/v1/chat/completions"


async def context(db_session, test_engine, monkeypatch, *, mode="off"):
    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    settings = config.get_settings().model_copy(update={
        "generation_provider_attempts_enabled": True,
        "generation_response_retention_enabled": True,
        "generation_recovery_execution_enabled": False,
        "generation_recovery_mode": mode,
        "generation_recovery_canary_project_ids": str(uuid4()),
        "generation_response_signing_secret": SecretStr("synthetic-enrollment-secret-" * 2),
    })
    monkeypatch.setattr(provider_attempts, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    return run, cursor, body


def legacy_state(run, variant):
    state = dict(run.agent_state)
    raw = state.pop("recovery_v1")
    if variant == "v1":
        state["recovery_v1"] = {
            key: value for key, value in raw.items() if key in RecoveryState.model_fields
        }
        state["recovery_v1"]["version"] = 1
    elif variant == "browser_marker":
        state["browser_canary"] = True
    return state


@asynccontextmanager
async def owning_session(engine, run):
    async with engine.connect() as owner:
        await owner.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {
            "key": int.from_bytes(run.id.bytes[-4:], "big", signed=True),
        })
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            yield
        finally:
            recovery_execution._SESSION_OWNER.reset(token)


@pytest.mark.parametrize("variant", ["absent", "v1", "browser_marker", "allowlisted"])
@pytest.mark.parametrize("mode", ["off", "canary"])
async def test_global_switch_leaves_legacy_wire_and_journal_untouched(
    db_session, test_engine, monkeypatch, variant, mode,
):
    import json

    run, _, body = await context(db_session, test_engine, monkeypatch, mode=mode)
    if variant == "allowlisted":
        settings = config.get_settings().model_copy(update={
            "generation_recovery_canary_project_ids": str(run.project_id),
            "generation_recovery_execution_enabled": True,
        })
        monkeypatch.setattr(provider_attempts, "get_settings", lambda: settings)
        monkeypatch.setattr(config, "get_settings", lambda: settings)
    run.agent_state = legacy_state(run, variant)
    await db_session.commit()
    sent = []

    def reply(request):
        sent.append((json.loads(request.content), request.headers))
        return httpx.Response(200, json={"choices": [], "usage": {}})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(
        transport=httpx.MockTransport(reply), **kw,
    ))
    await llm_client._chat_post(body, "https://synthetic.invalid", 30)
    assert len(sent) == 1
    assert "generation_provider_identity" not in sent[0][0]["metadata"]
    assert HEADER not in sent[0][1]
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 0


@pytest.mark.parametrize("fault", ["null", "unknown", "malformed", "foreign", "prompt"])
async def test_invalid_saved_recovery_cannot_fall_back_to_legacy(
    db_session, test_engine, monkeypatch, fault,
):
    run, cursor, body = await context(db_session, test_engine, monkeypatch)
    raw = dict(run.agent_state["recovery_v1"])
    if fault == "null":
        raw = None
    elif fault == "unknown":
        raw["version"] = 999
    elif fault == "malformed":
        raw.pop("execution")
    else:
        raw["identity"] = {**raw["identity"],
            "project_id" if fault == "foreign" else "prompt_hash": (
                str(uuid4()) if fault == "foreign" else "0" * 64
            )}
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    async with owning_session(test_engine, run):
        with provider_effect_scope(cursor), pytest.raises(RuntimeError, match="UNCONFIRMED"):
            await provider_attempts.prepare_generation_attempt(body, ROUTE)
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 0


@pytest.mark.parametrize("version", [2, 3])
async def test_saved_executable_carryover_enrolls_and_reuses_under_mode_off(
    db_session, test_engine, monkeypatch, version,
):
    run, cursor, body = await context(db_session, test_engine, monkeypatch)
    if version == 3:
        raw = dict(run.agent_state["recovery_v1"])
        raw.update(version=3, clock=RecoveryClock(
            window_ms=60000, segment_deadline=datetime.now(UTC) + timedelta(seconds=60),
            interval_id=uuid4(),
        ).model_dump(mode="json"))
        run.agent_state = {**run.agent_state, "recovery_v1": raw}
        await db_session.commit()
    async with owning_session(test_engine, run):
        with provider_effect_scope(cursor):
            identity = await provider_attempts.prepare_generation_attempt(body, ROUTE)
            assert identity is not None
            assert await provider_attempts.prepare_generation_attempt(
                body, ROUTE, existing=identity,
            ) == identity
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 1


async def saved_task7(db_session, test_engine, monkeypatch):
    run, _, body = await context(db_session, test_engine, monkeypatch)
    run.agent_state = legacy_state(run, "absent")
    identity = GenerationProviderIdentity(
        run.id, run.project_id, run.user_id, uuid4(), run.prompt_hash, request_digest(ROUTE, body),
    )
    row = GenerationProviderAttempt(
        attempt_id=identity.attempt_id, run_id=run.id, project_id=run.project_id,
        user_id=run.user_id, prompt_hash=run.prompt_hash,
        request_sha256=identity.request_sha256, dispatch_token=uuid4(),
    )
    db_session.add(row)
    await db_session.commit()
    return run, body, identity, row


@pytest.mark.parametrize("fault", [
    None, "missing", "owner", "prompt", "digest", "claimed_owner", "claimed_run", "claimed_project",
])
async def test_task7_continuation_requires_exact_committed_identity(
    db_session, test_engine, monkeypatch, fault,
):
    _, body, identity, row = await saved_task7(db_session, test_engine, monkeypatch)
    if fault == "missing":
        await db_session.delete(row)
    elif fault and fault.startswith("claimed_"):
        identity = replace(identity, **{
            {"claimed_owner": "user_id", "claimed_run": "run_id",
             "claimed_project": "project_id"}[fault]: uuid4(),
        })
    elif fault:
        setattr(row, {"owner": "user_id", "prompt": "prompt_hash", "digest": "request_sha256"}[
            fault
        ], uuid4() if fault == "owner" else "0" * 64)
    await db_session.commit()
    if fault:
        with pytest.raises(RuntimeError, match="UNCONFIRMED"):
            await provider_attempts.prepare_generation_attempt(body, ROUTE, existing=identity)
    else:
        assert await provider_attempts.prepare_generation_attempt(
            body, ROUTE, existing=identity,
        ) == identity


@pytest.mark.parametrize("closed", [False, True, "foreign_identity"])
async def test_task7_parent_requires_existing_affirmative_receipt(
    db_session, test_engine, monkeypatch, closed,
):
    from yleum_api.models.provider_call import ProviderCall

    run, body, identity, row = await saved_task7(db_session, test_engine, monkeypatch)
    if closed:
        call = ProviderCall(
            id=uuid4(), provider_scope="llmgw", route=ROUTE, requested_model="synthetic",
            expense_owner="user", user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, stage="generation", free=False, status="failed",
            receipt_hash="a" * 64, finished_at=datetime.now(UTC),
        )
        db_session.add(call)
        await db_session.flush()
        row.provider_call_id = call.id
        row.phase = "closed"
        row.terminal_reason = "preconnect"
        await db_session.commit()
        if closed == "foreign_identity":
            with pytest.raises(RuntimeError, match="UNCONFIRMED"):
                await provider_attempts.prepare_generation_attempt(
                    body, ROUTE, parent=replace(identity, user_id=uuid4()),
                )
            return
        child = await provider_attempts.prepare_generation_attempt(body, ROUTE, parent=identity)
        assert child is not None and child.attempt_id != identity.attempt_id
        saved = await db_session.get(GenerationProviderAttempt, child.attempt_id)
        assert saved.parent_attempt_id == identity.attempt_id
    else:
        with pytest.raises(RuntimeError, match="UNCONFIRMED"):
            await provider_attempts.prepare_generation_attempt(body, ROUTE, parent=identity)
