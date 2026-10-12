from unittest.mock import Mock
from uuid import uuid4

import pytest

from yleum_api.services.generation.agent_prompt import prepare_agent_prompt
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    StackPrompt,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("portable", [False, True])
@pytest.mark.parametrize("mode", ["edit", "build", "continue"])
@pytest.mark.parametrize(
    "owner_prompt",
    [
        "Fix nullable date PATCH, show the date and overdue badge, use #7c3aed buttons.",
        "Fix nullable date PATCH only. Do not change the UI.",
        "Review the nullable date PATCH. Do not modify source or tests.",
    ],
)
async def test_prompt_preserves_all_requested_clauses_without_expanding_scope(
    owner_prompt, mode, portable,
):
    factory = Mock(side_effect=AssertionError("request coverage must not add DB/planning work"))
    handle = Mock(capabilities={"portable_machine": True}) if portable else None
    if handle is not None:
        handle.is_portable.return_value = True
    plan, build_plan = await prepare_agent_prompt(
        stack=StackPrompt("existing context", "max-miniapp-nextjs", "guide", None, "system", False),
        factory=factory,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=ProjectGenerationFacts(
            "max_miniapp", "qa", "QA", None, None, False, "en", False, "", "",
        ),
        prompt_text=owner_prompt,
        runtime=GenerationRuntime(handle=handle),
        orchestrate=mode != "edit",
        selected_elements=None,
        _is_edit=mode == "edit",
        _is_continue=mode == "continue",
        force_model=None,
    )
    assert owner_prompt in plan.user
    assert "each explicit requirement" in plan.user
    assert "API-only change" in plan.user
    assert "rendered UI" in plan.user
    assert "Do not add requirements" in plan.user
    assert "read-only" in plan.user
    assert "Do not weaken existing tests" in plan.user
    delivery = plan.user.split("<product_delivery>", 1)[1].split("</product_delivery>", 1)[0]
    for requirement in (
        "read the actual custom route handlers and normalizers",
        "shared typed DTOs or an explicit validated mapper",
        "exact request and response shapes",
        "stable operation key per user intent",
        "unknown-outcome retry",
        "fresh key for a new intent",
        "failed writes visibly",
        "not persisted success",
        "actual UI payload through the route",
        "write -> read/reload",
        "matching history values",
        "SQL-only persistence checks cannot replace",
        "managed platform footer and its links",
        "layout space, stacking order and safe-area insets",
        "touch targets remain unobstructed",
        "Do not hide the footer",
        "Honor API-only and read-only requests",
        "Read-only means no writes",
        "API-only means no extra UI changes",
        "isolated disposable test records in the project",
        "own local test environment",
        "no external business actions",
        "never change existing or unrelated data",
        "never truncate the database",
    ):
        assert requirement in delivery
    # Custom fields from one failed generated app are not a managed API schema.
    assert all(field not in delivery for field in ("qty", "idempotencyKey", "totalRub"))
    assert plan.steps == ((30 if portable else 18) if mode == "edit" else 40)
    assert build_plan is None
    factory.assert_not_called()
