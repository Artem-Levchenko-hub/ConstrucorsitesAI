import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
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
@pytest.mark.parametrize("portable", [True, False])
async def test_max_build_edit_and_resume_receive_readability_and_completion_contract(
    is_edit, is_continue, portable,
):
    from yleum_api.services.max_data_evolution import build_max_agent_guide

    handle = SimpleNamespace(
        capabilities={"portable_machine": True},
        snapshot_files=AsyncMock(return_value={".omnia/cell.json": "{}"}),
        is_portable=lambda: True,
    ) if portable else None
    stack_name = "max-miniapp-nextjs"
    legacy = agent_builder.load_stack_system_prompt(stack_name)
    guide = await build_max_agent_guide(legacy, handle, coordinator_handoff=portable)
    plan, _ = await prepare_agent_prompt(
        stack=StackPrompt(
            "seed", stack_name, guide, None,
            agent_builder.build_system_prompt(guide, coordinator_handoff=portable), False,
        ),
        factory=None,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=ProjectGenerationFacts(
            "max_miniapp", "qa", "QA", None, None, False, "ru", False, "", "",
        ),
        prompt_text="Сделай читаемый профиль клуба. Сохрани заявки и историю.",
        runtime=GenerationRuntime(handle=handle),
        orchestrate=False,
        selected_elements=None,
        _is_edit=is_edit,
        _is_continue=is_continue,
        force_model="~anthropic/claude-sonnet-latest",
    )
    block = plan.user.split("<product_delivery>")[1].split("</product_delivery>")[0]
    delivery = " ".join(block.split())
    assert "interactive visitor transaction that depends on owner-managed data" in delivery
    assert "no-code setup" in delivery and "authorized application handler" in delivery
    assert "OWNER_GUIDE.md" in delivery and "one example" in delivery
    assert "existing product test" in delivery and "isolated empty business data" in delivery
    assert "reserve the slot" in delivery and "awaits confirmation" in delivery
    if not portable:
        assert "For confirmed slot reservations, prevent double booking" in plan.system
    assert "foreground/background pairs" in plan.user
    assert "4.5:1" in plan.user
    assert "existing business records" in plan.user
    assert "unverified" in plan.user
    assert "when the available tools support" in plan.user
    assert "API-only and read-only" in plan.user
    assert "of a finished product preserve its established typography.json decision" in plan.user
    assert "An unfinished first build must still implement the original request" in plan.user
    assert "a provisional seed is not an accepted final design" in plan.user
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
    assert "seed fallback or intermediate draft is not an accepted final design" in skill
    assert "prepared visual plan" in skill
    assert "prevent accidental page-wide horizontal overflow" in skill
    assert "deliberately contained gallery" in skill


@pytest.mark.parametrize("coordinator", [False, True])
def test_actual_native_system_keeps_owner_choices_without_freezing_first_build(coordinator):
    from yleum_api.services import agent_native

    owner_skill = "Explicit owner choice: one screen, red controls, no bottom navigation."
    system = agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT", owner_skill, coordinator_handoff=coordinator,
    )
    assert owner_skill in system
    assert "текущий явный запрос" in system.lower()
    assert "промежуточный черновик не являются принятым итоговым дизайном" in system
    assert "доделать исходный запрос и подготовленный план" in system
    assert "платформенные и SDK-контракты" in system


@pytest.mark.parametrize("native", [False, True])
async def test_prepared_plan_survives_actual_http_serialization_for_both_protocols(
    monkeypatch, native,
):
    from yleum_api.core.config import get_settings
    from yleum_api.services import agent_native, llm_client
    from yleum_api.services.prompt_preparation import render_prepared_brief

    owner_request = "Сделай питание под цель. Первый шаг — анкета, не тарифы."
    brief = "PLAN_BEGIN\n" + "Сохрани пользовательский путь. " * 500
    brief += "\nUNIQUE_VISUAL_MIDDLE: кольцо приёмов пищи, открытая композиция.\n"
    brief += "Согласуй типографику и действия. " * 500 + "\nPLAN_END"
    assert len(brief) > 12_000
    plan, _ = await prepare_agent_prompt(
        stack=StackPrompt("stack", "MAX PLATFORM CORE CONTRACT", None, None, "seed", False),
        factory=Mock(side_effect=AssertionError("Prompt assembly must not access storage")),
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=ProjectGenerationFacts(
            "max_miniapp", "qa", "QA", None, None, False, "ru", False, "", "",
        ),
        prompt_text=owner_request, runtime=GenerationRuntime(), orchestrate=True,
        selected_elements=None, _is_edit=False, _is_continue=False,
        force_model="claude-sonnet-5.5", prepared_brief=brief,
    )
    requests = []

    def reply(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if native:
            content = payload["messages"][0]["content"]
            sent_text = content if isinstance(content, str) else "".join(
                block["text"] for block in content if block.get("type") == "text"
            )
            system = payload["system"]
            response = {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
        else:
            sent_text = payload["messages"][1]["content"]
            system = payload["messages"][0]["content"]
            response = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        assert sent_text == plan.user
        assert owner_request in sent_text
        assert sent_text.count(render_prepared_brief(brief)) == 1
        assert "PLAN_BEGIN" in sent_text and "UNIQUE_VISUAL_MIDDLE" in sent_text
        assert "PLAN_END" in sent_text
        assert brief not in json.dumps(system, ensure_ascii=False)
        return httpx.Response(200, json=response)

    settings = get_settings().model_copy(update={"mock_llm": False, "llm_gateway_url": "https://gateway.test"})
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    original_client = httpx.AsyncClient
    if native:
        async with original_client(transport=httpx.MockTransport(reply)) as client:
            result = await agent_native._call_messages(
                client, "https://gateway.test/v1/messages",
                [{"role": "user", "content": plan.user}],
                agent_native.native_system_prompt(plan.stack_guide or "", plan.skills),
                model=plan.model, tools=[],
            )
        assert result["stop_reason"] == "end_turn"
    else:
        def isolated_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(reply)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(llm_client.httpx, "AsyncClient", isolated_client)
        assert await llm_client.complete_chat(
            [{"role": "system", "content": plan.system}, {"role": "user", "content": plan.user}],
            plan.model, max_tokens=None,
        ) == "OK"
    assert len(requests) == 1
