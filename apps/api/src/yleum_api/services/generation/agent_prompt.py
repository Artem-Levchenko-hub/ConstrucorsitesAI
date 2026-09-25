from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import (
    get_settings,
    model_for_role,
)
from yleum_api.services import agent_builder
from yleum_api.services.build_plan import BuildPlan
from yleum_api.services.generation.agent_messages import _agent_step_budget
from yleum_api.services.generation.agent_preparation import persist_build_plan
from yleum_api.services.generation.contracts import (
    AgentPromptPlan,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    StackPrompt,
)

_log = logging.getLogger("yleum_api.routers.messages")


async def prepare_agent_prompt(
    *,
    stack: StackPrompt,
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    runtime: GenerationRuntime,
    orchestrate: bool,
    selected_elements: list[dict[str, Any]] | None,
    _is_edit: bool,
    _is_continue: bool,
    force_model: str | None,
) -> tuple[AgentPromptPlan, BuildPlan | None]:
    _seed_block = stack.seed_context
    _build_plan = None
    try:
        from yleum_api.services import build_plan as _bplan

        if get_settings().use_build_plan and project_info.template != "max_miniapp":
            _build_plan = _bplan.BuildPlan()
            if orchestrate and not _is_continue and not _is_edit:
                _build_plan = await _bplan.plan_build(
                    prompt_text,
                    stack=(stack.orchestrator_template or project_info.template or ""),
                    user_id=str(ids.user_id),
                    project_id=str(ids.project_id),
                )
                if not _build_plan.is_empty:
                    try:
                        await persist_build_plan(factory, ids.project_id, _build_plan)
                    except Exception as _bp_persist_exc:
                        print(
                            f"[PP] build_plan persist skipped: {_bp_persist_exc!r}",
                            flush=True,
                        )
            else:
                _build_plan = _bplan.read_plan(project_info.discovery_spec)
            _bp_block = _build_plan.checklist_block()
            if _bp_block:
                _seed_block = _seed_block + _bp_block
                print(
                    "[PP] build_plan injected "
                    f"screens={len(_build_plan.screens)} "
                    f"caps={len(_build_plan.capabilities)} "
                    f"blocking={len(_build_plan.blocking_capabilities())}",
                    flush=True,
                )
    except Exception as _bp_exc:
        print(f"[PP] build_plan skipped: {_bp_exc!r}", flush=True)
    if _is_continue:
        # Resume: finish the partial app the agent left in the live
        # container (the prior turn committed + hot-reloaded what it had).
        # Full build prompt + the build step budget; the agent re-orients
        # from the current files (seed block) and carries on to `done`.
        _agent_user = (
            "Приложение в проекте уже ЧАСТИЧНО собрано (часть файлов на месте). "
            "Доведи сборку ДО КОНЦА: запусти build, посмотри текущие файлы "
            "(раскладка ниже), допиши недостающие entities/<Name>.json и страницы "
            "(включая обязательный dashboard/page.tsx индекс), почини ошибки до "
            "чистоты, затем done. НЕ начинай с нуля и НЕ переписывай работающее."
            f"\n\nДоп. пожелание пользователя: {prompt_text}{_seed_block}"
        )
        _agent_system = stack.system
        # A manual resume is a full completion pass, not a short edit.
        # Keep the same ceiling as the initial MAX product build.
        _agent_steps = 30
    elif _is_edit and project_info.restoration_context:
        # A restoration adaptation is not a point edit: it brings whole historical
        # screens back and fits them to the current database. The edit template
        # ("find the file, make the MINIMAL change") and its 18-step budget cost a
        # live run its whole deadline. The server-computed work plan and the
        # historical source ride in the seed block.
        _agent_user = (
            "Верни экраны и функции выбранной исторической версии в этот черновик и "
            "приведи их к ТЕКУЩЕЙ базе данных.\n\n"
            f"Запрос владельца:\n\n{prompt_text}\n{_seed_block}\n\n"
            "Ниже уже собраны: исторические файлы, отчёт совместимости и план работ "
            "(конфликты, маршруты, файлы). Начни с плана, а не с повторного обхода "
            "проекта. Проверь текущую схему прежде чем полагаться на план, вноси "
            "только additive-изменения схемы, сохраняй существующие строки, скрытые "
            "поля и границы владельцев.\n\n"
            # Живой случай 23.09: вместе со старой версией вернулись её тесты,
            # а они утверждали про базу то, чего там уже нет («заметка обязательна»).
            # Правило «только additive» вернуть такое ограничение не даёт, и
            # адаптация объявила себя неудачной, хотя код переписала верно.
            "Проверки и тесты, приехавшие со старой версией, — тоже её код. Если тест "
            "утверждает про базу то, чего в ней уже нет, перенеси это требование в само "
            "приложение (проверка при записи) и убери из теста утверждение про схему. "
            "Требование при этом НЕ выбрасывай и схему под него НЕ переделывай: ослабить "
            "проверку вместо переноса — это потеря смысла старой версии. "
            "Затем build, проверка и done."
        )
        _agent_system = stack.system
        _agent_steps = _agent_step_budget(
            project_info.template,
            configured_steps=min(40, max(1, int(get_settings().agent_builder_max_steps))),
        )
    elif _is_edit:
        _sel_block = ""
        try:
            if selected_elements:
                _sel_block = (
                    "\n\nПользователь выделил элемент(ы) в превью: " + str(selected_elements)[:800]
                )
        except Exception:
            _sel_block = ""
        _agent_user = (
            f"Внеси ТОЧЕЧНОЕ изменение в существующее приложение по "
            f"запросу:\n\n{prompt_text}\n{_sel_block}{_seed_block}\n\n"
            f"Найди нужный файл (grep/read), внеси МИНИМАЛЬНУЮ правку "
            f"(edit_file/write_file), запусти build, затем done. НЕ зацикливайся "
            f"на чтении — как только нашёл причину, СРАЗУ пиши правку (а не ещё "
            f"один read). Если ошибка указывает на бандл (src_*.js) — найди "
            f"реальный исходник в src/ по симптому. Не пересобирай работающее."
        )
        _agent_system = agent_builder.build_edit_system_prompt(stack.guide)
        # Budget above the no-write/stall thresholds so the loop's
        # escalate-to-stronger-model actually fires before max_steps when a
        # cheap model explores without writing (the "Починить" did nothing bug).
        _agent_steps = 18
    else:
        if project_info.template == "max_miniapp":
            from yleum_api.services.max_generation_contract import build_max_product_contract

            _portable_capable = (
                runtime.handle is not None
                and runtime.handle.capabilities.get("portable_machine") is True
                and runtime.handle.is_portable()
            )
            _max_product_contract = build_max_product_contract(
                prompt_text, portable=_portable_capable
            )
            _agent_user = (
                "Построй полноценный MAX Mini App под ПОЛНЫЙ запрос "
                f"пользователя:\n\n{prompt_text}\n\n{_seed_block}\n\n"
                "В контейнере уже есть только защищённое платформенное ядро. "
                "Продуктовой страницы, визуального шаблона и готовой навигации нет: "
                "создай src/app/page.tsx, стили, архитектуру, экраны, компоненты и "
                "рабочие сценарии с нуля. Сохрани MAX Bridge, "
                "серверную проверку initData, профиль пользователя, webhook и "
                "управляемые Studio-файлы. Не зашивай секреты пользователя в код.\n\n"
                f"{_max_product_contract}"
            )
            if _portable_capable:
                _agent_user = (
                    "Build the complete MAX product requested by the user:\n\n"
                    f"{prompt_text}\n\n{_seed_block}\n\n"
                    "Use Next.js/React/TypeScript with Node22 and pnpm. "
                    "Create src/app/page.tsx and real product tests. Install needed "
                    "libraries/tools in the project machine; extend .omnia/cell.json "
                    "only for necessary helpers. The trusted MAX boundary remains "
                    "platform-owned; no product UI is supplied.\n\n" + _max_product_contract
                )
            # The final envelope below restores the proven 40-turn MAX
            # single pass after this branch assembles the product prompt.
            _agent_steps = 30
        else:
            _agent_user = (
                f"Собери приложение по запросу пользователя:\n\n{prompt_text}\n\n"
                f"Тип проекта: {project_info.template}.{_seed_block}\n\n"
                f"Действуй: объяви нужные entities/<Name>.json, напиши страницы "
                f"(включая обязательный dashboard/page.tsx индекс), затем build и "
                f"чини ошибки до чистоты, затем done. Минимизируй разведку — "
                f"раскладка выше уже дана."
            )
        _agent_system = stack.system
        _agent_steps = min(30, max(1, int(get_settings().agent_builder_max_steps)))
    if not _is_edit:
        _agent_steps = _agent_step_budget(
            project_info.template,
            configured_steps=_agent_steps,
        )
    # The historical cinematic pipeline uses one strong Opus model for
    # planning, implementation, visual review, and recovery. Operators
    # can still retune a role through ROLE_MODELS without a code deploy.
    _agent_model = model_for_role("agent", override=force_model)
    # Stall recovery resolves through the same role registry.
    _escalate_model = model_for_role("agent_escalation", override=force_model)
    _prompt_plan = AgentPromptPlan(
        system=_agent_system,
        user=_agent_user,
        model=_agent_model,
        escalate_model=_escalate_model,
        steps=_agent_steps,
        seed_context=_seed_block,
        stack_guide=stack.guide,
        stack_system=stack.system,
        skills=stack.skills,
        bare_stack=stack.bare_stack,
    )
    return _prompt_plan, _build_plan
