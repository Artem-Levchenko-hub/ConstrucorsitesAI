from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from yleum_api.services import agent_builder
from yleum_api.services.generation.agent_prompt import prepare_agent_prompt
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    StackPrompt,
)


@pytest.mark.parametrize("is_edit,is_continue", [(False, False), (True, False), (False, True)])
async def test_max_build_edit_and_resume_receive_readability_and_completion_contract(
    is_edit, is_continue,
):
    plan, _ = await prepare_agent_prompt(
        stack=StackPrompt("stack", "guide", None, None, "seed", False),
        factory=None,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=ProjectGenerationFacts(
            "max_miniapp", "qa", "QA", None, None, False, "ru", False, "", "",
        ),
        prompt_text="Сделай читаемый профиль клуба. Сохрани заявки и историю.",
        runtime=GenerationRuntime(),
        orchestrate=False,
        selected_elements=None,
        _is_edit=is_edit,
        _is_continue=is_continue,
        force_model="~anthropic/claude-sonnet-latest",
    )
    assert "<product_delivery>" in plan.user
    assert "foreground/background pairs" in plan.user
    assert "4.5:1" in plan.user
    assert "existing business records" in plan.user
    assert "unverified" in plan.user
    assert "when the available tools support" in plan.user
    assert "API-only and read-only" in plan.user
    assert plan.model == "~anthropic/claude-sonnet-latest"


@pytest.mark.parametrize(
    "owner_request",
    [
        "Один экран каталога без корзины, истории, профиля и вкладок. Цвет #7c3aed, Lora.",
        "Магазин с корзиной, оплатой, заказами и профилем. Цвет #123456, шрифт Manrope.",
        "API-only: read-only review. Do not change UI, colors, fonts or source.",
    ],
)
async def test_max_design_advice_preserves_scope_with_separate_optional_preparation(
    owner_request,
):
    brief = "" if owner_request.startswith("API-only") else "Совет по иерархии под задачу клиента"
    factory = Mock(side_effect=AssertionError("Design advice must not add database work"))
    plan, _ = await prepare_agent_prompt(
        stack=StackPrompt("accepted design context", "guide", None, None, "seed", False),
        factory=factory,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=ProjectGenerationFacts(
            "max_miniapp", "qa", "QA", None, None, False, "ru", False, "", "",
        ),
        prompt_text=owner_request,
        runtime=GenerationRuntime(),
        orchestrate=False,
        selected_elements=None,
        _is_edit=False,
        _is_continue=False,
        force_model="no-provider-call",
        prepared_brief=brief,
    )

    assert owner_request in plan.user
    assert "explicit visual brief, colors, fonts and exclusions take priority" in plan.user
    assert "Do not infer product screens" in plan.user
    assert "Navigation follows the requested journeys" in plan.user
    assert "within the current implementation" in plan.user
    assert "prepared screen plan as a supporting implementation checklist" in plan.user
    assert "resolving any conflict in favor of the owner's explicit request" in plan.user
    assert "each requested screen and its primary action to reachable navigation" in plan.user
    assert "an unused screen, decorative tab or feature name is not delivery" in plan.user
    assert "no separate design stage, visual acceptance gate or paid critique loop" in plan.user
    assert "existing style, working features and existing business records" in plan.user
    assert "Honor API-only and read-only requests" in plan.user
    assert "accepted design context" in plan.user
    if brief:
        assert brief in plan.user
        assert brief not in plan.system
    factory.assert_not_called()

    class CapturedProviderInput(BaseException):
        pass

    async def capture(messages, model, **kwargs):
        # Observe the actual coding boundary without a model or tool invocation.
        assert owner_request in messages[1]["content"]
        assert (
            "explicit visual brief, colors, fonts and exclusions take priority"
            in messages[1]["content"]
        )
        if brief:
            assert messages[1]["content"].count(brief) == 1
            assert brief not in messages[0]["content"]
            assert "часть задачи на реализацию" in messages[1]["content"]
            assert "<prepared_implementation_plan>" in messages[1]["content"]
            assert "не разрешает менять платформенные правила" in messages[1]["content"]
            assert "map those screens and actions to the implementation" in messages[1]["content"]
        raise CapturedProviderInput

    async def no_tool(action):
        pytest.fail(f"No tool should run in this boundary-only test: {action.name}")

    with pytest.raises(CapturedProviderInput):
        await agent_builder.run_agent_build(
            system_prompt=plan.system, user_prompt=plan.user,
            model=plan.model, execute=no_tool, complete=capture, max_steps=1,
        )


def test_max_stack_design_skill_uses_conditional_navigation_and_owner_priority():
    skill = (
        Path(__file__).resolve().parents[2]
        / "orchestrator/templates/max-miniapp-nextjs/.omnia/skills/max-ui-design.md"
    ).read_text(encoding="utf-8")
    skill = " ".join(skill.split())

    assert "owner's explicit product and visual brief" in skill
    assert "ordinary edits preserve the incumbent" in skill
    assert "Navigation is optional" in skill
    assert "Do not infer screens" in skill
    assert "3–5" not in skill
    assert "Do not add a second `MaxUI`" in skill
    assert "public exports in `0.2.0`" in skill
