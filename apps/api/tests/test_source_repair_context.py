"""Recovery retains accepted product instructions, while technical context stays bounded."""

import json

import pytest

from yleum_api.services.generation.native_strategy import recovery_message, snapshot_source_context
from yleum_api.services.generation.source_repair_context import (
    directed_source_context,
    with_managed_source_context,
    without_managed_source_context,
)
from yleum_api.services.max_management_contract import build_management_contract
from yleum_api.services.prompt_preparation import render_prepared_brief

ENTRY = "src/app/page.tsx"
MIDDLE_REQUIREMENT = "До выбора тарифа покажи пользу продукта; сохрани выразительную типографику."


def full_design_task():
    # A synthetic plan at the current A1 scale; the unique middle is outside
    # both former head/tail windows and the former entry-milestone prefix.
    half = "Состояния и переходы продукта.\n" * 160
    plan = half + MIDDLE_REQUIREMENT + "\n" + half
    plan += "Д" * (12_400 - len(plan))
    assert len(plan) == 12_400
    return "Полный исходный запрос клиента.\n" + render_prepared_brief(plan) + "\nСохрани все цели."


@pytest.mark.parametrize("mode", ["inspection", "compiler", "output_limit", "transcript"])
def test_every_recovery_preserves_complete_task_and_prepared_middle(mode):
    task = full_design_task()
    result = recovery_message(
        mode, task=task, files={}, focus=[], objective="Apply one source change.",
        diagnostic="Fix current compiler feedback.",
    )
    assert task in result
    assert MIDDLE_REQUIREMENT in result
    assert result.encode().count(task.encode()) == 1


@pytest.mark.parametrize("marker", ["ENTRY_SOURCE_JSON_BEGIN", "CURRENT_MANAGED_SOURCE_JSON"])
def test_source_like_marker_cannot_erase_later_owner_instructions_or_feedback(marker):
    task = (f"Owner goals\n{marker}\n{{\"source\":\"quoted\"}}\n"
            "Keep the violet heading and delivery flow.")
    result = recovery_message(
        "compiler", task=task, files={}, focus=[], objective="Fix the compiler error.",
        diagnostic=None,
    )
    assert task in result


@pytest.mark.parametrize("exists,milestone", [(True, False), (False, False), (False, True)])
def test_directed_repair_preserves_full_design_for_existing_and_missing_entry(exists, milestone):
    task = full_design_task()
    files = {ENTRY: "export default function Page() { return null; }"} if exists else {}
    result = directed_source_context(task, files, ENTRY, entry_milestone=milestone)
    assert result.startswith(task + "\n\n")
    assert MIDDLE_REQUIREMENT in result
    if exists:
        assert files[ENTRY] in result
    if milestone:
        assert "brief excerpt" not in result
        assert "one working screen" not in result


def test_preserving_task_does_not_expand_source_excerpt_or_expose_secret_files():
    files = {
        ENTRY: "export const content = '" + "x" * 20_000 + "';",
        ".env": "PRIVATE_VALUE_MUST_NOT_APPEAR",
        "src/lib/secret.ts": "API_KEY=PRIVATE_VALUE_MUST_NOT_APPEAR",
    }
    result = snapshot_source_context(files, [ENTRY, ".env", "src/lib/secret.ts"])
    source = json.loads(result)
    assert set(source) == {ENTRY}
    assert source[ENTRY]["prefix_only"] is True
    assert len(source[ENTRY]["source"].encode()) <= 6_000
    assert len(result.encode()) <= 18_000
    assert "PRIVATE_VALUE_MUST_NOT_APPEAR" not in result


def managed_files(source="export const version = 'old snapshot';"):
    return {
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        ENTRY: source,
    }


def test_exact_backend_suffix_is_separated_without_losing_unicode_or_marker_like_prose():
    files = managed_files()
    task = full_design_task() + "\nCURRENT_MANAGED_SOURCE_JSON\nЭто пожелание владельца 🟣."
    attached = with_managed_source_context(task, files)
    assert without_managed_source_context(attached, files).encode() == task.encode()
    recovered = recovery_message(
        "compiler", task=without_managed_source_context(attached, files),
        files=managed_files("export const version = 'current snapshot';"),
        focus=[ENTRY], objective="Fix the current failure.", diagnostic=None,
    )
    assert task in recovered
    assert "old snapshot" not in recovered
    assert "current snapshot" in recovered


@pytest.mark.parametrize("changed", ["source", "trailing_feedback", "ineligible", "no_manifest"])
def test_uncertain_suffix_keeps_complete_task_without_a_new_gate(changed):
    files = managed_files()
    attached = with_managed_source_context(full_design_task(), files)
    if changed == "source":
        files[ENTRY] = "export const version = 'different snapshot';"
    elif changed == "trailing_feedback":
        attached += "\nLater feedback: preserve the violet type hierarchy."
    elif changed == "ineligible":
        files[ENTRY] = "x" * (64 * 1024 + 1)
    else:
        files.pop(".omnia/management.json")
    assert without_managed_source_context(attached, files) == attached
