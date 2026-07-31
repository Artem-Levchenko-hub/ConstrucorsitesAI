from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import max_studio
from omnia_api.schemas.max_studio import (
    MaxContentItem,
    MaxProjectConfigPayload,
    MaxUrlAttachedPayload,
)
from omnia_api.services.max_project_kit import (
    MAX_MANAGED_KIT_VERSION,
    _template_candidates,
    render_max_managed_files,
)


def _config() -> MaxProjectConfigPayload:
    return MaxProjectConfigPayload(
        app_name='Кофе "Рядом"',
        app_type="loyalty",
        summary="Баллы, награды и предзаказ",
        primary_action="Заказать кофе",
        features=["Каталог", "Баллы", "Каталог"],
        content=[
            MaxContentItem(
                id="flat-white",
                title="Флэт уайт",
                description="Двойной эспрессо",
                price="290 ₽",
                action_label="Заказать",
            )
        ],
        operator={"legal_name": "ООО Кофе", "inn": "1234567890"},
        support={"email": "help@example.ru"},
        legal={"has_sales": True, "terms_accepted": True},
    )


def test_max_config_normalises_features() -> None:
    assert _config().features == ["Каталог", "Баллы"]


def test_managed_kit_contains_config_and_required_legal_routes() -> None:
    project_id = uuid4()
    files = render_max_managed_files(_config(), project_id)

    assert set(files) == {
        "src/lib/max/session.ts",
        "src/app/api/omnia/preview-session/route.ts",
        "src/lib/omnia/max-config.ts",
        "src/app/api/omnia/config/route.ts",
        "src/lib/omnia/integration-client.ts",
        "src/app/api/omnia/integrations/[...path]/route.ts",
        "src/app/legal/privacy/page.tsx",
        "src/app/legal/terms/page.tsx",
        "src/app/support/page.tsx",
    }
    config = files["src/lib/omnia/max-config.ts"]
    assert '"app_name": "Кофе \\"Рядом\\""' in config
    assert '"price": "290 ₽"' in config
    assert "as const" in config
    assert "max_url_attached" not in config
    assert str(project_id) in files["src/app/api/omnia/integrations/[...path]/route.ts"]
    preview_route = files["src/app/api/omnia/preview-session/route.ts"]
    assert 'process.env.NODE_ENV !== "development"' in preview_route
    assert "partitioned: true" in preview_route
    assert "PREVIEW_SESSION_MAX_AGE_SECONDS" in preview_route
    assert 'headers: { Location: "/" }' in preview_route
    assert 'NextResponse.redirect(new URL("/", request.url))' not in preview_route
    assert "options: { maxAge?: number } = {}" in files["src/lib/max/session.ts"]


def test_template_lookup_does_not_depend_on_repository_depth() -> None:
    candidates = _template_candidates(
        "missing.ts",
        Path("/app/src/omnia_api/services/max_project_kit.py"),
    )

    assert len(candidates) >= 2
    assert (
        candidates[0].as_posix().endswith("/orchestrator/templates/max-miniapp-nextjs/missing.ts")
    )


def test_managed_kit_never_contains_model_or_generation_calls() -> None:
    combined = "\n".join(render_max_managed_files(_config()).values()).lower()

    assert "openai" not in combined
    assert "llmgw" not in combined
    assert "/chat/completions" not in combined
    assert "generate(" not in combined


async def test_config_save_is_versioned_and_idempotent(db_session, monkeypatch) -> None:
    user = User(email=f"max-{uuid4()}@example.ru")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="Кофе Рядом",
        slug=f"max-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    initial = Snapshot(
        project_id=project.id,
        commit_sha="1" * 40,
        prompt_text="Собери приложение",
    )
    db_session.add(initial)
    await db_session.flush()
    project.current_snapshot_id = initial.id
    await db_session.commit()

    calls: list[dict[str, str]] = []

    def fake_commit(project_id, files, message, parent_sha):
        calls.append(
            {
                "project_id": str(project_id),
                "message": message,
                "parent_sha": parent_sha,
                "config": files["src/lib/omnia/max-config.ts"],
            }
        )
        return "2" * 40

    async def stopped(_project_id):
        return {"state": "stopped"}

    async def not_deployed(_project_id):
        return {"phase": "queued"}

    monkeypatch.setattr(max_studio.repo_svc, "commit_files", fake_commit)
    monkeypatch.setattr(max_studio.orchestrator_client, "get_status", stopped)
    monkeypatch.setattr(max_studio.orchestrator_client, "get_deploy", not_deployed)

    first = await max_studio.put_max_config(project.id, _config(), db_session, user)
    second = await max_studio.put_max_config(project.id, _config(), db_session, user)

    assert first.config_version == 1
    assert second.synced_snapshot_id == first.synced_snapshot_id
    assert len(calls) == 1
    assert calls[0]["parent_sha"] == "1" * 40
    saved = await db_session.get(MaxProjectConfig, project.id)
    assert saved is not None
    assert saved.config["app_name"] == 'Кофе "Рядом"'
    assert saved.managed_kit_version == MAX_MANAGED_KIT_VERSION
    assert project.current_snapshot_id == saved.synced_snapshot_id

    # A project carrying an older managed kit is upgraded once even when its
    # business config and current snapshot are otherwise unchanged.
    saved.managed_kit_version = MAX_MANAGED_KIT_VERSION - 1
    await db_session.commit()
    upgraded = await max_studio.put_max_config(project.id, _config(), db_session, user)
    repeated_after_upgrade = await max_studio.put_max_config(
        project.id, _config(), db_session, user
    )
    refreshed = await db_session.get(MaxProjectConfig, project.id)

    assert len(calls) == 2
    assert refreshed is not None
    assert refreshed.managed_kit_version == MAX_MANAGED_KIT_VERSION
    assert repeated_after_upgrade.synced_snapshot_id == upgraded.synced_snapshot_id


async def test_url_confirmation_is_persisted_without_new_snapshot(db_session) -> None:
    user = User(email=f"max-url-{uuid4()}@example.ru")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="MAX URL",
        slug=f"max-url-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    initial = Snapshot(
        project_id=project.id,
        commit_sha="3" * 40,
        prompt_text="Собери приложение",
    )
    db_session.add(initial)
    await db_session.flush()
    project.current_snapshot_id = initial.id
    await db_session.commit()

    before = int(
        (
            await db_session.execute(
                select(func.count(Snapshot.id)).where(Snapshot.project_id == project.id)
            )
        ).scalar_one()
    )
    saved = await max_studio.patch_max_url_attached(
        project.id,
        MaxUrlAttachedPayload(attached=True),
        db_session,
        user,
    )
    repeated = await max_studio.patch_max_url_attached(
        project.id,
        MaxUrlAttachedPayload(attached=True),
        db_session,
        user,
    )
    after = int(
        (
            await db_session.execute(
                select(func.count(Snapshot.id)).where(Snapshot.project_id == project.id)
            )
        ).scalar_one()
    )

    assert saved.config.max_url_attached is True
    assert saved.config_version == 1
    assert repeated.config_version == 1
    assert saved.synced_snapshot_id is None
    assert project.current_snapshot_id == initial.id
    assert before == after == 1
