"""Policy delivery at prompt boundaries; not proof of generated SQL safety."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.services import agent_builder, agent_native, autoheal

POLICY_HEADER = "MAX DATA EVOLUTION POLICY v1"


@pytest.mark.parametrize("stale", [{}, {"database_admin": "protected", "secure_data_crud": True}])
def test_project_database_guide_grants_ordinary_development_admin_access(stale):
    from omnia_api.services.portable_cell_contract import machine_stack_guide

    guide = machine_stack_guide(
        "legacy", {"portable_machine": True, **stale}, {".omnia/cell.json": "{}"},
    )
    assert "development admin access" in guide
    assert "Manage your own schema, migrations" in guide
    assert "omnia-db" not in guide
    assert "data-contract.json" not in guide
    assert "secureCollection" not in guide
    assert "MAX DATA EVOLUTION POLICY" in guide


def test_shared_evolution_guidance_has_no_protected_controller_command():
    from omnia_api.services.max_data_evolution import MAX_DATA_EVOLUTION_POLICY

    assert "omnia-db" not in MAX_DATA_EVOLUTION_POLICY
    assert "protected database" not in MAX_DATA_EVOLUTION_POLICY
    assert "service startup" in MAX_DATA_EVOLUTION_POLICY


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
