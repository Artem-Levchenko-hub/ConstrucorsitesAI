"""Reserve real FK-backed runs concurrently before serializing the Free counter."""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_free_chat_allowance import no_providers, owner_projects
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError
from yleum_api.main import app
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.schemas.message import PromptRequest
from yleum_api.services import entitlements
from yleum_api.services.generation import acceptance


@pytest.mark.parametrize("count", [5, 10])
async def test_free_concurrent_reserved_runs_do_not_deadlock(
    test_engine, db_session, monkeypatch, count
):
    owner, projects, _ = await owner_projects(db_session, count=count)
    owner_id = owner.id
    project_ids = [project.id for project in projects]
    jobs = no_providers(monkeypatch, owner)
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    original = entitlements.admit_free_chat_message
    arrived = 0
    all_reserved = asyncio.Event()

    async def after_real_fk_reservation(session, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == count:
            all_reserved.set()
        await all_reserved.wait()
        return await original(session, **kwargs)

    monkeypatch.setattr(entitlements, "admit_free_chat_message", after_real_fk_reservation)

    async def submit(index):
        async with factory() as session:
            actor = await session.get(User, owner_id)
            project = await session.get(Project, project_ids[index])
            try:
                return await acceptance.PromptAcceptance(
                    project_id=project.id, project=project, current_user=actor, session=session,
                    payload=PromptRequest(prompt="Create a fixture menu", skip_clarify=True,
                                          idempotency_key=f"FK_RACE_{index}"),
                ).accept()
            except ApiError as error:
                assert error.status_code == 402
                assert error.details["entitlement"] == "free_chat_messages"
                return None

    try:
        results = await asyncio.wait_for(asyncio.gather(*(submit(i) for i in range(count))), 20)
        assert sum(result is not None for result in results) == 1
        assert len(jobs) == 1
        async with factory() as session:
            assert (await session.get(User, owner_id)).free_chat_messages_used == 1
            assert await session.scalar(select(func.count()).select_from(GenerationRun)) == 1
            assert await session.scalar(
                select(func.count()).select_from(Message).where(Message.role == "user")
            ) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
