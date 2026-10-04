"""Free accepts one owner-wide human message; no real generation dispatch."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from yleum_api.core.config import get_settings
from yleum_api.core.deps import get_current_user
from yleum_api.main import app
from yleum_api.models.billing import FREE_PLAN_ID, BillingAccount, Subscription
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services.generation import acceptance


async def owner_projects(session, plan_id=FREE_PLAN_ID, count=2):
    owner = User(email=f"free-quota-{uuid4().hex}@example.com", password_hash="test-only")
    session.add(owner)
    await session.flush()
    account = BillingAccount(
        scope="personal", personal_user_id=owner.id, created_by_user_id=owner.id
    )
    session.add(account)
    await session.flush()
    session.add(
        Subscription(
            billing_account_id=account.id, user_id=owner.id, plan_id=plan_id, status="active"
        )
    )
    projects = [
        Project(owner_id=owner.id, name="QA quota", slug=uuid4().hex, template="blank")
        for _ in range(count)
    ]
    session.add_all(projects)
    await session.commit()
    return owner, projects, account


def no_providers(monkeypatch, owner, **overrides):
    settings = get_settings().model_copy(
        update={
            "use_generation_worker": False,
            "unlimited_generations": False,
            "use_progressive_discovery": False,
            "use_clarify_interview": False,
            "use_result_type_router": False,
            "use_project_memory": False,
            **overrides,
        }
    )
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    jobs = []
    monkeypatch.setattr(acceptance, "_spawn_process_prompt", lambda **args: jobs.append(args))

    async def no_publish(*_args):
        return None

    monkeypatch.setattr(acceptance, "get_redis", lambda: SimpleNamespace(publish=no_publish))
    app.dependency_overrides[get_current_user] = lambda: owner
    return jobs


async def test_free_second_human_message_other_project_is_denied_before_dispatch(
    client, db_session, monkeypatch
):
    owner, projects, _ = await owner_projects(db_session)
    jobs = no_providers(monkeypatch, owner)
    payload = {
        "prompt": "Build a QA menu",
        "skip_clarify": True,
        "idempotency_key": "QA_FREE_HUMAN_FIRST",
    }
    try:
        first = await client.post(f"/api/projects/{projects[0].id}/prompt", json=payload)
        assert first.status_code == 202, first.text
        replay = await client.post(f"/api/projects/{projects[0].id}/prompt", json=payload)
        assert replay.status_code == 202 and replay.json()["replayed"]
        assert replay.json()["run_id"] == first.json()["run_id"]
        second = await client.post(
            f"/api/projects/{projects[1].id}/prompt",
            json={
                **payload,
                "prompt": "Continue the QA menu",
                "idempotency_key": "QA_FREE_HUMAN_SECOND",
            },
        )
        assert second.status_code == 402, second.text
        assert second.json()["error"]["code"] == "entitlement_exceeded"
        assert second.json()["error"]["details"]["entitlement"] == "free_chat_messages"
        await db_session.rollback()
        assert len(jobs) == 1
        assert len(list((await db_session.scalars(select(GenerationRun))).all())) == 1
        assert (
            len(
                list(
                    (await db_session.scalars(select(Message).where(Message.role == "user"))).all()
                )
            )
            == 1
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_independent_sessions_race_one_owner_only_one_admission(
    test_engine, db_session, monkeypatch
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.core.errors import ApiError
    from yleum_api.schemas.message import PromptRequest

    owner, projects, _ = await owner_projects(db_session)
    jobs = no_providers(monkeypatch, owner)
    owner_id = owner.id
    project_ids = [project.id for project in projects]
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    start = asyncio.Event()

    async def submit(index):
        async with factory() as session:
            current = await session.get(User, owner_id)
            project = await session.get(Project, project_ids[index])
            await start.wait()
            try:
                return await acceptance.PromptAcceptance(
                    project_id=project.id,
                    project=project,
                    current_user=current,
                    session=session,
                    payload=PromptRequest(
                        prompt=f"QA request {index}",
                        skip_clarify=True,
                        idempotency_key=f"QA_CONCURRENT_HUMAN_{index}",
                    ),
                ).accept()
            except ApiError as error:
                assert error.status_code == 402 and error.code == "entitlement_exceeded"
                assert error.details["entitlement"] == "free_chat_messages"
                return None

    tasks = [asyncio.create_task(submit(index)) for index in range(2)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    assert sum(result is not None for result in results) == 1 and len(jobs) == 1
    async with factory() as session:
        current = await session.get(User, owner_id)
        assert current.free_chat_messages_used == 1
        assert len(list((await session.scalars(select(GenerationRun))).all())) == 1
        assert (
            len(list((await session.scalars(select(Message).where(Message.role == "user"))).all()))
            == 1
        )


async def test_free_first_message_builds_without_mandatory_clarification(
    client, db_session, monkeypatch
):
    from unittest.mock import AsyncMock, Mock

    owner, projects, _ = await owner_projects(db_session, count=1)
    jobs = no_providers(
        monkeypatch,
        owner,
        use_progressive_discovery=True,
        use_batch_discovery=True,
        use_async_onboarding=True,
        use_clarify_interview=True,
    )
    discovery = AsyncMock(side_effect=AssertionError("Free cannot require a second message"))
    deferred = Mock(side_effect=AssertionError("Free cannot require a follow-up survey"))
    monkeypatch.setattr(acceptance, "_batch_discovery_turn", discovery)
    monkeypatch.setattr(acceptance, "run_discovery", discovery)
    monkeypatch.setattr(acceptance, "_spawn_async_onboarding", deferred)
    monkeypatch.setattr(acceptance, "_spawn_clarify", deferred)
    try:
        response = await client.post(
            f"/api/projects/{projects[0].id}/prompt",
            json={
                "prompt": "Create a coffee menu",
                "idempotency_key": "QA_FIRST_ONBOARDING_MESSAGE",
            },
        )
        assert response.status_code == 202 and response.json()["mode"] == "build"
        assert not response.json()["survey_pending"] and len(jobs) == 1
        discovery.assert_not_awaited()
        deferred.assert_not_called()
        await db_session.refresh(owner)
        assert owner.free_chat_messages_used == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_rejected_admission_does_not_spend_and_completed_or_cancelled_run_never_resets(
    client, db_session, monkeypatch
):
    from yleum_api.services.generation_runs import load_generation_dispatch

    owner, projects, _ = await owner_projects(db_session)
    project_id = projects[0].id
    other_id = projects[1].id
    owner_id = owner.id
    jobs = no_providers(monkeypatch, owner)
    payload = {
        "prompt": "QA create menu",
        "skip_clarify": True,
        "idempotency_key": "QA_ADMISSION_INVALID",
    }
    try:
        rejected = await client.post(
            f"/api/projects/{project_id}/prompt",
            json={
                **payload,
                "max_config_version": 9,
            },
        )
        assert rejected.status_code == 409 and not jobs
        await db_session.rollback()
        await db_session.refresh(owner)
        assert owner.free_chat_messages_used == 0
        assert not list((await db_session.scalars(select(GenerationRun))).all())
        first = await client.post(f"/api/projects/{project_id}/prompt", json=payload)
        assert first.status_code == 202 and len(jobs) == 1
        run = await db_session.get(GenerationRun, UUID(first.json()["run_id"]))
        for _ in range(3):
            assert load_generation_dispatch(run).user_id == owner_id
        run.status = "cancelled"
        await db_session.commit()
        # A completed/cancelled or deleted app cannot release the lifetime allowance.
        await db_session.delete(await db_session.get(Project, project_id))
        await db_session.commit()
        denied = await client.post(
            f"/api/projects/{other_id}/prompt",
            json={
                **payload,
                "idempotency_key": "QA_AFTER_DELETE_HUMAN",
            },
        )
        assert denied.status_code == 402 and len(jobs) == 1
        await db_session.rollback()
        await db_session.refresh(owner)
        assert owner.free_chat_messages_used == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_historical_free_run_message_or_success_counter_is_already_spent(db_session):
    import pytest

    from yleum_api.core.errors import ApiError
    from yleum_api.services.entitlements import admit_free_chat_message

    for history in ["failed_run", "user_message", "completed_deleted_run"]:
        owner, projects, _ = await owner_projects(db_session, count=1)
        owner_id = owner.id
        if history == "failed_run":
            db_session.add(
                GenerationRun(
                    project_id=projects[0].id,
                    user_id=owner_id,
                    status="failed",
                    prompt_hash="qa-only",
                    idempotency_key="QA_LEGACY_RUN",
                )
            )
        elif history == "user_message":
            db_session.add(Message(project_id=projects[0].id, role="user", content="QA old prompt"))
        else:
            owner.free_generations_used = 1
        await db_session.commit()
        with pytest.raises(ApiError) as denied:
            await admit_free_chat_message(db_session, user_id=owner_id, run_id=uuid4())
        assert denied.value.status_code == 402 and denied.value.details["used"] == 1
        await db_session.rollback()


async def test_paid_pro_and_business_chat_allowance_and_billing_are_unchanged(
    client, db_session, monkeypatch
):
    from yleum_api.models.billing import BUSINESS_PLAN_ID, PRO_PLAN_ID, BillingPlan
    from yleum_api.models.generation_billing import GenerationBillingPolicy
    from yleum_api.models.wallet import Wallet
    from yleum_api.services.entitlements import entitlement_limit

    for plan_id, capacity in [(PRO_PLAN_ID, 3), (BUSINESS_PLAN_ID, 10)]:
        owner, projects, account = await owner_projects(db_session, plan_id, count=2)
        owner.free_generations_used = 99
        db_session.add(
            Wallet(user_id=owner.id, billing_account_id=account.id, balance_rub=Decimal("20"))
        )
        await db_session.commit()
        jobs = no_providers(monkeypatch, owner)
        try:
            for index, project in enumerate(projects):
                response = await client.post(
                    f"/api/projects/{project.id}/prompt",
                    json={
                        "prompt": f"QA paid message {index}",
                        "skip_clarify": True,
                        "idempotency_key": f"QA_PAID_HUMAN_{index}",
                    },
                )
                assert response.status_code == 202, response.text
            assert len(jobs) == 2 and all(job["is_free"] is False for job in jobs)
            await db_session.refresh(owner)
            assert owner.free_chat_messages_used == 0 and owner.free_generations_used == 99
            policies = list(
                (
                    await db_session.scalars(
                        select(GenerationBillingPolicy)
                        .join(GenerationRun, GenerationRun.id == GenerationBillingPolicy.run_id)
                        .where(GenerationRun.user_id == owner.id)
                    )
                ).all()
            )
            assert len(policies) == 2 and not any(policy.is_free for policy in policies)
            plan = await db_session.get(BillingPlan, plan_id)
            assert entitlement_limit(plan, "max_projects") == capacity
            assert entitlement_limit(plan, "static_publish_slots") == capacity
            assert owner.free_publication_project_id is None
            current = await client.get("/api/auth/me")
            assert current.status_code == 200
            assert current.json()["user_chat_messages_limit"] is None
            assert current.json()["user_chat_messages_remaining"] is None
        finally:
            app.dependency_overrides.pop(get_current_user, None)


async def test_current_owner_me_reports_only_own_remaining_free_message(
    client, db_session, monkeypatch
):
    owner, projects, _ = await owner_projects(db_session, count=1)
    jobs = no_providers(monkeypatch, owner)
    try:
        before = await client.get("/api/auth/me", params={"user_id": str(uuid4())})
        assert before.status_code == 200 and before.json()["id"] == str(owner.id)
        assert before.json()["user_chat_messages_limit"] == 1
        assert before.json()["user_chat_messages_remaining"] == 1
        assert "free_chat_messages_used" not in before.json()
        assert "free_publication_project_id" not in before.json()
        response = await client.post(
            f"/api/projects/{projects[0].id}/prompt",
            json={
                "prompt": "QA owner menu",
                "idempotency_key": "QA_OWNER_DTO_MESSAGE",
                "skip_clarify": True,
            },
        )
        assert response.status_code == 202 and len(jobs) == 1
        after = await client.get("/api/auth/me")
        assert after.status_code == 200
        assert after.json()["user_chat_messages_limit"] == 1
        assert after.json()["user_chat_messages_remaining"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
