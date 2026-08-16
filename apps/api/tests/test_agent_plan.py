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
    initial = agent_plan.record_tool_evidence(
        initial,
        tool="write_file",
        ok=True,
        summary="Концепция записана",
    )
    checkpoint = agent_plan.update_plan(
        initial,
        step_id="step-1",
        status="completed",
        summary="Концепция сохранена",
        evidence=[initial["tool_evidence"][-1]["id"]],
        artifacts=[".omnia/max-design-spec.json"],
        next_action="Реализовать главный экран",
    )

    refined = agent_plan.make_plan(
        objective="Собери приложение",
        steps=[
            initial["steps"][0]["title"],
            "Реализовать главный экран",
            "Проверить приложение",
        ],
        acceptance_criteria=["Сборка чистая", "Runtime работает"],
        previous=checkpoint,
    )

    assert refined["steps"][0]["status"] == "completed"
    assert refined["steps"][0]["artifacts"] == [".omnia/max-design-spec.json"]
    assert refined["next_action"] == "Реализовать главный экран"


def test_plan_refinement_reopens_same_position_when_step_meaning_changes() -> None:
    initial = agent_plan.initial_plan("Собери приложение", max_product=True)
    initial = agent_plan.record_tool_evidence(
        initial, tool="write_file", ok=True, summary="Концепция записана"
    )
    initial = agent_plan.update_plan(
        initial,
        step_id="step-1",
        status="completed",
        summary="Готово",
        evidence=[initial["tool_evidence"][-1]["id"]],
    )

    refined = agent_plan.make_plan(
        objective="Собери приложение",
        steps=["Провести визуальную проверку"],
        acceptance_criteria=["Визуальная проверка зелёная"],
        previous=initial,
    )

    assert refined["steps"][0]["status"] == "pending"
    assert refined["steps"][0]["evidence"] == []


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


def test_tool_evidence_recovers_from_malformed_legacy_sequence() -> None:
    state = agent_plan.initial_plan("Собери приложение")
    state["tool_evidence_seq"] = "not-a-number"

    updated = agent_plan.record_tool_evidence(state, tool="build", ok=True, summary="Build clean")

    assert updated["tool_evidence_seq"] == 1
    assert updated["tool_evidence"][-1]["id"] == "tool:build:1"


def test_completion_gap_is_fail_closed_until_every_public_step_is_attested() -> None:
    state = agent_plan.initial_plan("Собери приложение", max_product=True)

    assert "step-1" in str(agent_plan.completion_gap(state))
    for tool in ("write_file", "build", "runtime_check", "see"):
        state = agent_plan.record_tool_evidence(state, tool=tool, ok=True, summary=f"{tool} green")
    state = agent_plan.reconcile_tool_evidence(state)

    assert agent_plan.completion_gap(state) is None
    assert "plan_task" in str(agent_plan.completion_gap(None))


def test_kernel_plan_closes_only_from_write_build_runtime_and_signed_proof() -> None:
    state = agent_plan.make_plan(
        objective="Собрать MAX продукт",
        steps=[
            "Реализовать целостную multi-file структуру экранов и сценариев",
            "Автоматически собрать проект и устранить полный список ошибок",
            "Проверить живой runtime приложения",
            "Пройти подписанную функциональную проверку",
        ],
        acceptance_criteria=["Продукт работает"],
    )
    for tool in ("write_files", "build", "runtime_check", "see"):
        state = agent_plan.record_tool_evidence(
            state,
            tool=tool,
            ok=True,
            summary=f"{tool} green",
            mutated=tool == "write_files",
        )
    state = agent_plan.reconcile_tool_evidence(state)

    assert [step["status"] for step in state["steps"]] == ["completed"] * 4
    assert agent_plan.completion_gap(state) is None


def test_update_plan_rejects_evidence_free_or_incompatible_completion() -> None:
    state = agent_plan.initial_plan("Собери приложение", max_product=True)
    state = agent_plan.record_tool_evidence(state, tool="build", ok=True, summary="typecheck clean")

    with pytest.raises(ValueError, match="server tool-evidence"):
        agent_plan.update_plan(
            state,
            step_id="step-5",
            status="completed",
            summary="Визуально готово",
            evidence=[state["tool_evidence"][-1]["id"]],
        )


def test_mutation_invalidates_old_build_runtime_and_visual_proof() -> None:
    state = agent_plan.initial_plan("Собери приложение", max_product=True)
    for tool in ("write_file", "build", "runtime_check", "see"):
        state = agent_plan.record_tool_evidence(
            state,
            tool=tool,
            ok=True,
            summary=f"{tool} green",
            mutated=tool == "write_file",
        )
    state = agent_plan.reconcile_tool_evidence(state)
    assert agent_plan.completion_gap(state) is None

    state = agent_plan.record_tool_evidence(
        state,
        tool="write_file",
        ok=True,
        summary="source changed",
        mutated=True,
    )
    state = agent_plan.record_tool_evidence(
        state,
        tool="build",
        ok=False,
        summary="typecheck red",
    )
    state = agent_plan.reconcile_tool_evidence(state)

    by_title = {item["title"]: item["status"] for item in state["steps"]}
    assert by_title["Собрать проект и устранить реальные ошибки"] == "pending"
    assert by_title["Проверить живой runtime и ключевые состояния"] == "pending"
    assert by_title["Провести визуальную проверку и применить конкретные улучшения"] == "pending"


def test_latest_visual_failure_supersedes_older_green_evidence() -> None:
    state = agent_plan.initial_plan("Собери приложение", max_product=True)
    state = agent_plan.record_tool_evidence(state, tool="see", ok=True, summary="visual green")
    state = agent_plan.reconcile_tool_evidence(state)
    assert state["steps"][4]["status"] == "completed"

    state = agent_plan.record_tool_evidence(
        state, tool="see", ok=False, summary="visual needs repair"
    )
    state = agent_plan.reconcile_tool_evidence(state)

    assert state["steps"][4]["status"] == "pending"
