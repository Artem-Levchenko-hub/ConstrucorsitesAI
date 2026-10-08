"""The selected portable MAX edit gets one bounded completion budget."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from yleum_api.services.generation import agent_prompt
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)


async def _plan(monkeypatch, *, configured, native=False, capable=True, selected=True,
                template="max_miniapp", mode="edit", restoration=""):
    settings = SimpleNamespace(agent_builder_max_steps=configured, use_native_agent=native)
    monkeypatch.setattr(agent_prompt, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_prompt, "model_for_role", lambda role, override=None: "test-model")
    handle = Mock(capabilities={"portable_machine": capable})
    handle.is_portable.return_value = selected
    factory = Mock(side_effect=AssertionError("planning must not add work or retries"))
    plan, build_plan = await agent_prompt.prepare_agent_prompt(
        stack=StackPrompt("existing source", template, "guide", None, "system", False),
        factory=factory,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=ProjectGenerationFacts(
            template, "qa", "QA", None, None, False, "en", False, "", restoration,
        ),
        prompt_text="Add editable workout dates to the existing screen.",
        runtime=GenerationRuntime(handle=handle),
        orchestrate=mode != "edit",
        selected_elements=None,
        _is_edit=mode == "edit",
        _is_continue=mode == "continue",
        force_model=None,
    )
    assert build_plan is None
    factory.assert_not_called()
    return plan


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("configured,expected", [(1, 30), (14, 30), (30, 30), (35, 35),
                                                  (40, 40), (80, 40)])
async def test_selected_portable_max_edit_has_bounded_budget(
    monkeypatch, native, configured, expected,
):
    plan = await _plan(monkeypatch, configured=configured, native=native)
    assert plan.steps == expected


@pytest.mark.parametrize("configured", [14, 35, 80])
@pytest.mark.parametrize("selection", [
    {"capable": False}, {"selected": False}, {"template": "nextjs"},
])
async def test_other_edits_retain_eighteen_steps(monkeypatch, configured, selection):
    plan = await _plan(monkeypatch, configured=configured, **selection)
    assert plan.steps == 18


@pytest.mark.parametrize("mode,restoration", [
    ("build", ""), ("continue", ""), ("edit", "historical source"),
])
async def test_other_max_completion_modes_keep_their_existing_budget(
    monkeypatch, mode, restoration,
):
    plan = await _plan(monkeypatch, configured=14, mode=mode, restoration=restoration)
    assert plan.steps == 40


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("configured", [14, 80])
async def test_both_runners_receive_the_planned_budget_once(monkeypatch, native, configured):
    from yleum_api.services import agent_builder, agent_native, max_behavior_proof
    from yleum_api.services.generation import agent_generation

    plan = await _plan(monkeypatch, configured=configured, native=native)
    monkeypatch.setattr(agent_generation, "get_settings", lambda: SimpleNamespace(
        use_native_agent=native, agent_require_green_before_done=True,
        agent_ship_green_on_abort=False,
    ))
    monkeypatch.setattr(max_behavior_proof, "freeze_for_turn", AsyncMock())
    deadline = object()
    coordinator = SimpleNamespace(source_edit_deadline=AsyncMock(return_value=deadline))
    runtime = GenerationRuntime(
        handle=SimpleNamespace(is_portable=lambda: True), coordinator=coordinator,
    )

    class RunnerReached(Exception):
        pass

    legacy_runner = AsyncMock(side_effect=RunnerReached)
    native_runner = AsyncMock(side_effect=RunnerReached)
    monkeypatch.setattr(agent_builder, "run_agent_build", legacy_runner)
    monkeypatch.setattr(agent_native, "run_native_build", native_runner)
    with pytest.raises(RunnerReached):
        await agent_generation.execute_agent_turn(
            _agent_res=None, _is_edit=True, _max_has_generated_snapshot=True,
            _max_seed_files={}, _max_shell_enabled=True,
            baseline=SourceBaseline(uuid4(), "baseline", {"src/app/page.tsx": "old screen"}),
            ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
            project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text="Add a date input to this screen.", runtime=runtime, plan=plan,
            operations=SimpleNamespace(execute=AsyncMock(), emit=AsyncMock()),
        )
    selected, unused = (native_runner, legacy_runner) if native else (legacy_runner, native_runner)
    selected.assert_awaited_once()
    unused.assert_not_awaited()
    assert selected.call_args.kwargs["max_steps"] == plan.steps
    if native:
        assert selected.call_args.kwargs["max_segments"] == 1
        assert selected.call_args.kwargs["edit_deadline"] is deadline
    else:
        assert selected.call_args.kwargs["require_green_before_done"] is True
        assert selected.call_args.kwargs["ship_green_on_abort"] is False
