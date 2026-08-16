"""Pure, compact task rendering for deterministic MAX ProductSpec runs."""

from __future__ import annotations

from types import SimpleNamespace

from omnia_api.schemas.max_product_spec import MaxProductSpec
from omnia_api.services.build_plan import build_plan_from_max_product_spec
from omnia_api.services.max_instruction_bundle import (
    MAX_PRODUCT_SPEC_PLAN_MAX_CHARS,
    MAX_PRODUCT_SPEC_TASK_MAX_CHARS,
    MaxInstructionBundle,
    build_max_instruction_bundle,
    render_max_plan_checklist,
    render_max_product_spec_task,
    selected_max_skill_ids,
)

_SPEC = {
    "purpose": "Запись на услуги",
    "audience": "клиенты салона",
    "screens": ["Главная", "Запись"],
    "primary_action": "забронировать время",
    "primary_action_kind": "managed_write",
    "capabilities": ["Расписание", "Избранное"],
    "data": ["Услуга", "Запись"],
    "history": True,
    "integrations": ["календарь"],
    "style": "спокойный премиальный",
    "acceptance": ["Запись сохраняется", "История восстанавливается после reload"],
}


def test_selected_skill_ids_are_safe_bounded_and_deterministic():
    skills = selected_max_skill_ids(["z-pack", "a_pack", "z-pack", "../escape", 42])

    assert skills == ("a_pack", "z-pack")


def test_bundle_renders_business_facts_and_compact_plan_only():
    plan = build_plan_from_max_product_spec(_SPEC)
    bundle = build_max_instruction_bundle(
        _SPEC,
        selected_skill_ids=["ui-ux-pro-max", "booking"],
        build_plan=plan,
    )

    assert isinstance(bundle, MaxInstructionBundle)
    assert bundle.selected_skill_ids == ("booking", "ui-ux-pro-max")
    assert "CANONICAL PRODUCT SPEC" in bundle.task
    assert "Purpose: Запись на услуги" in bundle.task
    assert "PLAN CHECKLIST" in bundle.task
    assert "File pass: ProductApp.tsx" in bundle.task
    assert "Applied capability guidance" in bundle.task
    assert "load only when useful" not in bundle.task
    assert "MAX HEADLESS PLATFORM ADAPTER" not in bundle.task
    assert "dark purple AI dashboard" not in bundle.task


def test_renderer_accepts_model_and_honours_budgets():
    model = SimpleNamespace(model_dump=lambda mode: {**_SPEC, "purpose": "x" * 900})
    plan = build_plan_from_max_product_spec(model)
    task = render_max_product_spec_task(model, build_plan=plan, max_chars=700, plan_max_chars=600)
    checklist = render_max_plan_checklist(plan, max_chars=9_999)

    assert len(task) <= 700
    assert len(checklist) <= MAX_PRODUCT_SPEC_PLAN_MAX_CHARS
    assert len(render_max_product_spec_task(model, max_chars=80)) <= 80
    assert task == render_max_product_spec_task(
        model,
        build_plan=plan,
        max_chars=700,
        plan_max_chars=600,
    )


def test_max_sized_valid_spec_keeps_late_requirements_and_plan() -> None:
    spec = MaxProductSpec.model_validate(
        {
            "purpose": "p" * 800,
            "audience": "a" * 400,
            "screens": [f"screen-{index}-" + "s" * 108 for index in range(8)],
            "primary_action": "m" * 240,
            "primary_action_kind": "managed_write",
            "capabilities": [f"capability-{index}-" + "c" * 225 for index in range(8)],
            "data": [f"data-{index}-" + "d" * 232 for index in range(8)],
            "history": True,
            "integrations": [f"integration-{index}-" + "i" * 225 for index in range(6)],
            "style": "t" * 240,
            "acceptance": [f"accept-{index}-" + "z" * 291 for index in range(8)],
        }
    )
    plan = build_plan_from_max_product_spec(spec)

    task = render_max_product_spec_task(spec, build_plan=plan)

    assert len(task) <= MAX_PRODUCT_SPEC_TASK_MAX_CHARS
    assert "- Integrations:" in task
    assert "integration-5-" in task
    assert "capability-7-" in task
    assert "- Acceptance:" in task
    assert "accept-7-" in task
    assert "PLAN CHECKLIST" in task
