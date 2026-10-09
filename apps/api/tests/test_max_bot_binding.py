"""Real PostgreSQL reservations with synthetic MAX and runtime boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.sql.elements import TextClause

from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError
from yleum_api.main import app
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.routers import max_integrations as router
from yleum_api.schemas.max_integration import MaxConnectRequest
from yleum_api.services import max_client

TOKEN = "synthetic-max-binding-token"
REAL_LOCK_PUBLIC_AUTH = router._lock_public_cell_auth
REAL_SYNC_PUBLIC_AUTH = router._sync_public_cell_auth


async def projects(session, *, same_owner=False):
    owners = [User(email=None), User(email=None)]
    session.add_all(owners)
    await session.flush()
    values = [
        Project(
            owner_id=owners[0 if same_owner else index].id,
            name="Fixture",
            slug=uuid4().hex,
            template="max_miniapp",
        )
        for index in range(2)
    ]
    session.add_all(values)
    await session.commit()
    return owners, values


def binding(project, bot_id="fixture-bot", *, status="active"):
    return MaxIntegration(
        project_id=project.id,
        owner_id=project.owner_id,
        bot_id=bot_id,
        status=status,
        bot_token_enc=encrypt_strong(TOKEN),
        webhook_secret_enc=encrypt_strong("fixture-hook"),
        app_url="https://source.example.test",
        webhook_url="https://source.example.test/api/max/webhook",
    )


@pytest.fixture
def boundaries(monkeypatch):
    mocks = {
        "get_me": AsyncMock(
            return_value=max_client.MaxBot("fixture-bot", "Fixture", "fixture_bot")
        ),
        "unsubscribe": AsyncMock(),
        "subscribe": AsyncMock(),
        "sync": AsyncMock(),
    }
    monkeypatch.setattr(router, "_lock_public_cell_auth", AsyncMock(return_value=False))
    monkeypatch.setattr(router, "_sync_public_cell_auth", mocks["sync"])
    for name in ("get_me", "unsubscribe", "subscribe"):
        monkeypatch.setattr(max_client, name, mocks[name])
    return mocks


@pytest.mark.parametrize("same_owner", [False, True])
@pytest.mark.parametrize("existing_status", ["verified", "active", "error"])
async def test_other_project_conflict_is_409_without_mutation(
    client,
    db_session,
    boundaries,
    same_owner,
    existing_status,
):
    owners, values = await projects(db_session, same_owner=same_owner)
    old = binding(values[0], status=existing_status)
    db_session.add(old)
    await db_session.commit()
    old_ciphertext = old.bot_token_enc
    app.dependency_overrides[get_current_user] = lambda: owners[0 if same_owner else 1]

    response = await client.post(
        f"/api/projects/{values[1].id}/integrations/max/connect",
        json={"token": TOKEN},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "max_bot_already_bound"
    assert str(values[0].id) not in response.text and str(owners[0].id) not in response.text
    assert TOKEN not in response.text and old_ciphertext not in response.text
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1
    await db_session.refresh(old)
    assert old.bot_token_enc == old_ciphertext and old.status == existing_status
    boundaries["unsubscribe"].assert_not_awaited()
    boundaries["subscribe"].assert_not_awaited()
    boundaries["sync"].assert_not_awaited()


async def test_same_project_reconnect_preserves_binding_and_secret(db_session, boundaries):
    owners, values = await projects(db_session)
    old = binding(values[0])
    db_session.add(old)
    await db_session.commit()
    old_id, old_ciphertext, old_secret = old.id, old.bot_token_enc, old.webhook_secret_enc
    result = await router.connect_max(
        values[0].id, MaxConnectRequest(token=TOKEN), db_session, owners[0]
    )
    assert result.connected and result.bot_id == "fixture-bot"
    assert old.id == old_id and old.bot_token_enc == old_ciphertext
    assert old.webhook_secret_enc == old_secret
    assert old.webhook_url == "https://source.example.test/api/max/webhook"
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1
    boundaries["unsubscribe"].assert_not_awaited()


async def test_rejected_replacement_keeps_old_bot_and_webhook(db_session, boundaries):
    owners, values = await projects(db_session)
    source, target = binding(values[0]), binding(values[1], "other-bot")
    target.bot_token_enc = encrypt_strong("previous-target-token")
    db_session.add_all([source, target])
    await db_session.commit()
    before = (target.bot_id, target.bot_token_enc, target.webhook_secret_enc, target.webhook_url)
    with pytest.raises(ApiError) as error:
        await router.connect_max(
            values[1].id, MaxConnectRequest(token=TOKEN), db_session, owners[1]
        )
    assert error.value.code == "max_bot_already_bound" and error.value.status_code == 409
    await db_session.refresh(target)
    assert (
        target.bot_id,
        target.bot_token_enc,
        target.webhook_secret_enc,
        target.webhook_url,
    ) == before
    boundaries["unsubscribe"].assert_not_awaited()


@pytest.mark.parametrize("bot_id", [None, "", "  "])
async def test_missing_bot_identity_never_claims_binding(db_session, boundaries, bot_id):
    owners, values = await projects(db_session)
    boundaries["get_me"].return_value = max_client.MaxBot(bot_id, "Fixture", "fixture_bot")
    with pytest.raises(ApiError) as error:
        await router.connect_max(
            values[0].id, MaxConnectRequest(token=TOKEN), db_session, owners[0]
        )
    assert error.value.status_code == 503 and error.value.code == "max_api_unavailable"
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 0
    boundaries["sync"].assert_not_awaited()


async def test_foreign_project_denied_before_provider_lookup(db_session, boundaries):
    owners, values = await projects(db_session)
    with pytest.raises(ApiError) as error:
        await router.connect_max(
            values[0].id, MaxConnectRequest(token=TOKEN), db_session, owners[1]
        )
    assert error.value.status_code == 404
    boundaries["get_me"].assert_not_awaited()


async def test_parallel_different_project_connect_has_one_winner(
    test_engine, db_session, boundaries
):
    owners, values = await projects(db_session)
    owners_ids, project_ids = [owner.id for owner in owners], [project.id for project in values]
    await db_session.rollback()
    arrived, ready = 0, asyncio.Event()

    async def same_bot(token):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        return max_client.MaxBot("fixture-bot", "Fixture", "fixture_bot")

    boundaries["get_me"].side_effect = same_bot
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt(index):
        async with factory() as session:
            user = await session.get(User, owners_ids[index])
            try:
                return await router.connect_max(
                    project_ids[index], MaxConnectRequest(token=TOKEN), session, user
                )
            except ApiError as error:
                return error

    results = await asyncio.wait_for(asyncio.gather(attempt(0), attempt(1)), 10)
    errors = [result for result in results if isinstance(result, ApiError)]
    assert len(errors) == 1 and errors[0].code in {
        "max_bot_already_bound",
        "max_bot_binding_busy",
    }
    assert errors[0].status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1
    boundaries["sync"].assert_awaited_once()


async def test_database_rejects_duplicate_even_without_api_guard(db_session, boundaries):
    _, values = await projects(db_session)
    db_session.add(binding(values[0]))
    await db_session.commit()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(binding(values[1]))
            await db_session.flush()
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1


async def test_parallel_same_project_reconnect_and_retry_keep_single_binding(
    test_engine,
    db_session,
    boundaries,
):
    owners, values = await projects(db_session)
    owner_id, project_id = owners[0].id, values[0].id
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session:
            owner = await session.get(User, owner_id)
            try:
                return await router.connect_max(
                    project_id, MaxConnectRequest(token=TOKEN), session, owner
                )
            except ApiError as error:
                return error

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), 10)
    assert any(not isinstance(result, ApiError) and result.connected for result in results)
    assert all(
        (result.code == "max_bot_binding_busy" and result.status_code == 409)
        if isinstance(result, ApiError)
        else result.connected and result.bot_id == "fixture-bot"
        for result in results
    )
    assert (await attempt()).connected
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1
    boundaries["unsubscribe"].assert_not_awaited()


async def test_racing_replacements_leave_loser_credentials_and_webhook_untouched(
    test_engine,
    db_session,
    boundaries,
):
    owners, values = await projects(db_session)
    old = [binding(project, f"previous-{index}") for index, project in enumerate(values)]
    for index, item in enumerate(old):
        item.bot_token_enc = encrypt_strong(f"previous-token-{index}")
        item.webhook_url = f"https://old-{index}.example.test/api/max/webhook"
    db_session.add_all(old)
    await db_session.commit()
    ids = [item.id for item in old]
    before = [
        (item.bot_id, item.bot_token_enc, item.webhook_secret_enc, item.webhook_url) for item in old
    ]
    owner_ids, project_ids = [owner.id for owner in owners], [project.id for project in values]
    await db_session.rollback()
    ready, arrived = asyncio.Event(), 0

    async def same_bot(token):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        return max_client.MaxBot("fixture-bot", "Fixture", "fixture_bot")

    boundaries["get_me"].side_effect = same_bot
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt(index):
        async with factory() as session:
            owner = await session.get(User, owner_ids[index])
            try:
                return await router.connect_max(
                    project_ids[index], MaxConnectRequest(token=TOKEN), session, owner
                )
            except ApiError as error:
                return error

    results = await asyncio.wait_for(asyncio.gather(attempt(0), attempt(1)), 10)
    loser = next(index for index, result in enumerate(results) if isinstance(result, ApiError))
    assert results[loser].code in {"max_bot_already_bound", "max_bot_binding_busy"}
    saved = await db_session.get(MaxIntegration, ids[loser])
    assert (
        saved.bot_id,
        saved.bot_token_enc,
        saved.webhook_secret_enc,
        saved.webhook_url,
    ) == before[loser]
    assert boundaries["unsubscribe"].await_count == 1
    assert boundaries["unsubscribe"].await_args.args[1] != before[loser][3]
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 2


async def test_commit_failure_does_not_leave_new_claim_or_sync_runtime(
    test_engine,
    db_session,
    boundaries,
    monkeypatch,
):
    owners, values = await projects(db_session)
    owner_id, project_id = owners[0].id, values[0].id
    await db_session.rollback()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await session.get(User, owner_id)
        monkeypatch.setattr(
            session, "commit", AsyncMock(side_effect=RuntimeError("fixture commit failed"))
        )
        with pytest.raises(RuntimeError, match="fixture commit failed"):
            await router.connect_max(project_id, MaxConnectRequest(token=TOKEN), session, owner)
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 0
    boundaries["sync"].assert_not_awaited()
    boundaries["unsubscribe"].assert_not_awaited()


async def test_unrelated_integrity_failure_is_not_reported_as_bot_conflict(db_session, boundaries):
    _, values = await projects(db_session)
    invalid = binding(values[0], status="invalid-status")
    with pytest.raises(IntegrityError):
        await router._reserve_bot_id(
            db_session, invalid, max_client.MaxBot("fixture-bot", "Fixture", None)
        )
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 0


async def test_sync_failure_keeps_committed_reservation_for_same_project_retry(
    db_session, boundaries
):
    owners, values = await projects(db_session)
    boundaries["sync"].side_effect = RuntimeError("fixture runtime unavailable")
    with pytest.raises(RuntimeError, match="fixture runtime unavailable"):
        await router.connect_max(
            values[0].id, MaxConnectRequest(token=TOKEN), db_session, owners[0]
        )
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1
    with pytest.raises(ApiError) as error:
        await router.connect_max(
            values[1].id, MaxConnectRequest(token=TOKEN), db_session, owners[1]
        )
    assert error.value.code == "max_bot_already_bound"
    boundaries["sync"].side_effect = None
    result = await router.connect_max(
        values[0].id, MaxConnectRequest(token=TOKEN), db_session, owners[0]
    )
    assert result.connected
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1


async def test_failed_unsubscribe_releases_new_reservation_and_preserves_old(
    db_session, boundaries
):
    owners, values = await projects(db_session)
    old = binding(values[0], "previous-bot")
    old.bot_token_enc = encrypt_strong("previous-token")
    db_session.add(old)
    await db_session.commit()
    original_id = old.id
    boundaries["unsubscribe"].side_effect = max_client.MaxApiUnavailable("fixture MAX unavailable")
    with pytest.raises(ApiError) as error:
        await router.connect_max(
            values[0].id, MaxConnectRequest(token=TOKEN), db_session, owners[0]
        )
    assert error.value.status_code == 503
    await db_session.rollback()
    saved = await db_session.get(MaxIntegration, original_id)
    assert (
        saved.bot_id == "previous-bot" and decrypt_strong(saved.bot_token_enc) == "previous-token"
    )
    assert saved.webhook_url == "https://source.example.test/api/max/webhook"
    boundaries["unsubscribe"].side_effect = None
    # Load IDs from persisted fixtures because rollback may expire ORM attributes.
    target = (
        await db_session.execute(select(Project).where(Project.id != saved.project_id))
    ).scalar_one()
    target_owner = await db_session.get(User, target.owner_id)
    result = await router.connect_max(
        target.id, MaxConnectRequest(token=TOKEN), db_session, target_owner
    )
    assert result.connected


async def test_verify_cannot_reassign_to_another_project_bot(db_session, boundaries):
    owners, values = await projects(db_session)
    source, target = binding(values[0]), binding(values[1], "previous-target-bot")
    db_session.add_all([source, target])
    await db_session.commit()
    with pytest.raises(ApiError) as error:
        await router.verify_max(values[1].id, db_session, owners[1])
    assert error.value.code == "max_bot_already_bound"
    await db_session.refresh(target)
    assert target.bot_id == "previous-target-bot" and target.status == "active"


@pytest.mark.parametrize("bot_id", [None, "", "  "])
async def test_verify_missing_identity_preserves_existing_binding(db_session, boundaries, bot_id):
    owners, values = await projects(db_session)
    old = binding(values[0])
    db_session.add(old)
    await db_session.commit()
    before = (old.bot_id, old.bot_token_enc, old.webhook_secret_enc, old.status)
    boundaries["get_me"].return_value = max_client.MaxBot(bot_id, "Fixture", "fixture_bot")
    with pytest.raises(ApiError) as error:
        await router.verify_max(values[0].id, db_session, owners[0])
    assert error.value.status_code == 503
    await db_session.refresh(old)
    assert (old.bot_id, old.bot_token_enc, old.webhook_secret_enc, old.status) == before


async def test_slow_provider_race_returns_409_before_production_db_timeout(
    test_engine,
    db_session,
    boundaries,
):
    owners, values = await projects(db_session)
    for index, project in enumerate(values):
        old = binding(project, f"previous-{index}")
        old.bot_token_enc = encrypt_strong(f"previous-token-{index}")
        db_session.add(old)
    await db_session.commit()
    owner_ids, project_ids = [owner.id for owner in owners], [project.id for project in values]
    await db_session.rollback()
    arrived, ready = 0, asyncio.Event()

    async def same_bot(token):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        return max_client.MaxBot("fixture-bot", "Fixture", "fixture_bot")

    async def slow_unsubscribe(token, url):
        # MAX permits 15s; production PostgreSQL commands time out after 10s.
        await asyncio.sleep(12)

    boundaries["get_me"].side_effect = same_bot
    boundaries["unsubscribe"].side_effect = slow_unsubscribe
    engine = create_async_engine(test_engine.url, connect_args={"command_timeout": 10.0})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt(index):
        async with factory() as session:
            owner = await session.get(User, owner_ids[index])
            try:
                return await router.connect_max(
                    project_ids[index],
                    MaxConnectRequest(token=TOKEN),
                    session,
                    owner,
                )
            except ApiError as error:
                return error

    try:
        results = await asyncio.wait_for(
            asyncio.gather(attempt(0), attempt(1), return_exceptions=True), 18
        )
        errors = [result for result in results if isinstance(result, ApiError)]
        assert len(errors) == 1, [type(result).__name__ for result in results]
        assert errors[0].status_code == 409
        assert sum(not isinstance(result, BaseException) for result in results) == 1
    finally:
        await engine.dispose()


async def test_database_unique_violation_is_mapped_when_conflict_prechecks_race(
    test_engine,
    db_session,
    boundaries,
    monkeypatch,
):
    owners, values = await projects(db_session)
    owner_ids, project_ids = [owner.id for owner in owners], [project.id for project in values]
    await db_session.rollback()
    arrived, ready = 0, asyncio.Event()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt(index):
        async with factory() as session:
            original = session.scalar

            async def stale_precheck(statement, *args, **kwargs):
                nonlocal arrived
                if isinstance(statement, TextClause) and "pg_try_advisory_xact_lock" in str(
                    statement
                ):
                    # Model another writer that doesn't participate in advisory locking.
                    return True
                result = await original(statement, *args, **kwargs)
                if isinstance(statement, Select) and "max_integrations.bot_id" in str(statement):
                    assert result is None
                    arrived += 1
                    if arrived == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), 5)
                return result

            monkeypatch.setattr(session, "scalar", stale_precheck)
            owner = await session.get(User, owner_ids[index])
            try:
                return await router.connect_max(
                    project_ids[index], MaxConnectRequest(token=TOKEN), session, owner
                )
            except ApiError as error:
                return error

    results = await asyncio.wait_for(asyncio.gather(attempt(0), attempt(1)), 10)
    errors = [result for result in results if isinstance(result, ApiError)]
    assert len(errors) == 1 and errors[0].code == "max_bot_already_bound"
    assert errors[0].status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1


async def test_real_public_lock_and_sync_do_not_invert_row_advisory_order(
    test_engine,
    db_session,
    boundaries,
    monkeypatch,
):
    owners, values = await projects(db_session)
    owner_id, project_id = owners[0].id, values[0].id
    await db_session.rollback()
    monkeypatch.setattr(router, "_lock_public_cell_auth", REAL_LOCK_PUBLIC_AUTH)
    monkeypatch.setattr(router, "_sync_public_cell_auth", REAL_SYNC_PUBLIC_AUTH)
    monkeypatch.setattr(
        router.project_cell_runtime,
        "resolve_project_cell_public_selection",
        AsyncMock(return_value=SimpleNamespace(selected=True)),
    )
    syncing, release = asyncio.Event(), asyncio.Event()

    async def configure(project_id, payload):
        syncing.set()
        await asyncio.wait_for(release.wait(), 5)
        return {"applied": True}

    monkeypatch.setattr(router.orchestrator_client, "configure_published_cell", configure)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session:
            owner = await session.get(User, owner_id)
            try:
                return await router.connect_max(
                    project_id, MaxConnectRequest(token=TOKEN), session, owner
                )
            except ApiError as error:
                return error

    first = asyncio.create_task(attempt())
    try:
        await asyncio.wait_for(syncing.wait(), 3)
        concurrent = await asyncio.wait_for(attempt(), 1)
        assert isinstance(concurrent, ApiError) and concurrent.status_code == 503
    finally:
        release.set()
        await asyncio.wait_for(first, 5)
    repeated = await attempt()
    assert repeated.connected
    assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1


async def test_slow_same_project_replacement_returns_busy_instead_of_row_timeout(
    test_engine,
    db_session,
    boundaries,
):
    owners, values = await projects(db_session)
    old = binding(values[0], "previous-bot")
    old.bot_token_enc = encrypt_strong("previous-token")
    db_session.add(old)
    await db_session.commit()
    owner_id, project_id = owners[0].id, values[0].id
    await db_session.rollback()
    replacing = asyncio.Event()

    async def slow_unsubscribe(token, url):
        replacing.set()
        await asyncio.sleep(12)

    boundaries["unsubscribe"].side_effect = slow_unsubscribe
    engine = create_async_engine(test_engine.url, connect_args={"command_timeout": 10.0})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt():
        async with factory() as session:
            owner = await session.get(User, owner_id)
            try:
                return await router.connect_max(
                    project_id, MaxConnectRequest(token=TOKEN), session, owner
                )
            except ApiError as error:
                return error

    first = asyncio.create_task(attempt())
    try:
        await asyncio.wait_for(replacing.wait(), 5)
        second = await asyncio.wait_for(attempt(), 13)
        assert isinstance(second, ApiError) and second.status_code == 409
        assert second.code == "max_bot_binding_busy"
        assert (await asyncio.wait_for(first, 15)).connected
        assert await db_session.scalar(select(func.count()).select_from(MaxIntegration)) == 1
    finally:
        if not first.done():
            await first
        await engine.dispose()
