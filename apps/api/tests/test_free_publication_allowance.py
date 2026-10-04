"""Free reservations survive uncertain publication and retain the project fence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_cell_publication_api import seed
from tests.test_free_chat_allowance import owner_projects
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError
from yleum_api.main import app
from yleum_api.models.billing import FREE_PLAN_ID, BillingAccount, Subscription
from yleum_api.models.billing_usage_event import BillingUsageEvent
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services import entitlements, orchestrator_client, project_cell_runtime


async def test_free_project_guard_race_creates_only_one_owned_app(test_engine, db_session):
    owner, _, _ = await owner_projects(db_session, count=0)
    owner_id = owner.id
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    start = asyncio.Event()

    async def create():
        async with factory() as session:
            await start.wait()
            try:
                await entitlements.assert_can_create_project(session, owner_id)
            except ApiError as error:
                assert error.status_code == 402 and error.details["entitlement"] == "max_projects"
                return False
            session.add(
                Project(owner_id=owner_id, name="QA race app", slug=uuid4().hex, template="blank")
            )
            await session.commit()
            return True

    tasks = [asyncio.create_task(create()) for _ in range(2)]
    start.set()
    assert sum(await asyncio.wait_for(asyncio.gather(*tasks), 15)) == 1
    assert len(list((await db_session.scalars(select(Project))).all())) == 1


async def test_publication_race_has_one_durable_project_even_without_journal(
    test_engine, db_session
):
    owner, projects, _ = await owner_projects(db_session)
    owner_id = owner.id
    ids = [project.id for project in projects]
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    start = asyncio.Event()

    async def publish(index):
        async with factory() as session:
            project = await session.get(Project, ids[index])
            await start.wait()
            try:
                await entitlements.assert_can_publish(session, project)
                # Simulate a lost controller acknowledgement after dispatch. The
                # caller's rollback cannot undo the separately committed reservation.
                target_id = project.id
                await session.rollback()
                return target_id
            except ApiError as error:
                assert error.status_code == 402
                assert error.details["entitlement"] == "static_publish_slots"
                return None

    tasks = [asyncio.create_task(publish(index)) for index in range(2)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    selected = [value for value in results if value is not None]
    assert len(selected) == 1
    await db_session.refresh(owner)
    assert owner.free_publication_project_id == selected[0]
    assert not list((await db_session.scalars(select(BillingUsageEvent))).all())
    allowed = await db_session.get(Project, selected[0])
    await entitlements.assert_can_publish(db_session, allowed)
    await db_session.delete(allowed)
    await db_session.commit()
    other = await db_session.get(Project, next(value for value in ids if value != selected[0]))
    with pytest.raises(ApiError) as denied:
        await entitlements.assert_can_publish(db_session, other)
    assert denied.value.status_code == 402
    await db_session.refresh(owner)
    assert owner.free_publication_project_id == selected[0]
    usages = await entitlements.entitlement_usages(db_session, owner_id)
    slot = next(item for item in usages if item.key == "static_publish_slots")
    assert slot.limit == slot.used == 1


@pytest.mark.parametrize("outcome", ["unknown", "journal_failure"])
async def test_actual_publication_retains_fence_and_slot_before_controller(
    client, db_session, test_engine, monkeypatch, outcome
):
    value, _ = await seed(db_session)
    project = value["project"]
    project_id = project.id
    owner = await db_session.get(User, project.owner_id)
    account = BillingAccount(
        scope="personal", personal_user_id=owner.id, created_by_user_id=owner.id
    )
    db_session.add(account)
    await db_session.flush()
    db_session.add(
        Subscription(
            billing_account_id=account.id, user_id=owner.id, plan_id=FREE_PLAN_ID, status="active"
        )
    )
    await db_session.commit()
    owner_id = owner.id
    calls = 0
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def controller(project_id, _payload):
        nonlocal calls
        calls += 1
        async with factory() as observer:
            current = await observer.get(User, owner_id)
            assert current.free_publication_project_id == project_id
            locked = await observer.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:p))"), {"p": str(project_id)}
            )
            assert locked is False  # Quota reservation did not commit the primary fence.
        if outcome == "unknown":
            raise httpx.ReadTimeout("QA_PUBLICATION_RESPONSE_LOST")
        return {"phase": "queued", "run_id": "QA_PUBLICATION_ACCEPTED"}

    monkeypatch.setattr(orchestrator_client, "publish_project_cell", controller)
    monkeypatch.setattr(
        project_cell_runtime,
        "_get_cell_resources",
        AsyncMock(return_value=SimpleNamespace(state="resources_ready")),
    )
    if outcome == "journal_failure":
        monkeypatch.setattr(
            entitlements,
            "record_publication",
            AsyncMock(side_effect=RuntimeError("QA_JOURNAL_FAILURE")),
        )
    try:
        if outcome == "unknown":
            with pytest.raises(httpx.ReadTimeout):
                await client.post(
                    f"/api/projects/{project_id}/deploy",
                    json={"idempotency_key": "QA_PUBLICATION_ONCE"},
                )
            await db_session.rollback()
        else:
            response = await client.post(
                f"/api/projects/{project_id}/deploy",
                json={"idempotency_key": "QA_PUBLICATION_ONCE"},
            )
            assert (
                response.status_code == 200
                and response.json()["run_id"] == "QA_PUBLICATION_ACCEPTED"
            )
        assert calls == 1
        await db_session.refresh(owner)
        assert owner.free_publication_project_id == project_id
        other = Project(
            owner_id=owner.id, name="Preserved QA old app", slug=uuid4().hex, template="blank"
        )
        db_session.add(other)
        await db_session.commit()
        with pytest.raises(ApiError) as denied:
            await entitlements.assert_can_publish(db_session, other)
        assert denied.value.status_code == 402 and calls == 1
        assert not list((await db_session.scalars(select(BillingUsageEvent))).all())
    finally:
        app.dependency_overrides.pop(get_current_user, None)
