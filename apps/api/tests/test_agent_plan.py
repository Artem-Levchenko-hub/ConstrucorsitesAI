from __future__ import annotations

import pytest

from omnia_api.services import agent_plan


def test_initial_plan_is_observable_and_has_no_reasoning_field() -> None:
    state = agent_plan.initial_plan("Собери фитнес-тренера", max_product=True)

    assert state["objective"] == "Собери фитнес-тренера"
    assert len(state["steps"]) == 5
    assert state["steps"][0]["id"] == "step-1"
    assert "reasoning" not in state
    assert "thoughts" not in state


def test_plan_refinement_preserves_existing_step_evidence() -> None:
    initial = agent_plan.initial_plan("Собери приложение", max_product=True)
    checkpoint = agent_plan.update_plan(
        initial,
        step_id="step-1",
        status="completed",
        summary="Концепция сохранена",
        evidence=[".omnia/max-design-spec.json прочитан"],
        artifacts=[".omnia/max-design-spec.json"],
        next_action="Реализовать главный экран",
    )

    refined = agent_plan.make_plan(
        objective="Собери приложение",
        steps=[
            "Зафиксировать концепцию",
            "Реализовать главный экран",
            "Проверить приложение",
        ],
        acceptance_criteria=["Сборка чистая", "Runtime работает"],
        previous=checkpoint,
    )

    assert refined["steps"][0]["status"] == "completed"
    assert refined["steps"][0]["artifacts"] == [".omnia/max-design-spec.json"]
    assert refined["next_action"] == "Реализовать главный экран"


def test_update_plan_rejects_unknown_steps_and_hidden_free_form_dump() -> None:
    state = agent_plan.initial_plan("Собери приложение")

    with pytest.raises(ValueError, match="unknown plan step"):
        agent_plan.update_plan(
            state,
            step_id="private-reasoning",
            status="completed",
            summary="Длинное внутреннее рассуждение",
        )


def test_recovery_context_contains_only_status_evidence_and_next_action() -> None:
    state = agent_plan.initial_plan("Собери приложение", max_product=True)
    state = agent_plan.record_tool_evidence(
        state,
        tool="build",
        ok=True,
        summary="Build clean",
        artifact="src/app/page.tsx",
    )
    context = agent_plan.recovery_context(state)

    assert "RECOVERED EXECUTION CHECKPOINT" in context
    assert "Last verified tool: build" in context
    assert "Build clean" in context
    assert "Continue from the live files" in context


def test_plan_observation_uses_uniform_harness_contract() -> None:
    state = agent_plan.initial_plan("Собери приложение")
    observation = agent_plan.observation(state, "Execution plan persisted.")

    assert observation["ok"] is True
    assert observation["status"] == "success"
    assert observation["summary"] == "Execution plan persisted."
    assert observation["next_actions"]
    assert observation["artifacts"] == []
