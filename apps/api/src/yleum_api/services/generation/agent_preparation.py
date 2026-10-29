from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.project import orchestrator_template
from yleum_api.services import agent_builder
from yleum_api.services.generation.agent_messages import (
    _is_continue_request,
    _is_retry_request,
    _recover_max_resume_prompt,
)
from yleum_api.services.generation.contracts import (
    AgentTurnClassification,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)
from yleum_api.services.generation.retry_request import resolve_request_source
from yleum_api.services.generation.runtime import _build_agent_seed_parts

_log = logging.getLogger("yleum_api.routers.messages")


async def classify_agent_turn(
    *,
    baseline: SourceBaseline,
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
    orchestrate: bool,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
) -> AgentTurnClassification:
    _has_prior_build = baseline.snapshot_id is not None
    _is_continue = _has_prior_build and _is_continue_request(prompt_text)
    _is_edit = (not orchestrate) and not _is_continue
    _max_has_generated_snapshot = False
    async with factory() as _max_history_session:
        recover_retry = _is_retry_request(prompt_text)
        if not recover_retry:
            request = await _max_history_session.get(Message, ids.user_message_id)
            if (
                request is not None and isinstance(request.content, str)
                and _is_retry_request(request.content)
            ):
                run = await _max_history_session.get(GenerationRun, ids.run_id)
                # A checkpoint may already have supplied its full saved brief.
                # Only unmarked manual retries replace expanded admission text.
                recover_retry = (
                    run is not None and isinstance(run.agent_state, dict)
                    and "source_checkpoint_resume" not in run.agent_state
                )
        if recover_retry:
            source = await resolve_request_source(_max_history_session, ids=ids, baseline=baseline)
            if source.message_id != ids.user_message_id:
                prompt_text = source.prompt
        _max_has_generated_snapshot = bool(
            await _max_history_session.scalar(
                select(func.count(Snapshot.id)).where(
                    Snapshot.project_id == ids.project_id,
                    Snapshot.prompt_text.is_not(None),
                    func.length(func.trim(Snapshot.prompt_text)) > 0,
                )
            )
        )
        if _is_continue and not _max_has_generated_snapshot:
            _prior_max_prompts = list(
                (
                    await _max_history_session.scalars(
                        select(Message.content)
                        .where(
                            Message.project_id == ids.project_id,
                            Message.role == "user",
                            Message.id != ids.user_message_id,
                        )
                        .order_by(Message.created_at.desc())
                        .limit(20)
                    )
                ).all()
            )
            _recovered_max_prompt = _recover_max_resume_prompt(_prior_max_prompts)
            if _recovered_max_prompt:
                prompt_text = _recovered_max_prompt
                _is_continue = False
                _is_edit = False
                print(
                    "[PP] MAX resume recovered original brief from history",
                    flush=True,
                )

    return AgentTurnClassification(prompt_text, _is_continue, _is_edit, _max_has_generated_snapshot)


async def _seed_saved_max_config(
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    *,
    catalog_runtime: bool,
) -> str | None:
    """Deliver authoritative public metadata before any model/runtime work."""
    from yleum_api.core.errors import ApiError
    from yleum_api.services.max_content_delivery import public_business_config
    from yleum_api.services.max_managed_generation import load_saved_business_config
    from yleum_api.services.max_project_kit import render_max_managed_files

    config, version = await load_saved_business_config(
        factory, project_id=ids.project_id, owner_id=ids.user_id,
        default_name=project_info.name if catalog_runtime else None,
    )
    if config is None:
        return None
    if catalog_runtime:
        seed = getattr(runtime.handle, "seed_business_config", None)
        if seed is None:
            raise ApiError(
                "orchestrator_unavailable", "Не удалось подготовить каталог приложения", 503,
            )
        await seed(version, public_business_config(config))
    files = render_max_managed_files(config, ids.project_id)
    if config.management_modules is not None:
        # Only an explicit new-app choice creates this privilege contract.
        # Stage it through the same generation lease as every other SDK file.
        if runtime.handle is None:
            raise ApiError(
                "orchestrator_unavailable", "Не удалось подготовить управление приложением", 503,
            )
        await runtime.handle.stage_patch(
            {".omnia/management.json": files[".omnia/management.json"]}, (),
        )
    return files["src/lib/omnia/max-config.ts"]


async def prepare_stack_prompt(
    *,
    _design_contract: Any,
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
    orchestrate: bool,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    runtime: GenerationRuntime,
) -> StackPrompt:
    _orch_name = orchestrator_template(project_info.template)
    _catalog_runtime = _orch_name == "max-miniapp-nextjs"
    _saved_max_config_source = await _seed_saved_max_config(
        factory, ids, project_info, runtime, catalog_runtime=_catalog_runtime,
    )
    _seed_parts = (
        await _build_agent_seed_parts(
            runtime.handle,
            refresh_managed_sdk=True,
            max_config_source=_saved_max_config_source,
            catalog_runtime=_catalog_runtime,
        )
        if runtime.handle is not None
        else []
    )
    _seed_block = (
        (
            "\n\nPROJECT CONTEXT (already gathered — do NOT re-explore these):\n"
            + "\n\n".join(_seed_parts)
        )
        if _seed_parts
        else ""
    )
    if project_info.memory_context:
        _seed_block = _seed_block + "\n\n" + project_info.memory_context
    if project_info.restoration_context:
        _seed_block += project_info.restoration_context
    # Per-project DESIGN MOOD — make every app look UNIQUE instead of the
    # baked dark zinc/indigo template («дизайн всегда одинаковый»). Seeded
    # curated palette + font + density fed into the BUILD prompt so the
    # agent writes distinct UI; works even on the hardcoded realtime
    # template where CSS-token injection is inert. Build only (orchestrate),
    # not surgical edits — a follow-up must not re-theme. Flag-gated, fail-soft.
    if _design_contract:
        _seed_block = _seed_block + "\n\n" + _design_contract.prompt_block
    elif orchestrate and get_settings().use_design_mood:
        try:
            from yleum_api.services.design_dna import design_mood_directive

            _seed_block = _seed_block + design_mood_directive(
                str(ids.project_id), industry_hint=prompt_text
            )
            print("[PP] design_mood injected", flush=True)
        except Exception as _dm_exc:
            print(f"[PP] design_mood skipped: {_dm_exc!r}", flush=True)
    # Per-stack agent prompt: entities keep their finely-tuned prompt; any
    # OTHER container stack (realtime, …) gets the shared LOOP_PROTOCOL + its
    # own SYSTEM_PROMPT.md, so the agent builds it with the right primitives
    # (e.g. the realtime hub + members ACL) instead of the entity guide.
    # Bare / no-stack experiment: the agent picks its OWN framework, so the
    # Next-specific `build` typecheck (require_green_before_done) does not
    # apply — completion is proven by runtime_check/probe instead.
    _bare_stack = _orch_name == "bare-nextjs"
    _stack_guide = (
        agent_builder.load_stack_system_prompt(_orch_name)
        if _orch_name and _orch_name != "nextjs-entities"
        else None
    )
    from yleum_api.services.max_project_kit import MAX_MODEL_DIRECTIVE

    _stack_guide = f"{_stack_guide or ''}\n\n{MAX_MODEL_DIRECTIVE}".strip()
    from yleum_api.services.integration_generation import generation_context

    async with factory() as _integration_session:
        _integration_guide = await generation_context(_integration_session, ids.project_id)
    from yleum_api.services.max_data_evolution import build_max_agent_guide

    _stack_guide = await build_max_agent_guide(
        _stack_guide,
        runtime.handle,
        integration_guide=_integration_guide,
    )
    # K1 knowledge layer: inject the stack's .omnia/skills (security/a11y/
    # perf canons aligned with the gates) when enabled. None → unchanged.
    _skills = (
        agent_builder.load_stack_skills(_orch_name) if get_settings().use_skill_injection else None
    )
    _stack_system = (
        agent_builder.build_system_prompt(_stack_guide, skills=_skills)
        if _stack_guide
        else agent_builder.SYSTEM_PROMPT
    )
    # Thin-base step 1 (owner «шаблоны накрывают дизайн»): on realtime the
    # agent kept the baked dark globals.css + layout → every chat looked
    # identical. Make it OWN the visual shell — rewrite its own globals.css
    # + layout in the design mood — while the realtime/auth/db primitives
    # stay LOCKED (import, never rewrite). Full realtime build only; the
    # extra files lean harder on the model (accepted — loop is hardened).
    if orchestrate and _orch_name == "nextjs-realtime" and get_settings().use_design_mood:
        _seed_block = _seed_block + (
            "\n\nТЫ ВЛАДЕЕШЬ ВСЕМ ВИЗУАЛОМ. Перепиши src/app/globals.css и "
            "src/app/(app)/layout.tsx ПОЛНОСТЬЮ под дизайн-настроение выше — НЕ "
            "оставляй дефолтный тёмный #0a0a0a вид шаблона: задай свой фон, "
            "типографику, плотность; оформи шапку, список бесед и пузыри "
            "сообщений в этом настроении.\n"
            "⚠️ src/app/(app)/layout.tsx — это ВЛОЖЕННЫЙ layout. НЕ пиши в нём "
            "`<html>`, `<head>` ни `<body>` — они уже есть в КОРНЕВОМ "
            "src/app/layout.tsx (он ЗАПЕРТ, не трогай). Верни ТОЛЬКО внутреннюю "
            "оболочку — `export default function AppLayout({children}) { return "
            '(<div className="..."><header>…шапка/нав…</header><main>{children}'
            "</main></div>); }`. Дубль `<html>`/`<body>` ломает гидрацию React и "
            "убивает реалтайм (сообщения перестают приходить).\n"
            "ГОТОВЫЕ ПРИМИТИВЫ (переиспользуй ПО УМОЛЧАНИЮ — auth/realtime/"
            "channels/db/ACL уже рабочие, не изобретай свои копии, это экономит "
            "шаги): src/lib/realtime/*, src/lib/channels.ts, src/lib/session.ts, "
            "src/lib/auth/*, src/lib/db/*, src/app/api/*. НО ты можешь и ПРАВИТЬ "
            "их, если чинишь баг или добавляешь фичу — функциональный гейт "
            "перепроверит, что доставка сообщений и 403-для-чужого живы. "
            "Единственное исключение: НЕ дублируй <html>/<body> в "
            "src/app/layout.tsx (корневой) — это ломает гидрацию и реалтайм."
        )
    return StackPrompt(_seed_block, _orch_name, _stack_guide, _skills, _stack_system, _bare_stack)
