from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import (
    get_settings,
    model_for_role,
)
from yleum_api.services import agent_builder
from yleum_api.services.build_plan import BuildPlan, max_request_build_plan, max_request_checklist
from yleum_api.services.generation.agent_messages import _agent_step_budget
from yleum_api.services.generation.contracts import (
    AgentPromptPlan,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    StackPrompt,
)
from yleum_api.services.portable_cell_contract import MAX_COORDINATOR_HANDOFF

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
    if (_is_edit or _is_continue) and runtime.handle is not None:
        from yleum_api.services.typography import existing_typography_context

        _seed_block = existing_typography_context(
            _seed_block, await runtime.handle.snapshot_files(),
        )
    coordinator_handoff = MAX_COORDINATOR_HANDOFF in stack.system
    if _is_continue:
        continuation_context = (
            "Продолжи MAX Mini App с сохранённых исходников. Найди фактическую точку "
            "входа и маршруты в текущем дереве. Дополни отсутствующее по запросу, "
            "сохрани работающие сценарии и данные, затем выполни проверки текущего режима."
            if project_info.template == "max_miniapp"
            else "Приложение частично собрано. Посмотри текущие файлы, допиши недостающие "
            "entities/<Name>.json и страницы, включая обязательный dashboard/page.tsx "
            "индекс. Почини ошибки сборки и закончи работу."
        )
        _agent_user = (
            f"{continuation_context}\n\nДоп. пожелание пользователя: {prompt_text}{_seed_block}"
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
            "проекта. Проверь текущую физическую схему. Адаптируй код и его запросы "
            "к существующей схеме без SQL-миграций, изменений схемы или данных. "
            "Сохраняй строки, скрытые поля и границы владельцев.\n\n"
            # Живой случай 23.09: вместе со старой версией вернулись её тесты,
            # а они утверждали про базу то, чего там уже нет («заметка обязательна»).
            # Правило «только additive» вернуть такое ограничение не даёт, и
            # адаптация объявила себя неудачной, хотя код переписала верно.
            "Проверки и тесты, приехавшие со старой версией, — тоже её код. Если тест "
            "утверждает про базу то, чего в ней уже нет, перенеси это требование в само "
            "приложение (проверка при записи) и убери из теста утверждение про схему. "
            "Требование при этом НЕ выбрасывай и схему под него НЕ переделывай: ослабить "
            "проверку вместо переноса — это потеря смысла старой версии. "
            "Затем выполни проверки завершения текущего режима."
        )
        _agent_system = stack.system
        _agent_steps = min(40, get_settings().agent_builder_max_steps)
    elif _is_edit:
        targets = [
            {**element, "html": str(element.get("html") or "")[:360]}
            for element in selected_elements or []
        ]
        _sel_block = (
            "\n\nПользователь выделил элементы в превью. Меняй только эти цели: "
            + json.dumps(targets, ensure_ascii=False)
            if targets else ""
        )
        _agent_user = (
            f"Внеси ТОЧЕЧНОЕ изменение в существующее приложение по "
            f"запросу:\n\n{prompt_text}\n{_sel_block}{_seed_block}\n\n"
            f"Найди нужный файл (grep/read), внеси МИНИМАЛЬНУЮ правку "
            f"(edit_file/write_file) и выполни проверки текущего режима. НЕ зацикливайся "
            f"на чтении — как только нашёл причину, СРАЗУ пиши правку (а не ещё "
            f"один read). Если ошибка указывает на бандл (src_*.js) — найди "
            f"реальный исходник в src/ по симптому. Не пересобирай работающее."
        )
        _agent_system = agent_builder.build_edit_system_prompt(
            stack.guide, coordinator_handoff=coordinator_handoff,
        )
        # Budget above the no-write/stall thresholds so the loop's
        # escalate-to-stronger-model actually fires before max_steps when a
        # cheap model explores without writing (the "Починить" did nothing bug).
        _agent_steps = 18
        if (
            project_info.template == "max_miniapp"
            and runtime.handle is not None
            and runtime.handle.capabilities.get("portable_machine") is True
            and runtime.handle.is_portable()
        ):
            # A portable edit must finish its UI, persistence and verification
            # in the same pass. Keep a floor without opening extra segments.
            _agent_steps = min(40, max(30, int(get_settings().agent_builder_max_steps)))
    else:
        from yleum_api.services.max_generation_contract import (
            build_max_product_contract,
            is_api_only_request,
        )

        _portable_capable = (
            runtime.handle is not None
            and runtime.handle.capabilities.get("portable_machine") is True
            and runtime.handle.is_portable()
        )
        _max_product_contract = build_max_product_contract(
            prompt_text, portable=_portable_capable, coordinator_handoff=coordinator_handoff,
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
        if is_api_only_request(prompt_text):
            _agent_user = (
                f"Implement the API requested by the owner:\n\n{prompt_text}\n\n"
                f"{_seed_block}\n\n{_max_product_contract}"
            )
        elif _portable_capable:
            _agent_user = (
                "Build the complete MAX product requested by the user:\n\n"
                f"{prompt_text}\n\n{_seed_block}\n\n"
                "Use Next.js/React/TypeScript with Node22 and pnpm. "
                "Create src/app/page.tsx and real product tests. Install needed "
                "libraries/tools in the project machine; extend .omnia/cell.json "
                "only for necessary helpers. The trusted MAX boundary remains "
                "platform-owned; no product UI is supplied.\n\n" + _max_product_contract
            )
        _agent_system = stack.system
        _agent_steps = min(30, get_settings().agent_builder_max_steps)
    if project_info.template == "max_miniapp":
        _agent_user += max_request_checklist(max_request_build_plan(prompt_text))
        _agent_user += (
            "\n\n<product_delivery>\n"
            "Complete the requested product or edit before handing it off; do not stop "
            "after planning or a partial implementation. Work on one requested behavior "
            "at a time, using the current source and observed tool results as ground truth. "
            "A context window is not the whole task: retain working features and continue "
            "from the supplied checkpoint rather than restarting. If a verification fails, "
            "diagnose its concrete output and change the relevant implementation; repeating "
            "the same command, oscillating between earlier versions or adding unrelated "
            "files is not progress. Do not stop to ask whether to continue when the next "
            "safe step is already covered by the request. Once all requested behaviors "
            "are implemented, hand off for the platform's final checks; do not invent "
            "extra features or declare an unverified product ready. For requested interface work, "
            "choose one visual direction appropriate to the audience, with deliberate "
            "typography, hierarchy, spacing and accent colors. For a new interface or "
            "an explicitly requested redesign, establish a compact design brief before "
            "implementation: the primary user action, 4-6 named palette values, "
            "typography roles and the main screen's layout hierarchy. Follow the "
            "owner's supplied visual brief; make choices specific to this product "
            "rather than adding decorative sections or a generic dashboard. Use system or "
            "bundled local fonts by default. When typography.json exists, preserve its "
            "versioned decision, pinned dependencies and typography.css on ordinary edits "
            "and restoration; an explicit owner redesign may change them together. "
            "Use an available local or owner-supplied asset for a custom font request and retain "
            "a system fallback when the asset is unavailable. Keep fonts local. Preserve "
            "required MAX SDK loading. On edits preserve the "
            "existing style, working features and existing business records unless the "
            "owner requests changes. Set explicit foreground/background pairs for "
            "product surfaces using consistent --app-* tokens. Check primary/secondary "
            "text, inputs, buttons and loading/empty/error states in the MAX theme; "
            "normal text needs at least 4.5:1 contrast. Inspect inherited CSS instead "
            "of applying a blanket color override to all controls. Use real available "
            "build/type checks; fix concrete errors before completion. Inspect the "
            "rendered result when the available tools support it. A source check or "
            "HTTP 200 is not a visual inspection: report visual behavior as unverified "
            "until actually observed.\n"
            "For requested UI backed by custom routes, read the actual custom route "
            "handlers and normalizers before implementing or editing the UI. Use shared "
            "typed DTOs or an explicit validated mapper reused across UI/API; follow the "
            "exact request and response shapes, including history display. For requested "
            "writes, follow the route's operation-key contract: a stable operation key "
            "per user intent, preserved for an explicit unknown-outcome retry, and a "
            "fresh key for a new intent. Do not invent field names or retry automatically. "
            "Use the authoritative response; show failed writes visibly and roll back "
            "or reconcile optimistic state. Local optimistic state is not persisted "
            "success. Test the actual UI payload through the route and its response "
            "mapping, then write -> read/reload and matching history values. SQL-only "
            "persistence checks cannot replace this client-route contract check.\n"
            "For requested fixed navigation, preserve the managed platform footer and "
            "its links. Reserve layout space, stacking order and safe-area insets so "
            "content and touch targets remain unobstructed at mobile viewport sizes. "
            "Inspect actual overlap and hit targets when available. Do not hide the "
            "footer or apply global text overrides to work around overlap. Honor API-only "
            "and read-only requests. Read-only means no writes; API-only means no extra "
            "UI changes. For requested writes, contract tests may use isolated disposable "
            "test records in the project's own local test environment: no external "
            "business actions; never change existing or unrelated data and never "
            "truncate the database.\n"
            "</product_delivery>"
        )
    _agent_user += (
        "\n\nREQUEST COVERAGE: identify each explicit requirement in the owner's "
        "request, including constraints, before editing. Before done, check every "
        "requirement against the actual implementation and relevant tests within "
        "this same turn budget. An API-only change does not fulfill requested "
        "rendered UI behavior, field display, accessibility or styling. A helper "
        "that is never used by the relevant screen is not an implementation. "
        "For requested UI controls, give the actual interactive element the exact "
        "requested label as its accessible name. For selects, use a separate label "
        "linked by htmlFor/id or an explicit aria-label so option text does not "
        "become part of that name. When tools support rendered-control inspection, "
        "check the actual controls by role and exact accessible name; source or "
        "unit checks alone do not prove browser behavior. Otherwise report this "
        "rendered check as unverified. "
        "A green build does not prove these requirements. Fix missing requested "
        "pieces before done; report any remaining unverified or platform-managed "
        "requirement honestly. Do not add requirements or change unrelated behavior. "
        "Honor API-only and read-only requests; do not force UI or source changes "
        "when they were not requested. Do not weaken existing tests to get green, "
        "bypass managed-file protections or perform external business writes "
        "as a verification shortcut."
    )
    if not _is_edit or project_info.restoration_context:
        _agent_steps = _agent_step_budget(
            project_info.template,
            configured_steps=_agent_steps,
        )
    # The orchestration registry selects planning, implementation and recovery.
    # Operators
    # can still retune a role through ROLE_MODELS without a code deploy.
    _agent_model = model_for_role("agent", override=force_model)
    # Stall recovery resolves through the same role registry.
    _escalate_model = model_for_role("agent_escalation", override=force_model)
    from yleum_api.services.max_behavior_proof import freeze_for_turn, required_contract
    from yleum_api.services.max_behavior_ui_contract import named_ui_guidance

    coordinator = getattr(runtime, "coordinator", None)
    named_contract = (
        await freeze_for_turn(coordinator, prompt_text, template=project_info.template)
        if coordinator is not None
        else required_contract(prompt_text, template=project_info.template)
    )
    # Use the authoritative frozen request for controller-backed turns, including
    # synthesized adaptation context. This same immutable plan is used by repair.
    guidance = named_ui_guidance(named_contract)
    if guidance:
        _agent_user += "\n\n" + guidance
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
    # Keep the legacy probe slot empty. MAX checks the same request-derived plan
    # at source completion, including durable continuations and coordinator proof.
    return _prompt_plan, None
