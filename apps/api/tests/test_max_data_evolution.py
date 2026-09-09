"""Policy delivery at prompt boundaries; not proof of generated SQL safety."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.services import agent_builder, agent_native, autoheal, prompt_builder

POLICY_HEADER = "MAX DATA EVOLUTION POLICY v1"


@pytest.mark.parametrize("model_id", [None, "claude-sonnet-5", "gpt-4.1-mini"])
@pytest.mark.parametrize("language", ["ru", "en"])
def test_max_code_writer_receives_policy_despite_model_tier_and_language(model_id, language):
    prompt = prompt_builder.build_system_prompt(
        "max_miniapp", model_id=model_id, language=language,
    )
    assert prompt.count(POLICY_HEADER) == 1
    if language == "en":
        assert prompt.startswith("ЯЗЫК ПРОЕКТА: en.")


@pytest.mark.parametrize("template", ["blank", "fullstack", "realtime", "spa"])
def test_other_project_writers_do_not_receive_max_database_instructions(template):
    assert POLICY_HEADER not in prompt_builder.build_system_prompt(template)


def test_prose_only_art_director_does_not_receive_database_execution_policy():
    prompt = prompt_builder.build_system_prompt("max_miniapp", brief_only=True)
    assert POLICY_HEADER not in prompt


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("template", ["max_miniapp", "fullstack"])
def test_edit_policy_follows_project_type_even_with_imported_source(imported, template):
    messages = prompt_builder._build_edit_messages(
        current_files={"src/app/page.tsx": "export default function Page() {}"},
        history=[],
        user_prompt="Rename surname to Family name; keep the saved customers",
        selected_elements=None,
        template=template,
        is_imported=imported,
        language="en",
    )
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].count(POLICY_HEADER) == (template == "max_miniapp")
    assert messages[0]["content"].startswith("ЯЗЫК ПРОЕКТА: en.")


@pytest.mark.parametrize("mode", ["build", "edit", "native"])
@pytest.mark.parametrize("provider", ["legacy", "cell-legacy", "portable", "missing-manifest"])
async def test_agent_policy_survives_provider_replacement_and_all_prompt_protocols(mode, provider):
    from omnia_api.services.max_data_evolution import build_max_agent_guide

    legacy = "MAX PLATFORM CORE CONTRACT\nLEGACY-ONLY-INSTRUCTIONS"
    snapshot = AsyncMock(return_value=(
        {} if provider == "missing-manifest" else {".omnia/cell.json": "{}"}
    ))
    executor = None if provider == "legacy" else SimpleNamespace(
        capabilities={"portable_machine": provider != "cell-legacy"}, snapshot_files=snapshot,
    )
    guide = await build_max_agent_guide(legacy, executor)
    builders = {
        "build": agent_builder.build_system_prompt,
        "edit": agent_builder.build_edit_system_prompt,
        "native": agent_native.native_system_prompt,
    }
    prompt = builders[mode](guide)
    assert prompt.count(POLICY_HEADER) == 1
    assert ("LEGACY-ONLY-INSTRUCTIONS" in prompt) == (provider != "portable")
    assert ("EXTENSIBLE MAIN STACK" in prompt) == (provider == "portable")
    assert snapshot.await_count == (provider != "legacy")
    if mode == "native":
        assert "MAX VERIFICATION OVERRIDE" in prompt


@pytest.mark.parametrize("template", ["max_miniapp", "fullstack"])
def test_failed_exact_edit_rewrite_retains_max_policy(template):
    messages = prompt_builder.build_container_rewrite_messages(
        {"src/app/page.tsx": "export default function Page() {}"},
        [], "Fix the customers form", None, template=template,
    )
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].count(POLICY_HEADER) == (template == "max_miniapp")
    if template == "fullstack":
        assert messages[0]["content"] == prompt_builder.build_container_rewrite_messages(
            {}, [], "Fix form", None,
        )[0]["content"]


@pytest.mark.parametrize("template", ["max_miniapp", "fullstack"])
async def test_autoheal_sends_project_policy_to_actual_agent_boundary(monkeypatch, template):
    monkeypatch.setattr(autoheal, "get_settings", lambda: SimpleNamespace(
        use_autoheal_on_open=True, autoheal_debounce_seconds=300,
    ))
    monkeypatch.setattr(autoheal, "get_redis", lambda: SimpleNamespace(
        set=AsyncMock(return_value=True),
    ))
    monkeypatch.setattr(autoheal.orchestrator_client, "compile_status", AsyncMock(
        side_effect=[{"ok": False, "error": "type mismatch"}, {"ok": True}],
    ))
    captured = []

    async def run(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(files={"src/app/page.tsx": "fixed"})

    monkeypatch.setattr(autoheal.agent_builder, "run_agent_build", run)
    result = await autoheal.maybe_autoheal_on_open(uuid4(), "fixture", template=template)
    assert result == {"healed": True, "files": 1}
    assert len(captured) == 1
    assert captured[0]["system_prompt"].count(POLICY_HEADER) == (template == "max_miniapp")
    if template == "fullstack":
        assert captured[0]["system_prompt"] == agent_builder.EDIT_SYSTEM_PROMPT
    else:
        assert "Use `window.WebApp` only" in captured[0]["system_prompt"]


async def test_disabled_autoheal_does_not_load_guide_or_call_model(monkeypatch):
    monkeypatch.setattr(autoheal, "get_settings", lambda: SimpleNamespace(
        use_autoheal_on_open=False,
    ))
    run = AsyncMock(side_effect=AssertionError("disabled autoheal must not spend tokens"))
    monkeypatch.setattr(autoheal.agent_builder, "run_agent_build", run)
    result = await autoheal.maybe_autoheal_on_open(uuid4(), "fixture", template="max_miniapp")
    assert result == {"healed": False, "reason": "disabled"}
    run.assert_not_called()
