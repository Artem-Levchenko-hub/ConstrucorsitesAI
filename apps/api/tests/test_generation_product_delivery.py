from uuid import uuid4

import pytest

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
