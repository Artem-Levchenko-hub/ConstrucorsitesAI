from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.security import create_access_token
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import product_advice as advice_router
from omnia_api.services.product_advisor import AdviceItem, ProductAdviceResult


class FakeRedis:
    def __init__(self, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.setex_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | None:
        if self.fail_get:
            raise ConnectionError("redis get unavailable")
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self.fail_set:
            raise ConnectionError("redis set unavailable")
        self.values[key] = value
        self.setex_calls.append((key, ttl))


async def _user(db_session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="test", is_anon=False)
    db_session.add(user)
    await db_session.flush()
    return user


def _login(client: httpx.AsyncClient, user: User) -> None:
    client.cookies.clear()
    client.cookies.set(
        get_settings().jwt_cookie_name,
        create_access_token(user.id),
    )


async def _project_with_snapshots(
    db_session: AsyncSession,
    owner: User,
    *,
    template: str = "max_miniapp",
) -> tuple[Project, Snapshot, Snapshot]:
    project = Project(
        owner_id=owner.id,
        name="Кофе рядом",
        slug=f"coffee-{template}-{str(owner.id)[:8]}",
        template=template,
    )
    db_session.add(project)
    await db_session.flush()
    material = Snapshot(
        project_id=project.id,
        commit_sha="b" * 40,
        prompt_text="Добавь каталог кофе и историю заказов",
        created_at=datetime.now(UTC),
    )
    db_session.add(material)
    await db_session.flush()
    cosmetic = Snapshot(
        project_id=project.id,
        commit_sha="c" * 40,
        prompt_text="Поменяй цвет заголовка",
        parent_id=material.id,
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    db_session.add(cosmetic)
    await db_session.flush()
    project.current_snapshot_id = cosmetic.id
    await db_session.commit()
    return project, material, cosmetic


def _ranked_result() -> ProductAdviceResult:
    return ProductAdviceResult(
        archetype="commerce",
        source="model",
        items=(
            AdviceItem(
                id="saved-favorites",
                kind="feature",
                title="Избранное",
                benefit="Быстрее вернуться к выбору",
                prompt="Добавь сохранение избранного и проверь основной поток.",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_product_advice_requires_authentication(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session, "owner-auth@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    client.cookies.clear()

    response = await client.post(f"/api/projects/{project.id}/product-advice")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_product_advice_hides_other_users_and_non_max_projects(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session, "owner-scope@example.com")
    other = await _user(db_session, "other-scope@example.com")
    max_project, _, _ = await _project_with_snapshots(db_session, owner)
    site_project, _, _ = await _project_with_snapshots(
        db_session,
        owner,
        template="fullstack",
    )

    _login(client, other)
    hidden = await client.post(f"/api/projects/{max_project.id}/product-advice")
    _login(client, owner)
    non_max = await client.post(f"/api/projects/{site_project.id}/product-advice")

    assert hidden.status_code == 404
    assert non_max.status_code == 404


@pytest.mark.asyncio
async def test_advice_analyzes_and_caches_current_snapshot(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session, "owner-cache@example.com")
    project, _, cosmetic = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis()
    model_calls = 0
    read_commits: list[str] = []

    async def generate(*_args, **_kwargs) -> ProductAdviceResult:
        nonlocal model_calls
        model_calls += 1
        return _ranked_result()

    def read_files(_project_id: UUID, commit_sha: str) -> dict[str, str]:
        read_commits.append(commit_sha)
        return {"src/app/page.tsx": "Каталог и корзина"}

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", read_files)

    url = f"/api/projects/{project.id}/product-advice"
    first = await client.post(url)
    second = await client.post(url)

    assert first.status_code == 200
    assert first.json()["source"] == "model"
    assert first.json()["current_snapshot_id"] == str(cosmetic.id)
    assert first.json()["analysis_snapshot_id"] == str(cosmetic.id)
    assert first.json()["items"] == [
        {
            "id": "saved-favorites",
            "kind": "feature",
            "title": "Избранное",
            "benefit": "Быстрее вернуться к выбору",
            "prompt": "Добавь сохранение избранного и проверь основной поток.",
        }
    ]
    assert second.status_code == 200
    assert second.json()["source"] == "cache"
    assert model_calls == 1
    assert read_commits == [cosmetic.commit_sha]
    assert redis.setex_calls[0][1] == 2_592_000


@pytest.mark.asyncio
async def test_long_cosmetic_history_still_analyzes_current_state(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session, "owner-long-history@example.com")
    project, _, parent = await _project_with_snapshots(db_session, owner)
    base_time = datetime.now(UTC) + timedelta(minutes=1)
    for index in range(55):
        snapshot = Snapshot(
            project_id=project.id,
            commit_sha=f"{index + 1:040x}",
            prompt_text=f"Поменяй цвет кнопки, вариант {index + 1}",
            parent_id=parent.id,
            created_at=base_time + timedelta(seconds=index),
        )
        db_session.add(snapshot)
        await db_session.flush()
        parent = snapshot
    project.current_snapshot_id = parent.id
    await db_session.commit()
    _login(client, owner)
    redis = FakeRedis()
    read_commits: list[str] = []

    async def generate(*_args, **_kwargs) -> ProductAdviceResult:
        return _ranked_result()

    def read_files(_project_id: UUID, commit_sha: str) -> dict[str, str]:
        read_commits.append(commit_sha)
        return {"src/app/page.tsx": "Каталог"}

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", read_files)

    response = await client.post(f"/api/projects/{project.id}/product-advice")

    assert response.status_code == 200
    assert response.json()["current_snapshot_id"] == str(parent.id)
    assert response.json()["analysis_snapshot_id"] == str(parent.id)
    assert read_commits == [parent.commit_sha]


@pytest.mark.asyncio
async def test_rollback_snapshot_is_used_as_current_product_state(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session, "owner-rollback@example.com")
    project, _, cosmetic = await _project_with_snapshots(db_session, owner)
    rollback = Snapshot(
        project_id=project.id,
        commit_sha="d" * 40,
        prompt_text="Восстановление версии",
        parent_id=cosmetic.id,
        created_at=datetime.now(UTC) + timedelta(seconds=2),
    )
    db_session.add(rollback)
    await db_session.flush()
    project.current_snapshot_id = rollback.id
    await db_session.commit()
    _login(client, owner)
    redis = FakeRedis()
    read_commits: list[str] = []

    async def generate(*_args, **_kwargs) -> ProductAdviceResult:
        return _ranked_result()

    def read_files(_project_id: UUID, commit_sha: str) -> dict[str, str]:
        read_commits.append(commit_sha)
        return {"src/app/page.tsx": "Восстановленный каталог"}

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", read_files)

    response = await client.post(f"/api/projects/{project.id}/product-advice")

    assert response.status_code == 200
    assert response.json()["current_snapshot_id"] == str(rollback.id)
    assert response.json()["analysis_snapshot_id"] == str(rollback.id)
    assert read_commits == [rollback.commit_sha]


@pytest.mark.asyncio
async def test_redis_failure_does_not_block_model_advice(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session, "owner-fallback@example.com")
    project, material, _ = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis(fail_get=True, fail_set=True)

    async def generate(*_args, **_kwargs) -> ProductAdviceResult:
        result = _ranked_result()
        return ProductAdviceResult(
            archetype=result.archetype,
            items=result.items,
            source="model",
        )

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(
        advice_router.repo,
        "read_files",
        lambda _project_id, commit_sha: (
            {"src/app/page.tsx": "Каталог"} if commit_sha == material.commit_sha else {}
        ),
    )

    response = await client.post(f"/api/projects/{project.id}/product-advice")

    assert response.status_code == 200
    assert response.json()["source"] == "model"
    assert len(response.json()["items"]) == 1


@pytest.mark.asyncio
async def test_cached_payload_is_validated_before_return(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session, "owner-invalid-cache@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis()

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_args: {})

    async def generate(*_args, **_kwargs) -> ProductAdviceResult:
        return _ranked_result()

    monkeypatch.setattr(advice_router, "generate_product_advice", generate)

    first = await client.post(f"/api/projects/{project.id}/product-advice")
    assert first.status_code == 200
    key = next(iter(redis.values))
    redis.values[key] = json.dumps({"items": [{"id": "unsafe", "prompt": ""}]})
    response = await client.post(f"/api/projects/{project.id}/product-advice")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "saved-favorites"
    assert response.json()["source"] == "model"


@pytest.mark.asyncio
async def test_abandoned_future_branch_is_excluded_from_advice_context(
    client,
    db_session,
    monkeypatch,
) -> None:
    owner = await _user(db_session, "owner-branch@example.com")
    project, material, current = await _project_with_snapshots(db_session, owner)
    db_session.add(
        Snapshot(
            project_id=project.id,
            commit_sha="f" * 40,
            prompt_text="Добавь казино на заброшенной ветке",
            parent_id=material.id,
            created_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await db_session.commit()
    _login(client, owner)
    contexts = []

    async def generate(context):
        contexts.append(context)
        return _ranked_result()

    monkeypatch.setattr(advice_router, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(
        advice_router.repo,
        "read_files",
        lambda *_: {"src/app/page.tsx": "<h1>Текущий каталог кофе</h1>"},
    )
    response = await client.post(f"/api/projects/{project.id}/product-advice")
    assert response.status_code == 200
    assert response.json()["analysis_snapshot_id"] == str(current.id)
    assert contexts[0].initial_brief == "Добавь каталог кофе и историю заказов"
    assert "Поменяй цвет заголовка" in contexts[0].recent_changes
    assert "казино" not in repr(contexts[0])


@pytest.mark.asyncio
async def test_configuration_change_invalidates_advice_without_snapshot_change(
    client,
    db_session,
    monkeypatch,
) -> None:
    owner = await _user(db_session, "owner-config@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    config = MaxProjectConfig(
        project_id=project.id,
        owner_id=owner.id,
        config_version=1,
        config={"app_name": "Кофе рядом", "app_type": "catalog", "summary": "Зерно для дома"},
    )
    db_session.add(config)
    await db_session.commit()
    _login(client, owner)
    redis = FakeRedis()
    contexts = []

    async def generate(context):
        contexts.append(context)
        return _ranked_result()

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_: {})
    url = f"/api/projects/{project.id}/product-advice"
    first = await client.post(url)
    config.config = {**config.config, "summary": "Доставка кофе в офисы"}
    config.config_version = 2
    await db_session.commit()
    second = await client.post(url)
    third = await client.post(url)
    assert first.status_code == second.status_code == third.status_code == 200
    assert second.json()["source"] == "model"
    assert third.json()["source"] == "cache"
    assert len(contexts) == 2
    assert "Зерно для дома" in contexts[0].product_config
    assert "Доставка кофе в офисы" in contexts[1].product_config


@pytest.mark.asyncio
async def test_model_outage_returns_retryable_error_and_does_not_cache(
    client,
    db_session,
    monkeypatch,
) -> None:
    from omnia_api.services import llm_client

    owner = await _user(db_session, "owner-unavailable@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis()
    attempts = 0

    async def complete(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("private provider error")
        return '{"items":[]}'

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(llm_client, "complete_chat", complete)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_: {})
    url = f"/api/projects/{project.id}/product-advice"
    failed = await client.post(url)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "advice_unavailable"
    assert "private provider" not in failed.text
    assert redis.values == {}
    retried = await client.post(url)
    assert retried.status_code == 200
    assert retried.json()["items"] == []


@pytest.mark.asyncio
async def test_simultaneous_requests_share_one_analysis(client, db_session, monkeypatch) -> None:
    owner = await _user(db_session, "owner-concurrent@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def generate(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _ranked_result()

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_: {})
    url = f"/api/projects/{project.id}/product-advice"
    first = asyncio.create_task(client.post(url))
    await started.wait()
    second = asyncio.create_task(client.post(url))
    # Allow the second request to finish its database reads while the first
    # request is deliberately held at the external model boundary.
    await asyncio.sleep(0.1)
    release.set()
    responses = await asyncio.gather(first, second)
    assert [response.status_code for response in responses] == [200, 200]
    assert calls == 1


@pytest.mark.asyncio
async def test_discovery_brief_change_invalidates_cached_advice(client, db_session, monkeypatch):
    owner = await _user(db_session, "owner-brief@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis()
    contexts = []

    async def generate(context):
        contexts.append(context)
        return _ranked_result()

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_: {})
    url = f"/api/projects/{project.id}/product-advice"
    assert (await client.post(url)).status_code == 200
    project.discovery_spec = {"brief": "Кофе для мероприятий", "audience": "организаторы"}
    await db_session.commit()
    response = await client.post(url)
    assert response.status_code == 200
    assert response.json()["source"] == "model"
    assert len(contexts) == 2
    assert "Кофе для мероприятий" in contexts[1].discovery


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "run_status", ["pending", "queued_for_capacity", "running", "cancel_requested"]
)
async def test_active_generation_blocks_analysis_until_terminal(
    client,
    db_session,
    monkeypatch,
    run_status,
):
    owner = await _user(db_session, "owner-busy@example.com")
    project, _, _ = await _project_with_snapshots(db_session, owner)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="busy-test",
        prompt_hash="a" * 64,
        status=run_status,
    )
    db_session.add(run)
    await db_session.commit()
    _login(client, owner)
    redis = FakeRedis()
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        return _ranked_result()

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_: {})
    url = f"/api/projects/{project.id}/product-advice"
    busy = await client.post(url)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "conflict"
    assert calls == 0
    run.status = "cancelled"
    await db_session.commit()
    ready = await client.post(url)
    assert ready.status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_new_snapshot_with_same_commit_does_not_join_previous_analysis(
    client,
    db_session,
    monkeypatch,
):
    owner = await _user(db_session, "owner-same-commit@example.com")
    project, _, current = await _project_with_snapshots(db_session, owner)
    _login(client, owner)
    redis = FakeRedis()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _ranked_result()

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router, "generate_product_advice", generate)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_: {})
    url = f"/api/projects/{project.id}/product-advice"
    first = asyncio.create_task(client.post(url))
    await started.wait()
    restored = Snapshot(
        project_id=project.id,
        commit_sha=current.commit_sha,
        parent_id=current.id,
        prompt_text="Восстановление версии",
    )
    db_session.add(restored)
    await db_session.flush()
    project.current_snapshot_id = restored.id
    await db_session.commit()
    second = asyncio.create_task(client.post(url))
    await asyncio.sleep(0.1)
    release.set()
    results = await asyncio.gather(first, second)
    assert results[1].json()["analysis_snapshot_id"] == str(restored.id)
    assert calls == 2
