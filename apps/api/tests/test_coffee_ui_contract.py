"""Platform selectors must reach the real task, not only operator configuration."""

from types import SimpleNamespace as NS

import pytest

from yleum_api.services.generation import agent_prompt

from .test_generation_source_check_repair import source_check as source_check

REQUEST = "Добавь кнопку «Проверить заявку»: показывай локальное резюме без отправки данных."
SELECTORS = {
    "button": '[data-omnia-behavior="coffee-summary-button"]',
    "form": '[data-omnia-behavior="coffee-request-form"]',
    "text_input": '[data-omnia-behavior="coffee-request-text"]',
    "summary": '[data-omnia-behavior="coffee-request-summary"]',
}


async def prompt(monkeypatch, mode, request=REQUEST, coordinator=None):
    monkeypatch.setattr(agent_prompt, "model_for_role", lambda *a, **k: "no-model-call")
    monkeypatch.setattr(agent_prompt, "get_settings", lambda: NS(agent_builder_max_steps=40))
    return await agent_prompt.prepare_agent_prompt(
        stack=NS(seed_context="", system="SYSTEM", guide="", skills=None, bare_stack=False),
        factory=None,
        ids=None,
        project_info=NS(template="max_miniapp", restoration_context=mode == "adaptation"),
        prompt_text=request,
        runtime=NS(handle=None, coordinator=coordinator),
        orchestrate=False,
        selected_elements=None,
        _is_edit=mode in {"edit", "adaptation"},
        _is_continue=mode == "continue",
        force_model=None,
    )


@pytest.mark.parametrize("mode", ["build", "edit", "continue", "adaptation"])
async def test_real_prompt_branches_supply_single_coffee_selector_contract(monkeypatch, mode):
    plan, _ = await prompt(monkeypatch, mode)
    for selector in SELECTORS.values():
        assert selector in plan.user
    assert plan.user.count("PLATFORM COFFEE UI CONTRACT v1") == 1
    assert 'type="button"' in plan.user
    assert "Проверить заявку" in plan.user
    assert "form values" in plan.user
    assert "do not send" in plan.user
    assert "No synthetic user records" in plan.user


@pytest.mark.parametrize(
    "prompt_text",
    [
        "API-only: local summary without UI.",
        "Inspect the Coffee source.",
        "Не добавляй кнопку Проверить заявку: резюме без отправки.",
        "Поменяй цвет обычного заголовка.",
    ],
)
async def test_unrelated_or_excluded_requests_do_not_acquire_coffee_interface(
    monkeypatch, prompt_text
):
    plan, _ = await prompt(monkeypatch, "edit", prompt_text)
    assert "PLATFORM COFFEE UI CONTRACT" not in plan.user
    assert "data-omnia-behavior" not in plan.user


async def test_synthesized_adaptation_uses_authoritative_frozen_request(monkeypatch):
    from unittest.mock import AsyncMock

    from yleum_api.services import max_behavior_proof as proof

    frozen = proof.required_contract(REQUEST, template="max_miniapp")
    freeze = AsyncMock(return_value=frozen)
    monkeypatch.setattr(proof, "freeze_for_turn", freeze)
    plan, _ = await prompt(monkeypatch, "adaptation", "Server adaptation context only.", NS())
    assert SELECTORS["button"] in plan.user
    assert plan.user.count("PLATFORM COFFEE UI CONTRACT v1") == 1
    freeze.assert_awaited_once()


async def test_synthesized_context_cannot_add_coffee_to_frozen_unrelated_request(monkeypatch):
    from unittest.mock import AsyncMock

    from yleum_api.services import max_behavior_proof as proof

    monkeypatch.setattr(proof, "freeze_for_turn", AsyncMock(return_value=None))
    plan, _ = await prompt(monkeypatch, "adaptation", REQUEST, NS())
    assert "PLATFORM COFFEE UI CONTRACT" not in plan.user
    assert "data-omnia-behavior" not in plan.user


def test_operator_preset_supports_only_shared_coffee_interface():
    from yleum_api.services.behavior_driver_configuration import _adapter

    adapter = _adapter({"preset": "coffee-local-summary-ui-v1"})
    assert adapter.density is None and adapter.theme is None
    assert adapter.coffee_summary is not None
    for field, selector in SELECTORS.items():
        assert getattr(adapter.coffee_summary, field) == selector


@pytest.mark.parametrize(
    "raw",
    [
        {"preset": "unknown"},
        {"preset": "coffee-local-summary-ui-v1", "density": {}},
        {"preset": "coffee-local-summary-ui-v1", "coffee_summary": SELECTORS},
    ],
)
def test_unknown_or_mixed_operator_preset_is_rejected(raw):
    from yleum_api.services.behavior_driver_configuration import _adapter

    with pytest.raises(ValueError):
        _adapter(raw)


async def test_real_freeze_recovers_original_message_before_guidance(monkeypatch):
    import hashlib
    from unittest.mock import AsyncMock
    from uuid import uuid4

    project_id = uuid4()
    run = NS(
        prompt_hash=hashlib.sha256(REQUEST.encode()).hexdigest(),
        user_message_id=uuid4(),
        agent_state={},
    )
    session = NS(
        get=AsyncMock(return_value=NS(project_id=project_id, role="user", content=REQUEST)),
        commit=AsyncMock(),
    )

    class Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return None

    coordinator = NS(
        project_id=project_id, session_factory=Context, _locked_run=AsyncMock(return_value=run)
    )
    plan, _ = await prompt(monkeypatch, "adaptation", "Synthesized server context.", coordinator)
    assert SELECTORS["button"] in plan.user
    assert run.agent_state["max_named_behavior_contract"]["request_sha256"] == run.prompt_hash
    session.commit.assert_awaited_once()


async def test_shared_plan_is_preserved_in_real_bounded_native_repair(monkeypatch, source_check):
    from dataclasses import replace

    from yleum_api.services.generation import agent_recovery

    kwargs, _workspace, calls, _rollback, _mode = source_check
    plan, _ = await prompt(monkeypatch, "edit")
    kwargs["plan"] = replace(plan, stack_guide="MAX PLATFORM CORE CONTRACT", steps=4)
    kwargs["prompt_text"] = REQUEST
    result = await agent_recovery.recover_rejected_candidate(**kwargs)
    assert not result.candidate_failed
    first_task = str(calls[0])
    for selector in SELECTORS.values():
        assert selector in first_task
    assert first_task.count("PLATFORM COFFEE UI CONTRACT v1") == 1


async def test_shared_plan_reaches_first_real_native_provider_task(monkeypatch):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.core.config import get_settings
    from yleum_api.services import agent_native
    from yleum_api.services.generation import agent_generation
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    settings = get_settings().model_copy(update={"use_native_agent": True})
    monkeypatch.setattr(agent_generation, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    plan, _ = await prompt(monkeypatch, "edit")
    baseline = {"src/app/page.tsx": "existing Coffee product"}
    workspace, tasks = dict(baseline), []

    async def provider(*args, **kwargs):
        tasks.append(args[2])
        return {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": str(len(tasks)),
                    "name": "done",
                    "input": {"summary": "claimed ready without writes"},
                }
            ],
        }

    async def execute(action):
        pytest.fail("unchanged done must never execute a tool")

    async def export():
        return dict(workspace)

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await agent_generation.execute_agent_turn(
            _agent_res=None,
            _is_edit=True,
            _max_has_generated_snapshot=True,
            _max_seed_files={},
            _max_shell_enabled=False,
            baseline=SourceBaseline(uuid4(), "baseline", baseline),
            ids=GenerationIds(*(uuid4() for _ in range(5))),
            is_free=False,
            project_info=NS(template="max_miniapp"),
            prompt_text=REQUEST,
            runtime=NS(
                handle=NS(is_portable=lambda: True, export_files=export, snapshot_files=export),
                coordinator=None,
            ),
            plan=plan,
            operations=NS(execute=execute, emit=AsyncMock()),
        )
    assert workspace == baseline and 1 <= len(tasks) <= 6
    first_task = str(tasks[0])
    for selector in SELECTORS.values():
        assert selector in first_task
    assert first_task.count("PLATFORM COFFEE UI CONTRACT v1") == 1
