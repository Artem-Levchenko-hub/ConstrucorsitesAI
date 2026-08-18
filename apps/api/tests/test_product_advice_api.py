from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.security import create_access_token
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
async def test_cosmetic_snapshot_reuses_material_advice_cache(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(db_session, "owner-cache@example.com")
    project, material, cosmetic = await _project_with_snapshots(db_session, owner)
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
    assert first.json()["analysis_snapshot_id"] == str(material.id)
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
    assert read_commits == [material.commit_sha]
    assert redis.setex_calls[0][1] == 2_592_000


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
async def test_redis_failure_does_not_block_fallback_advice(
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
            source="fallback",
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
    assert response.json()["source"] == "fallback"
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
    redis.values[advice_router.product_advice_cache_key(project.id, "b" * 40)] = json.dumps(
        {"items": [{"id": "unsafe", "prompt": ""}]}
    )

    monkeypatch.setattr(advice_router, "get_redis", lambda: redis)
    monkeypatch.setattr(advice_router.repo, "read_files", lambda *_args: {})

    async def generate(*_args, **_kwargs) -> ProductAdviceResult:
        return _ranked_result()

    monkeypatch.setattr(advice_router, "generate_product_advice", generate)

    response = await client.post(f"/api/projects/{project.id}/product-advice")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "saved-favorites"
