"""Draft extraction, owner approval, legacy settings and public document visibility."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError

from yleum_api.core.errors import ApiError
from yleum_api.routers import max_studio
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services import max_content_delivery, max_privacy_document, repo

SCHEMA = b"""
import { pgTable as table, text } from 'drizzle-orm/pg-core';
export const profiles = table('profiles', {
  userId: text('user_id'), foodPreferences: text('food_preferences'), water_ml: text('water_ml')
});
export const recipes = table('recipes', {email: text('email'), calories: text('calories')});
export const starter = table('max_users', {maxUserId: text('uid'), phone: text('number')});
function unrelated(table) { const fake = table('foo', {userId: 1, allergies: 1}); }
"""


def config(**fields):
    return MaxProjectConfigPayload.model_validate(
        {
            "app_name": "Рацион",
            "app_type": "custom",
            "summary": "Просьба собирать адрес",
            **fields,
        }
    )


def active(project_id):
    return {
        "project_id": str(project_id),
        "phase": "failed",
        "active_publication": {
            "release_id": str(uuid4()),
            "snapshot_id": str(uuid4()),
            "commit_sha": "a" * 40,
            "prod_url": "https://example.test",
        },
    }


def test_draft_uses_config_and_saved_user_schema_only():
    draft = max_privacy_document.build_privacy_draft(
        config(operator={"legal_name": "Студия питания"}, support={"email": "team@example.org"}),
        SCHEMA,
    )
    assert all(
        fact in draft
        for fact in (
            "Рацион",
            "Студия питания",
            "team@example.org",
            "идентификатор пользователя",
            "пищевые предпочтения",
            "количество выпитой воды",
        )
    )
    assert all(
        word not in draft
        for word in (
            "рецепт",
            "калорийность",
            "электронная почта",
            "номер телефона",
            "аллергии",
            "собирать адрес",
        )
    )
    generic = max_privacy_document.build_privacy_draft(config(), b"")
    assert "личных записях" not in generic
    assert "оплат" not in generic


def test_policy_approval_legacy_clients_and_public_projection(monkeypatch):
    project_id = uuid4()
    monkeypatch.setattr(
        max_content_delivery,
        "get_settings",
        lambda: SimpleNamespace(web_base_url="https://studio.test/"),
    )
    legacy, current = config(), config(legal={"policy_text": "  Текст владельца  "})
    assert "policy_text" not in legacy.model_dump(mode="json")["legal"]
    retained = max_studio._prepare_config_change(
        current,
        legacy,
        current_version=2,
        expected_version=1,
    )
    assert retained.legal.policy_text == "Текст владельца"
    pub = max_content_delivery.public_business_config(retained, project_id)
    assert (
        pub["legal"]["policy_url"]
        == f"https://studio.test/api/projects/{project_id}/max/privacy-policy"
    )
    assert "policy_text" not in pub["legal"]
    external = config(legal={"policy_url": "https://outside.test/policy", "policy_text": "Текст"})
    outgoing = max_content_delivery.public_business_config(external, project_id)["legal"]
    assert outgoing["policy_url"] == "https://outside.test/policy" and "policy_text" not in outgoing
    clear = config(legal={"policy_text": ""})
    with pytest.raises(ApiError, match="Данные изменились"):
        max_studio._prepare_config_change(current, clear, current_version=2, expected_version=1)
    cleared = max_studio._prepare_config_change(
        current, clear, current_version=2, expected_version=2
    )
    assert not max_content_delivery.public_business_config(cleared, project_id)["legal"][
        "policy_url"
    ]
    with pytest.raises(ValidationError):
        config(legal={"policy_text": "a" * 20_001})


@pytest.mark.asyncio
async def test_draft_reads_owned_accepted_snapshot_without_writes(monkeypatch):
    project_id, owner = uuid4(), SimpleNamespace(id=uuid4())
    project = SimpleNamespace(id=project_id, current_snapshot_id=uuid4(), name="Приложение")
    owned = AsyncMock(return_value=project)
    monkeypatch.setattr(max_studio, "_owned_max_project", owned)
    accepted = AsyncMock(return_value=None)
    monkeypatch.setattr(max_studio, "current_accepted_version", accepted)
    read = Mock(return_value=SCHEMA)
    monkeypatch.setattr(repo, "read_file", read)
    session = AsyncMock()
    with pytest.raises(ApiError) as error:
        await max_studio.get_max_privacy_draft(project_id, session, owner, Response())
    assert error.value.status_code == 409
    read.assert_not_called()
    accepted.return_value = SimpleNamespace()
    resolve = AsyncMock(return_value=("ready", SimpleNamespace(project_id=uuid4())))
    monkeypatch.setattr(max_studio, "resolve_version", resolve)
    with pytest.raises(ApiError) as error:
        await max_studio.get_max_privacy_draft(project_id, session, owner, Response())
    assert error.value.status_code == 409
    read.assert_not_called()
    snapshot = SimpleNamespace(project_id=project_id, commit_sha="1" * 40)
    record = SimpleNamespace(owner_id=owner.id, config=config().model_dump(mode="json"))
    resolve.return_value = ("ready", snapshot)
    session.get.return_value = record
    response = Response()
    draft = await max_studio.get_max_privacy_draft(project_id, session, owner, response)
    owned.assert_awaited_with(session, project_id, owner.id)
    read.assert_called_once_with(
        project_id,
        snapshot.commit_sha,
        "src/lib/db/schema.ts",
        max_bytes=repo.MAX_FILE_BYTES,
    )
    assert "пищевые предпочтения" in draft.text
    assert "no-store" in response.headers["cache-control"]
    session.commit.assert_not_awaited()
    read.side_effect = OSError("storage offline")
    with pytest.raises(ApiError) as error:
        await max_studio.get_max_privacy_draft(project_id, session, owner, Response())
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_policy_document_visibility_and_escaped_owner_text(monkeypatch):
    project_id, owner, stranger = uuid4(), SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    project = SimpleNamespace(id=project_id, owner_id=owner.id, template="max_miniapp")
    record = SimpleNamespace(
        owner_id=owner.id,
        config=config(
            legal={
                "policy_text": "Политика\n\n<em> & «данные»",
            }
        ).model_dump(mode="json"),
    )
    deploy = AsyncMock()
    monkeypatch.setattr(max_studio.orchestrator_client, "get_deploy", deploy)

    async def get(user):
        session = AsyncMock()
        session.get.side_effect = [project, record]
        return await max_studio.get_max_privacy_policy(project_id, session, user)

    response = await get(owner)
    deploy.assert_not_awaited()

    assert "&lt;em&gt; &amp;" in bytes(response.body).decode()
    assert "sandbox" in response.headers["content-security-policy"]
    assert "no-store" in response.headers["cache-control"]
    for payload in [
        {"project_id": str(project_id), "phase": "done", "prod_url": "https://stale.test"},
        {**active(project_id), "active_publication": None},
        active(uuid4()),
        {**active(project_id), "active_publication": {"prod_url": "https://stale.test"}},
    ]:
        deploy.return_value = payload
        for visitor in [None, stranger]:
            with pytest.raises(ApiError) as error:
                await get(visitor)
            assert error.value.status_code == 404
    deploy.return_value = active(project_id)
    assert (await get(None)).body == response.body
    deploy.reset_mock()
    record.config["legal"]["policy_url"] = "https://outside.test/policy"
    with pytest.raises(ApiError) as error:
        await get(None)
    assert error.value.status_code == 404
    deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_site_draft_uses_accepted_ancestor_and_approval_is_private(
    client,
    db_session,
    monkeypatch,
):
    from tests.session_authority import authenticated_session_token
    from yleum_api.models.max_project_config import MaxProjectConfig
    from yleum_api.models.project import Project
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.models.user import User
    from yleum_api.services.project_versions import record_manual_version

    owner = User(email="privacy-owner@example.test")
    other = User(email="privacy-other@example.test")
    db_session.add_all([owner, other])
    await db_session.flush()
    project = Project(owner_id=owner.id, name="Рацион", slug="nutrition", template="max_miniapp")
    db_session.add(project)
    await db_session.flush()
    project_id, owner_id = project.id, owner.id
    owner_headers = {
        "Authorization": f"Bearer {await authenticated_session_token(db_session, owner)}"
    }
    other_headers = {
        "Authorization": f"Bearer {await authenticated_session_token(db_session, other)}"
    }
    root = f"/api/projects/{project_id}/max"
    draft_url = f"{root}/privacy-draft"
    document = f"{root}/privacy-policy"
    assert (await client.get(draft_url, headers=owner_headers)).status_code == 409
    assert (await client.get(draft_url, headers=other_headers)).status_code == 404
    assert (await client.get(draft_url)).status_code == 401

    path = "src/lib/db/schema.ts"
    commit = repo.init_from_files(project_id, {path: SCHEMA.decode()}, "Reviewed build")
    accepted = Snapshot(project_id=project_id, commit_sha=commit)
    db_session.add(accepted)
    await db_session.flush()
    await record_manual_version(db_session, project, accepted, label="Reviewed build")
    later = repo.commit_files(
        project_id,
        {path: SCHEMA.decode().replace("foodPreferences", "allergies")},
        "Working draft",
        parent_sha=commit,
    )
    head = Snapshot(project_id=project_id, commit_sha=later, parent_id=accepted.id)
    db_session.add(head)
    await db_session.flush()
    project.current_snapshot_id = head.id
    await db_session.commit()

    response = await client.get(draft_url, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"text"}
    assert "пищевые предпочтения" in response.json()["text"]
    assert "аллергии" not in response.json()["text"]
    assert (await client.get(document, headers=owner_headers)).status_code == 404

    approved = config(legal={"policy_text": "Проверенный текст\n\nНовая строка"})
    db_session.add(
        MaxProjectConfig(
            project_id=project_id,
            owner_id=owner_id,
            config=approved.model_dump(mode="json"),
        )
    )
    await db_session.commit()
    assert "Проверенный текст" in (await client.get(document, headers=owner_headers)).text
    deploy = AsyncMock(return_value={"project_id": str(project_id), "active_publication": None})
    monkeypatch.setattr(max_studio.orchestrator_client, "get_deploy", deploy)
    assert (await client.get(document)).status_code == 404
    deploy.return_value = active(project_id)
    assert "Проверенный текст" in (await client.get(document)).text
