from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.schemas.message import PromptRequest
from yleum_api.services.generation import acceptance, recovery_acceptance
from yleum_api.services.generation.meal_acceptance import BUILD_PROMPT


@pytest.mark.parametrize(
    ("mode", "required"), [("clarify", False), ("build", True), ("edit", True)],
)
def test_canary_requires_a_publication_only_for_a_product_turn(monkeypatch, mode, required):
    project_id = uuid4()
    monkeypatch.setattr(recovery_acceptance, "get_settings", lambda: SimpleNamespace(
        generation_recovery_mode="canary", generation_recovery_canary_project_ids=str(project_id),
    ))
    run = GenerationRun(project_id=project_id, response_mode=mode, agent_state={})
    assert recovery_acceptance.acceptance_required(run) is required


@pytest.mark.parametrize(("scoped", "prompt", "asks"), [
    (True, BUILD_PROMPT, False),
    (True, "Хочу приложение с меню на месяц", True),
    (False, BUILD_PROMPT, True),
], ids=["reviewed-canary", "other-canary-brief", "reviewed-outside-canary"])
async def test_reviewed_meal_build_uses_the_exact_brief_instead_of_starting_a_survey(
    monkeypatch, scoped, prompt, asks,
):
    project_id = uuid4()
    settings = SimpleNamespace(
        generation_recovery_mode="canary", generation_recovery_execution_enabled=True,
        generation_recovery_canary_project_ids=str(project_id) if scoped else str(uuid4()),
        use_progressive_discovery=True, use_batch_discovery=True, use_async_onboarding=True,
        use_clarify_interview=True,
    )
    monkeypatch.setattr(acceptance, "wants_build_now", lambda _prompt: False)
    monkeypatch.setattr(acceptance, "_infer_code_from_text", lambda _prompt: None)
    monkeypatch.setattr(acceptance, "zero_question_build", lambda *_args: None)
    empty_messages = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    session = SimpleNamespace(execute=AsyncMock(return_value=empty_messages))
    turn = acceptance.PromptAcceptance(
        project_id=project_id, payload=PromptRequest(prompt=prompt), session=session,
        current_user=SimpleNamespace(), project=SimpleNamespace(
            template="max_miniapp", discovery_plan=None,
        ),
    )
    turn.settings = settings
    turn.is_first_build = True
    turn.selected_dump = []
    turn.font_selections = []
    turn.resolved_fonts = []
    turn.credential_redirect = False
    turn.explain_failed_build = False
    await turn.conduct_interview()
    assert turn.async_onboarding is asks
    assert turn.effective_prompt == prompt
    assert session.execute.await_count == int(asks)
