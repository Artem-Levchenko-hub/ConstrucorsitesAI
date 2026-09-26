from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.project import orchestrator_template
from yleum_api.services import agent_builder
from yleum_api.services.build_plan import BuildPlan, merge_plan_into_spec
from yleum_api.services.generation.agent_messages import (
    _is_continue_request,
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
    _saved_max_config_source = None
    from yleum_api.models.max_project_config import MaxProjectConfig
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_managed_files

    async with factory() as _config_session:
        _saved_max_record = await _config_session.get(MaxProjectConfig, ids.project_id)
    if _saved_max_record is not None:
        _saved_max_config_source = render_max_managed_files(
            MaxProjectConfigPayload.model_validate(_saved_max_record.config),
            ids.project_id,
        )["src/lib/omnia/max-config.ts"]
    _seed_parts = (
        await _build_agent_seed_parts(
            runtime.handle,
            refresh_managed_sdk=True,
            max_config_source=_saved_max_config_source,
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
    _orch_name = orchestrator_template(project_info.template)
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
    _stack_guide += "\n\n" + _integration_guide
    from yleum_api.services.max_data_evolution import build_max_agent_guide

    _stack_guide = await build_max_agent_guide(
        _stack_guide,
        runtime.handle,
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


async def persist_build_plan(
    factory: async_sessionmaker[AsyncSession], project_id: UUID, plan: BuildPlan
) -> None:
    """Persist a completed model plan in a fresh transaction."""
    async with factory() as session:
        project = await session.get(Project, project_id)
        if project is not None:
            project.discovery_spec = merge_plan_into_spec(project.discovery_spec, plan)
            await session.commit()
