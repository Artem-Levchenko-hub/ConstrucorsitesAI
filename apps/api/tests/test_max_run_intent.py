"""«Как запустить?» in a MAX Mini App chat must reach the builder.

The run/install shortcut (owner 2026-06-19) answers such follow-ups with a
downloadable installer card — right for a generated desktop/CLI program, wrong
for a MAX Mini App, which has no installer and is launched inside MAX. The
shortcut stays as it was for every other template.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services.generation import acceptance

pytestmark = pytest.mark.asyncio


class _NoopRedis:
    async def publish(self, *_args: object, **_kwargs: object) -> int:
        return 0


async def _built_project(session: AsyncSession, template: str) -> tuple[User, Project]:
    owner = User(email=f"run-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Запись клиентов",
        slug=f"zapis-{uuid.uuid4().hex[:8]}",
        template=template,
    )
    session.add(project)
    await session.flush()
    built = Snapshot(project_id=project.id, commit_sha="a" * 40, prompt_text="запись клиентов")
    session.add(built)
    await session.flush()
    project.current_snapshot_id = built.id
    await session.commit()
    return owner, project


async def _ask(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    owner: User,
    project: Project,
    prompt: str,
) -> tuple[httpx.Response, list[dict[str, object]], list[str]]:
    builds: list[dict[str, object]] = []
    texts: list[str] = []
    settings = SimpleNamespace(
        unlimited_generations=True,
        force_model=None,
        use_progressive_discovery=False,
        use_clarify_interview=False,
        use_auto_stack_routing=False,
        use_followup_appification=False,
        use_result_type_router=False,
        use_generation_worker=False,
        use_surgical_edit=True,
    )

    async def current_user() -> User:
        return owner

    def spawn_text(
        _project_id: uuid.UUID, _message_id: uuid.UUID, text: str, *, run_id: uuid.UUID
    ) -> None:
        texts.append(text)

    app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    monkeypatch.setattr(acceptance, "_spawn_process_prompt", lambda **kw: builds.append(kw))
    monkeypatch.setattr(acceptance, "_spawn_text_turn", spawn_text)
    monkeypatch.setattr(acceptance, "get_redis", lambda: _NoopRedis())
    try:
        response = await client.post(
            f"/api/projects/{project.id}/prompt",
            json={"prompt": prompt, "idempotency_key": uuid.uuid4().hex},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    return response, builds, texts


@pytest.mark.parametrize(
    "prompt", ["как запустить приложение в MAX?", "хочу запустить", "запусти бота"]
)
async def test_max_project_never_gets_the_installer_card(client, db_session, monkeypatch, prompt):
    owner, project = await _built_project(db_session, "max_miniapp")

    response, builds, texts = await _ask(client, monkeypatch, owner, project, prompt)

    assert response.status_code == 202, response.text
    assert not [text for text in texts if "install-bundle" in text or "установщик" in text]
    assert response.json()["mode"] != "clarify" or not texts
    assert len(builds) == 1, "the request must reach the builder"


async def test_other_templates_keep_the_installer_shortcut(client, db_session, monkeypatch):
    owner, project = await _built_project(db_session, "code")

    response, builds, texts = await _ask(client, monkeypatch, owner, project, "как запустить")

    assert response.status_code == 202, response.text
    assert builds == []
    assert len(texts) == 1 and "<install-bundle></install-bundle>" in texts[0]
