"""Operator-only truncation is an owning attempt, never a model/browser option."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


def test_default_does_not_change_output_budget():
    from yleum_api.services.generation.recovery_diagnostic import diagnostic_output_budget

    for original in (None, 1024, 8192):
        assert (
            diagnostic_output_budget(
                run_id=uuid4(),
                attempt_id=uuid4(),
                requested_max=original,
            )
            == original
        )


def test_signed_exact_first_attempt_and_later_omission():
    from yleum_api.services.generation import recovery_diagnostic as d

    now = datetime.now(UTC)
    ids = [uuid4() for _ in range(4)]
    token = d.mint_intent(
        run_id=ids[0],
        project_id=ids[1],
        user_id=ids[2],
        attempt_id=ids[3],
        prompt_hash="a" * 64,
        now=now,
        secret="s" * 32,
    )
    intent = d.verify_intent(token, now=now, secret="s" * 32)
    with d._budget_scope(intent):
        assert (
            d.diagnostic_output_budget(run_id=ids[0], attempt_id=ids[3], requested_max=None) == 16
        )
        assert (
            d.diagnostic_output_budget(run_id=ids[0], attempt_id=uuid4(), requested_max=None)
            is None
        )
        with pytest.raises(ValueError):
            d.diagnostic_output_budget(run_id=uuid4(), attempt_id=ids[3], requested_max=None)


@pytest.mark.parametrize("fault", ["signature", "purpose", "expired", "future", "extra"])
def test_invalid_signed_intent_never_authorizes(fault):
    from yleum_api.services.generation import recovery_diagnostic as d
    from yleum_api.services.generation.response_auth import _signed

    now = datetime.now(UTC)
    token = d.mint_intent(
        run_id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        attempt_id=uuid4(),
        prompt_hash="a" * 64,
        now=now,
        secret="s" * 32,
    )
    if fault in {"purpose", "extra"}:
        claims = d.verify_intent(token, now=now, secret="s" * 32)
        claims["purpose" if fault == "purpose" else "extra"] = "generation_response.record"
        token = _signed(claims, "s" * 32, d.DOMAIN)
    elif fault == "signature":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif fault == "expired":
        now += timedelta(seconds=61)
    else:
        now -= timedelta(seconds=2)
    with pytest.raises(ValueError):
        d.verify_intent(token, now=now, secret="s" * 32)


async def _armed(db_session, test_engine, monkeypatch):
    from uuid import uuid5

    from pydantic import SecretStr

    from tests.test_generation_recovery_execution import _provider_context
    from yleum_api.core import config
    from yleum_api.services.generation import recovery_diagnostic as d

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    from yleum_api.services.generation_runs import prompt_hash

    run.prompt_hash = prompt_hash("Собери приложение")
    raw = dict(run.agent_state["recovery_v1"])
    raw["identity"] = {**raw["identity"], "prompt_hash": run.prompt_hash}
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    settings = config.get_settings().model_copy(
        update={
            "generation_recovery_mode": "canary",
            "generation_recovery_execution_enabled": True,
            "generation_provider_attempts_enabled": True,
            "generation_response_retention_enabled": True,
            "generation_recovery_canary_project_ids": str(run.project_id),
            "generation_response_signing_secret": SecretStr("synthetic-only-secret-" * 3),
        }
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(d, "get_settings", lambda: settings)
    attempt = uuid5(cursor.root_effect_id, "provider-attempt-v1")
    await d.arm_diagnostic_output_budget(
        db_session,
        run_id=run.id,
        project_id=run.project_id,
        user_id=run.user_id,
        attempt_id=attempt,
    )
    await db_session.commit()
    return run, cursor, body, settings


async def test_real_dispatch_consumes_once_then_next_sonnet_omits(
    db_session,
    test_engine,
    monkeypatch,
):
    import json

    import httpx
    from sqlalchemy import func, select, text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.services import llm_client
    from yleum_api.services.generation import recovery_execution
    from yleum_api.services.generation.recovery_reconciliation import (
        ProviderEffectCursor,
        provider_effect_scope,
    )

    run, cursor, body, _settings = await _armed(db_session, test_engine, monkeypatch)
    body["model"] = "anthropic/claude-sonnet-4.5"
    sent = []

    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [], "usage": {}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw)
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor):
                await llm_client._chat_post(dict(body), "https://synthetic.invalid", 30)
            assert len(sent) == 1 and sent[0]["max_tokens"] == 16
            replay = ProviderEffectCursor(cursor.claim, 1, 0)
            with provider_effect_scope(replay), pytest.raises(Exception):
                await llm_client._chat_post(dict(body), "https://synthetic.invalid", 30)
            assert len(sent) == 1
            await db_session.refresh(run)
            next_cursor = ProviderEffectCursor(cursor.claim, 2, 1)
            raw = dict(run.agent_state["recovery_v1"])
            raw["state_revision"] = 2
            raw["execution"] = {
                **raw["execution"],
                "logical_step_seq": 1,
                "execution_context_ref": {"object_key": "synthetic/context", "sha256": "d" * 64},
                "pending_effect_id": str(next_cursor.root_effect_id),
            }
            run.agent_state = {**run.agent_state, "recovery_v1": raw}
            await db_session.commit()
            with provider_effect_scope(next_cursor):
                await llm_client._chat_post(dict(body), "https://synthetic.invalid", 30)
            assert len(sent) == 2 and "max_tokens" not in sent[1]
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 2


@pytest.mark.parametrize(
    "fault", ["foreign", "cancel", "later", "corrupt", "expired", "fence", "copied", "off"]
)
async def test_owned_attempt_rejects_changed_authority(db_session, test_engine, monkeypatch, fault):
    from sqlalchemy import func, select, text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.project import Project
    from yleum_api.services.generation import (
        provider_attempts,
        recovery_diagnostic,
        recovery_execution,
    )
    from yleum_api.services.generation.recovery_reconciliation import (
        ProviderEffectCursor,
        provider_effect_scope,
    )

    run, cursor, body, _settings = await _armed(db_session, test_engine, monkeypatch)
    if fault == "foreign":
        from yleum_api.models.user import User

        foreign = User(email=f"synthetic-{uuid4()}@example.invalid")
        db_session.add(foreign)
        await db_session.flush()
        project = await db_session.get(Project, run.project_id)
        project.owner_id = foreign.id
    elif fault == "cancel":
        run.status = "cancel_requested"
    elif fault == "later":
        cursor = ProviderEffectCursor(cursor.claim, 1, 1)
    elif fault == "corrupt":
        run.agent_state = {**run.agent_state, recovery_diagnostic.KEY: None}
    elif fault in {"expired", "copied"}:
        old = datetime.now(UTC) - timedelta(minutes=2) if fault == "expired" else datetime.now(UTC)
        from uuid import uuid5

        token = recovery_diagnostic.mint_intent(
            run_id=uuid4() if fault == "copied" else run.id,
            project_id=run.project_id,
            user_id=run.user_id,
            attempt_id=uuid5(cursor.root_effect_id, "provider-attempt-v1"),
            prompt_hash=run.prompt_hash,
            now=old,
        )
        run.agent_state = {
            **run.agent_state,
            recovery_diagnostic.KEY: {
                "token": token,
                "issued_at": int(old.timestamp()),
            },
        }
    elif fault == "off":
        monkeypatch.setattr(
            recovery_diagnostic,
            "get_settings",
            lambda: _settings.model_copy(update={"generation_recovery_mode": "off"}),
        )
    else:
        cursor = ProviderEffectCursor(cursor.claim.model_copy(update={"token": uuid4()}), 1, 0)
    await db_session.commit()
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor), pytest.raises(RuntimeError, match="UNCONFIRMED"):
                await provider_attempts.prepare_generation_attempt(body, "/v1/chat/completions")
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 0


async def test_arm_and_first_dispatch_share_nonblocking_lock(db_session, test_engine, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.services.generation import recovery_diagnostic as d

    run, _cursor, _body, _settings = await _armed(db_session, test_engine, monkeypatch)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as first, factory() as competing:
        await d.lock_diagnostic_namespace(first, run.id)
        with pytest.raises(ValueError, match="busy"):
            await asyncio.wait_for(
                d.arm_diagnostic_output_budget(
                    competing,
                    run_id=run.id,
                    project_id=run.project_id,
                    user_id=run.user_id,
                    attempt_id=uuid4(),
                ),
                2,
            )

        await first.rollback()
        await competing.rollback()
    async with factory() as row_owner, factory() as operator:
        from yleum_api.models.generation_run import GenerationRun

        await row_owner.get(GenerationRun, run.id, with_for_update=True)
        with pytest.raises(Exception):
            await asyncio.wait_for(
                d.arm_diagnostic_output_budget(
                    operator,
                    run_id=run.id,
                    project_id=run.project_id,
                    user_id=run.user_id,
                    attempt_id=uuid4(),
                ),
                2,
            )


async def test_worker_winner_prevents_late_arm_without_new_admission(
    db_session, test_engine, monkeypatch
):
    from sqlalchemy import func, select, text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation import provider_attempts, recovery_execution
    from yleum_api.services.generation import recovery_diagnostic as d
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body, _settings = await _armed(db_session, test_engine, monkeypatch)
    run.agent_state = {key: value for key, value in run.agent_state.items() if key != d.KEY}
    await db_session.commit()
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor):
                identity = await provider_attempts.prepare_generation_attempt(
                    body, "/v1/chat/completions"
                )
            with pytest.raises(ValueError, match="unused"):
                await d.arm_diagnostic_output_budget(
                    db_session,
                    run_id=run.id,
                    project_id=run.project_id,
                    user_id=run.user_id,
                    attempt_id=identity.attempt_id,
                )
            await db_session.rollback()
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 1
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 1


def test_browser_knob_cannot_enter_normal_prompt_contract():
    from yleum_api.schemas.message import PromptRequest

    request = PromptRequest(
        prompt="synthetic",
        diagnostic_output_budget=16,
        diagnostic_intent="browser-forged",
        max_tokens=16,
    )
    assert (
        not {"diagnostic_output_budget", "diagnostic_intent", "max_tokens"}
        & request.model_dump().keys()
    )


@pytest.mark.parametrize("requested", [None, 16384])
@pytest.mark.parametrize("reason", ["length", "content_filter", "stop"])
async def test_complete_chat_reports_the_committed_diagnostic_wire_budget(
    db_session,
    test_engine,
    monkeypatch,
    requested,
    reason,
):
    import json

    import httpx
    from sqlalchemy import text

    from yleum_api.services import llm_client
    from yleum_api.services.generation import recovery_execution
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body, settings = await _armed(db_session, test_engine, monkeypatch)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: settings.model_copy(
            update={
                "mock_llm": False,
                "llm_gateway_url": "https://synthetic.invalid",
            }
        ),
    )
    monkeypatch.setattr(llm_client, "_billing_metadata", lambda *args: body["metadata"])
    sent = []

    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": reason, "message": {"content": "synthetic"}}],
                "usage": {"completion_tokens": 16},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(reply),
            **kw,
        ),
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner),
        )
        try:
            with provider_effect_scope(cursor):
                call = llm_client.complete_chat(
                    body["messages"],
                    "anthropic/claude-sonnet-4.5",
                    user_id=str(run.user_id),
                    project_id=str(run.project_id),
                    max_tokens=requested,
                )
                if reason == "stop":
                    assert await call == "synthetic"
                else:
                    with pytest.raises(llm_client.LLMError) as caught:
                        await call
                    assert caught.value.requested_max_tokens == 16
                    assert caught.value.completion_tokens == 16
                    assert caught.value.finish_reason == reason
                    assert caught.value.retry_safe is False
                    assert isinstance(caught.value, llm_client.LLMOutputTruncated) == (
                        reason == "length"
                    )
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
    assert len(sent) == 1 and sent[0]["max_tokens"] == 16


@pytest.mark.parametrize("requested", [None, 1024, 16384])
async def test_complete_chat_ordinary_budget_telemetry_uses_wire(monkeypatch, requested):
    import httpx

    from tests.test_legacy_provider_error_policy import install_transport
    from yleum_api.services import llm_client

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": "synthetic"}}],
                "usage": {"completion_tokens": 4},
            },
        ),
    )
    with pytest.raises(llm_client.LLMOutputTruncated) as caught:
        await llm_client.complete_chat([], "anthropic/claude-sonnet-4.5", max_tokens=requested)
    assert caught.value.requested_max_tokens == requested
