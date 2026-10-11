"""Consent gates and durable event ordering, without provider traffic."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.services import integration_consent as consent


class ReadSession:
    def __init__(self, *rows):
        self.rows = iter(rows)

    async def scalar(self, statement):
        assert statement.get_execution_options().get("populate_existing")
        return next(self.rows)


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (None, "consent_required"),
        (SimpleNamespace(granted=False), "consent_revoked"),
        (
            SimpleNamespace(granted=True, policy_version="old", policy_hash="a" * 64),
            "consent_policy_outdated",
        ),
        (
            SimpleNamespace(granted=True, policy_version="v2", policy_hash="b" * 64),
            "consent_policy_outdated",
        ),
    ],
)
async def test_gate_rejects_absent_revoked_and_stale(row, code):
    policy = SimpleNamespace(enabled=True, policy_version="v2", policy_hash="a" * 64)
    with pytest.raises(ApiError) as error:
        await consent.require_consent(ReadSession(policy, row), uuid4(), 123)
    assert error.value.status_code == 403
    assert error.value.code == code


async def test_enabled_invalid_policy_fails_closed():
    policy = SimpleNamespace(enabled=True, policy_version="", policy_hash="")
    with pytest.raises(ApiError) as error:
        await consent.require_consent(ReadSession(policy), uuid4(), 123)
    assert error.value.code == "consent_policy_invalid"


async def test_unconfigured_project_keeps_staged_legacy_behavior():
    await consent.require_consent(ReadSession(None), uuid4(), 123)


async def test_current_grant_allows_dispatch():
    policy = SimpleNamespace(enabled=True, policy_version="v2", policy_hash="a" * 64)
    state = SimpleNamespace(granted=True, policy_version="v2", policy_hash="a" * 64)
    await consent.require_consent(ReadSession(policy, state), uuid4(), 123)


@pytest.mark.parametrize(("length", "accepted"), [(32, True), (33, False)])
async def test_policy_version_matches_endpoint_length_limit(length, accepted):
    policy = SimpleNamespace(enabled=True, policy_version="v" * length, policy_hash="a" * 64)
    if accepted:
        result = await consent.policy_context(ReadSession(policy), uuid4())
        assert result.policy_version == "v" * length
    else:
        with pytest.raises(ApiError) as error:
            await consent.policy_context(ReadSession(policy), uuid4())
        assert error.value.status_code == 403
        assert error.value.code == "consent_policy_invalid"


async def setup_project(session):
    from yleum_api.models.project import Project
    from yleum_api.models.user import User

    owner = User(id=uuid4())
    project = Project(
        id=uuid4(), owner_id=owner.id, name="Consent QA", slug=str(uuid4()), template="max_miniapp"
    )
    session.add(owner)
    await session.flush()
    session.add(project)
    await session.commit()
    await consent.set_policy(
        session, project.id, enabled=True, policy_version="v2", policy_hash="a" * 64
    )
    return project.id


async def record(session, project, actor=123, **changes):
    args = dict(
        event_id=uuid4(),
        expected_revision=0,
        granted=True,
        policy_version="v2",
        policy_hash="a" * 64,
    )
    args.update(changes)
    return await consent.record_consent(session, project, actor, **args)


async def test_events_replay_without_resurrecting_revoked_consent(db_session):
    from yleum_api.models.integration_consent import IntegrationConsentEvent

    project = await setup_project(db_session)
    event = uuid4()
    first = await record(db_session, project, event_id=event)
    assert first["revision"] == 1
    await record(db_session, project, expected_revision=1, granted=False)
    assert await record(db_session, project, event_id=event) == first
    state = await consent.consent_state(db_session, project, 123)
    assert state["granted"] is False
    assert state["revision"] == 2
    assert await db_session.scalar(select(func.count()).select_from(IntegrationConsentEvent)) == 2
    with pytest.raises(ApiError) as error:
        await record(db_session, project, expected_revision=0)
    assert error.value.code == "consent_revision_conflict"
    with pytest.raises(ApiError) as error:
        await record(db_session, project, event_id=event, granted=False)
    assert error.value.code == "consent_event_conflict"


async def test_grants_are_scoped_and_old_version_cannot_grant(db_session):
    project = await setup_project(db_session)
    other = await setup_project(db_session)
    await record(db_session, project)
    for project_id, actor in [(project, 456), (other, 123)]:
        with pytest.raises(ApiError) as error:
            await consent.require_consent(db_session, project_id, actor)
        assert error.value.code == "consent_required"
    with pytest.raises(ApiError) as error:
        await record(db_session, project, actor=456, policy_version="old")
    assert error.value.code == "consent_policy_outdated"
    await consent.set_policy(
        db_session, project, enabled=True, policy_version="v3", policy_hash="b" * 64
    )
    with pytest.raises(ApiError) as error:
        await consent.require_consent(db_session, project, 123)
    assert error.value.code == "consent_policy_outdated"
    # Revoking the old grant remains possible after the required policy changes.
    revoked = await record(db_session, project, expected_revision=1, granted=False)
    assert revoked["granted"] is False


async def test_revoke_waits_for_dispatch_even_after_receipt_commit(db_session, test_engine):
    project = await setup_project(db_session)
    await record(db_session, project)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    started = asyncio.Event()

    async def revoke():
        async with factory() as session:
            started.set()
            return await record(session, project, expected_revision=1, granted=False)

    async with consent.actor_dispatch_guard(db_session, project, 123):
        await consent.require_consent(db_session, project, 123)
        await db_session.commit()  # execute_once durably records before provider HTTP.
        task = asyncio.create_task(revoke())
        await started.wait()
        await asyncio.sleep(0.15)
        assert not task.done()
    await asyncio.wait_for(task, timeout=3)
    with pytest.raises(ApiError) as error:
        await consent.require_consent(db_session, project, 123)
    assert error.value.code == "consent_revoked"


async def test_guard_timeout_and_cancellation_release_locks(db_session, test_engine):
    project = await setup_project(db_session)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with consent.actor_dispatch_guard(db_session, project, 123):
        async with factory() as other:
            with pytest.raises(ApiError) as error:
                async with consent.actor_dispatch_guard(other, project, 123, wait_seconds=0.1):
                    pytest.fail("concurrent owner acquired actor lock")
            assert error.value.code == "consent_busy"
    entered = asyncio.Event()

    async def holder():
        async with factory() as other:
            async with consent.actor_dispatch_guard(other, project, 123):
                entered.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with consent.actor_dispatch_guard(db_session, project, 123, wait_seconds=0.5):
        pass


async def test_policy_change_waits_for_dispatch_and_invalidates_cached_grant(
    db_session, test_engine
):
    project = await setup_project(db_session)
    await record(db_session, project)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    started = asyncio.Event()

    async def change_policy():
        async with factory() as session:
            started.set()
            await consent.set_policy(
                session, project, enabled=True, policy_version="v3", policy_hash="b" * 64
            )

    async with consent.actor_dispatch_guard(db_session, project, 123):
        await consent.require_consent(db_session, project, 123)
        await db_session.commit()
        task = asyncio.create_task(change_policy())
        await started.wait()
        await asyncio.sleep(0.15)
        assert not task.done()
    await asyncio.wait_for(task, timeout=3)
    with pytest.raises(ApiError) as error:
        await consent.require_consent(db_session, project, 123)
    assert error.value.code == "consent_policy_outdated"
