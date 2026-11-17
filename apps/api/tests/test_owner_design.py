from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime
from yleum_api.services.owner_design import owner_palette_context, record_owner_palette


def _request():
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    run = SimpleNamespace(
        id=ids.run_id, project_id=ids.project_id, user_id=ids.user_id,
        user_message_id=ids.user_message_id, prompt_hash="a" * 64,
        agent_state={"dispatch": {"original": "unchanged"}},
    )
    return ids, run


def test_only_explicit_owner_palette_is_rendered_and_original_stays_unchanged():
    ids, run = _request()
    assert owner_palette_context(run, ids) == ""
    record_owner_palette(run, "festival-brutalist")
    context = owner_palette_context(run, ids)
    assert "Владелец явно выбрал палитру" in context
    assert "выбор цветов, не архитектуры" in context
    assert "Прямые значения и ограничения" in context
    assert "Lora" not in context and "hero" not in context
    from yleum_api.services.typography import existing_typography_context

    preserved = existing_typography_context(
        "Source context" + context + "\n\nOMNIA DESIGN PRO · automatic candidate",
        {"DESIGN.md": "Existing owner design"},
    )
    assert "явно выбрал палитру" in preserved
    assert "automatic candidate" not in preserved
    assert run.prompt_hash == "a" * 64
    assert run.agent_state["dispatch"] == {"original": "unchanged"}
    record_owner_palette(run, "festival-brutalist")
    with pytest.raises(ValueError, match="changed within one request"):
        record_owner_palette(run, "wellness-casual")


@pytest.mark.parametrize("field", ["run_id", "project_id", "user_id", "user_message_id"])
def test_palette_cannot_cross_request_or_owner_boundary(field):
    ids, run = _request()
    record_owner_palette(run, "festival-brutalist")
    assert owner_palette_context(run, replace(ids, **{field: uuid4()})) == ""


@pytest.mark.parametrize("patch", [
    {"version": True}, {"preset_id": "unknown"}, {"prompt_hash": "b" * 64},
    {"extra": "untrusted instructions"},
])
def test_invalid_or_stale_preferences_do_not_become_instructions(patch):
    ids, run = _request()
    record_owner_palette(run, "festival-brutalist")
    run.agent_state["owner_palette_v1"].update(patch)
    assert owner_palette_context(run, ids) == ""


def test_unknown_selection_is_ignored_without_altering_other_state():
    _, run = _request()
    record_owner_palette(run, "unknown")
    assert run.agent_state == {"dispatch": {"original": "unchanged"}}


@pytest.mark.asyncio
async def test_admission_preserves_explicit_selection_without_rewriting_prompt(monkeypatch):
    from yleum_api.schemas.message import PromptRequest
    from yleum_api.services.generation import acceptance
    from yleum_api.services.generation.acceptance import PromptAcceptance

    monkeypatch.setattr(acceptance, "get_settings", lambda: SimpleNamespace())

    ids, run = _request()
    project = SimpleNamespace(design_preset_id="retail-product")
    request = PromptAcceptance(
        project_id=ids.project_id, payload=PromptRequest(
            prompt="Один экран чтения, без корзины", design_preset_id="festival-brutalist",
        ), session=None, current_user=SimpleNamespace(id=ids.user_id), project=project,
    )
    request.generation_run = run
    await request.route_existing_project()
    assert project.design_preset_id == "festival-brutalist"
    assert "явно выбрал палитру" in owner_palette_context(run, ids)
    assert request.payload.prompt == "Один экран чтения, без корзины"
    request.readonly_source = True
    request.payload = PromptRequest(prompt="Только прочитай", design_preset_id="wellness-casual")
    await request.route_existing_project()
    assert project.design_preset_id == "festival-brutalist"


@pytest.mark.asyncio
async def test_explicit_palette_survives_real_admission_commit_and_replay(
    client, db_session, test_engine, monkeypatch,
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_runs import load_generation_dispatch
    from yleum_api.services.owner_design import load_owner_palette_context

    from .test_prompt_admission_lock import _prompt_fixture

    owner, project, spawned = await _prompt_fixture(db_session, monkeypatch)
    payload = {
        "prompt": "Один экран чтения. Без корзины и вкладок.",
        "skip_clarify": True, "idempotency_key": "explicit-palette-replay",
        "design_preset_id": "festival-brutalist",
    }
    first = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    assert first.status_code == replay.status_code == 202
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert len(spawned) == 1
    from uuid import UUID

    run_id = UUID(first.json()["run_id"])
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        assert run is not None
        dispatch = load_generation_dispatch(run)
        assert dispatch.prompt_text == payload["prompt"]
        ids = GenerationIds(run_id, project.id, owner.id,
                            run.user_message_id, run.assistant_message_id)
    assert "явно выбрал палитру" in await load_owner_palette_context(factory, ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["max_miniapp", "fullstack"])
async def test_max_plugin_absence_never_revives_legacy_design_mood(monkeypatch, template):
    from yleum_api.services import design_dna, integration_generation, max_data_evolution
    from yleum_api.services.generation import agent_preparation

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return None

    mood = Mock(return_value="LEGACY DESIGN MOOD")
    monkeypatch.setattr(design_dna, "design_mood_directive", mood)
    monkeypatch.setattr(agent_preparation, "_seed_saved_max_config", AsyncMock(return_value=""))
    monkeypatch.setattr(agent_preparation, "get_settings", lambda: SimpleNamespace(
        use_design_mood=True, use_skill_injection=False,
    ))
    monkeypatch.setattr(integration_generation, "generation_context", AsyncMock(return_value=""))

    async def guide(text, *args, **kwargs):
        return text

    monkeypatch.setattr(max_data_evolution, "build_max_agent_guide", guide)
    stack = await agent_preparation.prepare_stack_prompt(
        _design_contract=None, factory=Session,
        ids=GenerationIds(*(uuid4() for _ in range(5))), orchestrate=True,
        project_info=SimpleNamespace(
            template=template, memory_context="", restoration_context="",
        ), prompt_text="Один экран без вкладок и корзины", runtime=GenerationRuntime(),
    )
    if template == "max_miniapp":
        mood.assert_not_called()
        assert "LEGACY DESIGN MOOD" not in stack.seed_context
    else:
        mood.assert_called_once()
        assert "LEGACY DESIGN MOOD" in stack.seed_context
