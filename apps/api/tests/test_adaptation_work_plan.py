"""What the agent is told before it starts adapting a historical version.

The live run of 21.09.2026 spent most of a 25-minute deadline rediscovering facts the
controller had already collected, under the prompt template for a *point edit*. These
tests pin the compact plan, the adaptation-specific prompt and the memory carried into
each repair pass.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.test_restoration_adaptation import source_case  # noqa: F401  (fixture)
from yleum_api.services.restoration_adaptation import _adaptation_work_plan


def _diff(**overrides: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "historical_source": "selected_historical_code",
        "current_source": "controller_observed_catalog",
        "findings": [
            {
                "code": "column_became_required",
                "status": "unknown",
                "severity": "blocking",
                "operation": "clients.insert",
                "object": "public.clients.surname",
                "evidence": "structural_rule",
                "resolution": "Keep the current column and adapt historical writes.",
            },
            {
                "code": "owner_rule_unchanged",
                "status": "compatible",
                "severity": "info",
                "operation": "clients.access",
                "object": "public.clients.owner_id",
                "evidence": "structural_rule",
                "resolution": None,
            },
        ],
        "blockers": ["Новое обязательное поле требует совместимой записи."],
        **overrides,
    }


_REPORT = {
    "capabilities": {
        "lost": [{"method": "GET", "path": "/api/clients/summary"}],
        "restored": [],
    }
}
_FILES = {
    "src/app/clients/page.tsx": "export const rows = await db.from('clients').select('surname')",
    "src/app/about/page.tsx": "export default () => 'about'",
}


def test_the_plan_names_the_conflict_the_lost_route_and_where_to_start() -> None:
    plan = _adaptation_work_plan(_diff(), _REPORT, _FILES)

    assert "public.clients.surname — clients.insert — Keep the current column" in plan
    assert "GET /api/clients/summary" in plan
    assert "src/app/clients/page.tsx (clients, surname)" in plan
    # A file that mentions nothing conflicting is not busywork for the agent.
    assert "src/app/about/page.tsx" not in plan
    assert "Новое обязательное поле" in plan
    assert "additive only" in plan


def test_a_passed_check_is_not_presented_as_work() -> None:
    plan = _adaptation_work_plan(_diff(), _REPORT, _FILES)

    assert "owner_rule_unchanged" not in plan and "clients.access" not in plan


def test_an_incompatible_finding_counts_even_without_the_blocking_severity() -> None:
    diff = _diff(
        findings=[
            {
                "status": "incompatible",
                "severity": "warning",
                "operation": "clients.read",
                "object": "public.clients.title",
                "resolution": "",
            }
        ],
        blockers=[],
    )

    assert "public.clients.title — clients.read" in _adaptation_work_plan(diff, None, _FILES)


def test_no_controller_evidence_means_no_plan_at_all() -> None:
    empty = {"version": 1, "findings": [], "blockers": []}

    assert _adaptation_work_plan(empty, {"capabilities": {"lost": []}}, _FILES) == ""
    assert _adaptation_work_plan(None, None, {}) == ""
    # A malformed bundle must not raise on the prompt path.
    assert _adaptation_work_plan({"findings": "nonsense"}, {"capabilities": 5}, _FILES) == ""


def test_a_schema_name_alone_never_selects_every_file() -> None:
    diff = _diff(
        findings=[
            {"severity": "blocking", "operation": "read", "object": "public", "resolution": ""}
        ],
        blockers=[],
    )

    plan = _adaptation_work_plan(diff, None, {"src/a.ts": "public class", "src/b.ts": "x"})

    assert "src/a.ts" not in plan and "src/b.ts" not in plan


def test_the_plan_is_bounded() -> None:
    diff = _diff(
        findings=[
            {
                "severity": "blocking",
                "operation": f"clients.op{index}",
                "object": f"public.table_{index}.column_{index}",
                "resolution": "x" * 400,
            }
            for index in range(40)
        ],
        blockers=[f"blocker {index}" for index in range(40)],
    )
    files = {f"src/file_{index}.ts": f"table_{index}" for index in range(40)}

    plan = _adaptation_work_plan(diff, _REPORT, files)

    assert len(plan.encode("utf-8")) <= 8 * 1024
    assert plan.count("\n1. ") <= 1
    assert "table_12" not in plan


async def test_the_plan_reaches_the_agent_without_touching_the_sealed_bundle(
    source_case,  # noqa: F811
) -> None:
    service, session, project, _operation, run, reference, _historical, _reads = source_case

    bundle = await service.prepare_adaptation(
        session, project, project.owner_id, reference, run
    )
    run.agent_state = {"restoration_adaptation": bundle}
    text = await service.append_adaptation_context(
        session, run.id, project.id, project.owner_id, project.current_snapshot_id, "Adapt"
    )

    assert "ADAPTATION WORK PLAN" in text
    assert "public.clients.surname — clients.insert" in text
    # The plan is computed at prompt time: putting it in the bundle would break the
    # integrity check of a request prepared by an older revision.
    assert "ADAPTATION WORK PLAN" not in str(bundle)
    assert service.has_current_adaptation_contract(bundle)
    # Order: instructions, probe contract, plan, then the historical source itself.
    assert text.index("ADAPTATION WORK PLAN") < text.index('{"version": 2')


def _plan_for(*, restoration_context: str, template: str = "max_miniapp"):
    from yleum_api.services.generation.contracts import ProjectGenerationFacts

    return ProjectGenerationFacts(
        template=template,
        slug="p",
        name="P",
        design_preset_id=None,
        discovery_spec=None,
        image_gen_enabled=False,
        language="ru",
        is_imported=False,
        memory_context="",
        restoration_context=restoration_context,
    )


async def _prompt(monkeypatch: pytest.MonkeyPatch, *, restoration_context: str):
    from yleum_api.services.generation import agent_prompt
    from yleum_api.services.generation.contracts import (
        GenerationIds,
        GenerationRuntime,
        StackPrompt,
    )

    monkeypatch.setattr(agent_prompt, "model_for_role", lambda role, override=None: "m")
    stack = StackPrompt(
        seed_context="\n\nSEED", orchestrator_template="max-miniapp-nextjs",
        guide="GUIDE", skills=None, system="SYSTEM", bare_stack=False,
    )
    plan, _ = await agent_prompt.prepare_agent_prompt(
        stack=stack,
        factory=None,  # type: ignore[arg-type]
        ids=GenerationIds(
            run_id=uuid4(), project_id=uuid4(), user_id=uuid4(),
            user_message_id=uuid4(), assistant_message_id=uuid4(),
        ),
        project_info=_plan_for(restoration_context=restoration_context),
        prompt_text="Верни версию",
        runtime=GenerationRuntime(),
        orchestrate=False,
        selected_elements=None,
        _is_edit=True,
        _is_continue=False,
        force_model=None,
    )
    return plan


async def test_an_adaptation_does_not_get_the_point_edit_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _prompt(monkeypatch, restoration_context="\n\nHISTORICAL SOURCE")

    assert "МИНИМАЛЬНУЮ правку" not in plan.user
    assert "Верни экраны и функции выбранной исторической версии" in plan.user
    assert "Верни версию" in plan.user
    assert plan.system == "SYSTEM"
    # 18 steps is the point-edit budget; a restoration rebuilds whole screens.
    assert plan.steps == 40


async def test_the_versions_own_tests_are_part_of_what_must_be_adapted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Живой отказ 23.09: старые тесты утверждали про базу то, чего в ней нет.

    Вместе со старой версией возвращаются её проверки. Одна из них требовала,
    чтобы база запрещала пустую заметку, — а текущая база этого уже не
    запрещает, и вернуть запрет нельзя: additive-правило не даёт менять
    существующие колонки. Адаптация объявила себя неудачной, хотя код
    переписала верно. Владелец выбрал: приводить к текущей базе и сами тесты.
    """
    plan = await _prompt(monkeypatch, restoration_context="\n\nHISTORICAL SOURCE")

    assert "тесты, приехавшие со старой версией" in plan.user
    assert "перенеси это требование в само приложение" in plan.user


async def test_adapting_the_tests_must_not_become_deleting_the_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Самый вероятный способ «починить» тест — ослабить его. Это потеря смысла
    # старой версии, и инструкция обязана запрещать такой путь прямо.
    plan = await _prompt(monkeypatch, restoration_context="\n\nHISTORICAL SOURCE")

    assert "НЕ выбрасывай" in plan.user
    assert "схему под него НЕ переделывай" in plan.user


async def test_an_ordinary_edit_never_hears_about_historical_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _prompt(monkeypatch, restoration_context="")

    assert "тесты, приехавшие со старой версией" not in plan.user


async def test_an_ordinary_edit_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = await _prompt(monkeypatch, restoration_context="")

    assert "МИНИМАЛЬНУЮ правку" in plan.user
    assert plan.steps == 18


async def test_each_repair_pass_carries_what_the_earlier_ones_were_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services import agent_native
    from yleum_api.services.generation import agent_finalization

    tasks: list[str] = []
    workspace = {"src/app/page.tsx": "v2", "src/app/new.tsx": "added"}

    async def run_native_build(**kwargs: Any) -> Any:
        tasks.append(str(kwargs["task"]))
        return SimpleNamespace(stop_reason="done", summary="", files={}, steps=1)

    monkeypatch.setattr(agent_native, "run_native_build", run_native_build)
    repairs: list[str] = []

    async def coordinator_finalize(*, prompt: str, repair: Any) -> Any:
        for detail in ("missing screen", "missing route"):
            repairs.append(detail)
            await repair(detail)
        # Leave through the one branch that re-raises untouched; the rest of
        # finalization is not what this test is about.
        raise agent_finalization.AdaptationActivationPending("handed off")

    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(finalize_with_repair=coordinator_finalize),
        handle=SimpleNamespace(snapshot_files=_snapshot(workspace)),
    )
    with pytest.raises(agent_finalization.AdaptationActivationPending):
        await agent_finalization.finalize_max_candidate(
            _is_edit=True,
            _max_has_generated_snapshot=True,
            _max_shell_enabled=False,
            accumulated="",
            baseline=SimpleNamespace(files={}, sha=None, snapshot_id=None),  # type: ignore[arg-type]
            files={"src/app/page.tsx": "v1"},
            ids=SimpleNamespace(  # type: ignore[arg-type]
                run_id=uuid4(), project_id=uuid4(), user_id=uuid4(),
                user_message_id=uuid4(), assistant_message_id=uuid4(),
            ),
            is_free=False,
            prompt_text="Верни версию",
            runtime=runtime,  # type: ignore[arg-type]
            plan=SimpleNamespace(user="TASK", stack_guide="G", skills=None, steps=40),  # type: ignore[arg-type]
            operations=SimpleNamespace(  # type: ignore[arg-type]
                execute=None, emit=_emit(), probe_runtime=None, probe_build=None, preview_url=None
            ),
        )

    assert repairs == ["missing screen", "missing route"]
    # Pass one knows only its own feedback; pass two knows both, and what changed.
    assert "EARLIER CHECK FEEDBACK" not in tasks[0]
    assert "missing screen" in tasks[0]
    assert "EARLIER CHECK FEEDBACK (pass 1" in tasks[1]
    assert "missing screen" in tasks[1] and "missing route" in tasks[1]
    assert "src/app/new.tsx" in tasks[1] and "src/app/page.tsx" in tasks[1]


def _snapshot(files: dict[str, str]):
    async def snapshot_files() -> dict[str, str]:
        return dict(files)

    return snapshot_files


def _emit():
    async def emit(_event: str, _data: dict[str, Any]) -> None:
        return None

    return emit
