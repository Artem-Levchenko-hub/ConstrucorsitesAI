"""Project contention is a pre-reservation refusal, never a running build."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from yleum_api.core.config import get_settings
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError
from yleum_api.main import app
from yleum_api.models.generation_billing import GenerationBillingPolicy
from yleum_api.models.generation_credits import GenerationCreditReservation
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.provider_call import ProviderCall
from yleum_api.models.usage import Usage
from yleum_api.models.wallet import Wallet
from yleum_api.schemas.message import PromptRequest
from yleum_api.services.generation import acceptance

from .test_generation_runs import _NoopRedis, _owner_and_project

BUSY_DETAILS = {"reason": "project_admission_busy", "retryable": True, "retry_after": 2}


@pytest.mark.parametrize("acquired", [False, None, 1, "true"])
async def test_busy_admission_refuses_before_receipt_or_reservation(monkeypatch, acquired):
    project_id, user_id = uuid4(), uuid4()
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=acquired),
        execute=AsyncMock(side_effect=AssertionError("Blocking lock or reservation is forbidden")),
        get=AsyncMock(side_effect=AssertionError("Busy admission cannot read a receipt")),
    )
    reserve = AsyncMock(side_effect=AssertionError("Busy admission cannot reserve a run"))
    monkeypatch.setattr(acceptance, "reserve_generation_run", reserve)
    request = acceptance.PromptAcceptance(
        project_id=project_id,
        payload=PromptRequest(prompt="Change this app", idempotency_key="same-submit"),
        session=session,
        current_user=SimpleNamespace(id=user_id),
        project=SimpleNamespace(id=project_id, owner_id=user_id),
    )
    with pytest.raises(ApiError) as busy:
        await request.reserve_or_replay()
    assert (busy.value.code, busy.value.status_code) == ("project_busy", 409)
    assert busy.value.details == BUSY_DETAILS
    assert busy.value.message == (
        "Проект сейчас занят другой операцией. "
        "Подождите немного и отправьте запрос ещё раз."
    )
    statement, params = session.scalar.await_args.args
    assert str(statement) == "SELECT pg_try_advisory_xact_lock(hashtext(:project_id))"
    assert params == {"project_id": str(project_id)}
    assert session.scalar.await_count == 1
    session.execute.assert_not_awaited()
    session.get.assert_not_awaited()
    reserve.assert_not_awaited()
    assert "request_accepted" not in busy.value.details


async def _effect_snapshot(session):
    models = (GenerationRun, Message, GenerationCreditReservation,
              GenerationBillingPolicy, ProviderCall, Usage)
    counts = {model.__tablename__: await session.scalar(select(func.count()).select_from(model))
              for model in models}
    wallets = (await session.execute(select(Wallet.user_id, Wallet.balance_rub))).all()
    return counts, wallets


async def _prompt_fixture(db_session, monkeypatch):
    owner, project = await _owner_and_project(db_session)
    settings = get_settings().model_copy(update={
        "use_generation_worker": False, "unlimited_generations": False,
        "force_model": None, "use_progressive_discovery": False,
        "use_clarify_interview": False, "use_followup_appification": False,
        "use_result_type_router": False,
    })
    spawned = []
    app.dependency_overrides[get_current_user] = lambda: owner
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    monkeypatch.setattr(
        acceptance, "_spawn_process_prompt", lambda **kwargs: spawned.append(kwargs),
    )
    monkeypatch.setattr(acceptance, "get_redis", lambda: _NoopRedis())
    return owner, project, spawned


async def test_native_project_lock_busy_has_no_effect_then_same_key_is_accepted_once(
    client, db_session, test_engine, monkeypatch,
):
    owner, project, spawned = await _prompt_fixture(db_session, monkeypatch)
    # Keep the other project's financial allowance independent: the default
    # account permits one initial generation, which this test must not consume
    # before it verifies acceptance of the originally blocked project.
    other_owner, other = await _owner_and_project(db_session)
    payload = {"prompt": "Собери статический сайт", "skip_clarify": True,
               "idempotency_key": "busy-then-same-key"}
    before = await _effect_snapshot(db_session)
    async with test_engine.connect() as blocker:
        await blocker.execute(text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
                              {"project_id": str(project.id)})
        response = await asyncio.wait_for(
            client.post(f"/api/projects/{project.id}/prompt", json=payload), timeout=2,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "project_busy"
        assert response.json()["error"]["details"] == BUSY_DETAILS
        assert await _effect_snapshot(db_session) == before
        assert spawned == []

        app.dependency_overrides[get_current_user] = lambda: other_owner
        unrelated = await asyncio.wait_for(
            client.post(f"/api/projects/{other.id}/prompt", json=payload), timeout=2,
        )
        assert unrelated.status_code == 202, unrelated.text
        assert len(spawned) == 1
        assert await db_session.scalar(select(func.count()).select_from(GenerationRun).where(
            GenerationRun.project_id == project.id)) == 0

    app.dependency_overrides[get_current_user] = lambda: owner
    first = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    assert first.status_code == replay.status_code == 202
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert first.json()["replayed"] is False and replay.json()["replayed"] is True
    assert len(spawned) == 2  # One request for each independent project, no replay dispatch.
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun).where(
        GenerationRun.project_id == project.id)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Message).where(
        Message.project_id == project.id)) == 2

    conflict = await client.post(f"/api/projects/{project.id}/prompt", json={
        **payload, "prompt": "Another payload with the same immutable request key",
    })
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(spawned) == 2


async def test_native_non_owner_is_refused_before_busy_project_information(
    client, db_session, test_engine, monkeypatch,
):
    _owner, project, spawned = await _prompt_fixture(db_session, monkeypatch)
    other_owner, _other_project = await _owner_and_project(db_session)
    app.dependency_overrides[get_current_user] = lambda: other_owner
    before = await _effect_snapshot(db_session)
    async with test_engine.connect() as blocker:
        await blocker.execute(text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
                              {"project_id": str(project.id)})
        response = await asyncio.wait_for(client.post(
            f"/api/projects/{project.id}/prompt",
            json={"prompt": "unauthorized", "idempotency_key": "not-owner"},
        ), timeout=2)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert await _effect_snapshot(db_session) == before
    assert spawned == []
