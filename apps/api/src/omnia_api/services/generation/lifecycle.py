from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.redis import (
    clear_stream_state,
    publish_event,
)
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services import (
    agent_builder,
    stack_routing,
)
from omnia_api.services import repo as repo_svc
from omnia_api.services.generation.agent_pipeline import run_agent_generation
from omnia_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.progress import GenerationProgress, MessageStream
from omnia_api.services.generation.publication import consume_free_generation
from omnia_api.services.generation.streamed_pipeline import run_streamed_generation
from omnia_api.services.generation_runs import set_generation_run_status
from omnia_api.services.llm_client import set_free_generation
from omnia_api.services.preset_classifier import classify_preset
from omnia_api.services.project_memory import render_project_memory_context
from omnia_api.services.prompt_builder import KIT_FILES


async def _process_prompt(
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
    current_snapshot_id: UUID | None,
    prompt_text: str,
    model_id: str,
    force_model: str | None = None,
    is_free: bool = False,
    free_business_id: UUID | None = None,
    orchestrate: bool = True,
    selected_elements: list[dict[str, Any]] | None = None,
    capacity_dispatch_token: UUID | None = None,
) -> None:
    import logging as _log_mod

    _log = _log_mod.getLogger("omnia_api.routers.messages")
    # Mark this async context free (gateway skips wallet debit) for the whole
    # generation — the contextvar rides every stream_chat_completion call below.
    set_free_generation(is_free)
    # `model_id` is the routing model; it gets reassigned to the effective model
    # when an empty-response fallback fires. Keep the original for the snapshot
    # label decision (orchestrated vs forced-model run).
    routing_model = model_id
    # Surgical EDIT mode — a cheap, scoped <edit> patch that must NOT regenerate
    # the page or re-roll the palette. The triage's CHEAP verdict (orchestrate=
    # False) on an existing project means "edit just this thing"; the flag is the
    # R-10 kill switch back to the old full-build-prompt behaviour. When on, we
    # build a lean edit-only prompt AND skip the full-build guards below
    # (palette / contrast / signature-floor / acceptance / dead-link re-roll) so
    # nothing outside the requested change can drift.
    surgical = (not orchestrate) and get_settings().use_surgical_edit
    # Imported foreign repos are NEVER regenerated from scratch — only surgically
    # edited. Force surgical here so the entire build pipeline (palette/contrast/
    # acceptance/vision/dead-link/kit-injection), which assumes an Omnia-authored
    # template, is bypassed. project_is_imported is loaded from the DB below; we
    # also apply it again after loading for defense-in-depth.
    # NOTE: project_is_imported is initialised False above and set from the DB
    # in the context-load block. The second enforcement below (after DB load)
    # is the real gate. This first assignment is a no-op until we reload.
    print(
        f"[PP] start project={project_id} asst_msg={assistant_message_id} "
        f"model={model_id} free={is_free} force={force_model} surgical={surgical}",
        flush=True,
    )

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)

    _consume_free_generation = partial(
        consume_free_generation, is_free=is_free, free_business_id=free_business_id, user_id=user_id
    )

    # Persisted agentic transcript: every `agent.step` payload published this turn
    # is appended and saved immediately so reload restores completed work while
    # the same server-side run continues.

    _progress = GenerationProgress(factory, run_id, project_id, assistant_message_id)
    _record_agent_step = _progress.record_agent_step

    # Resumable-stream accumulator. `_run_stream` resets its per-pass
    # `state["accumulated"]` on every fallback re-run, but the CLIENT's content
    # is the concatenation of every delta ever published for this message — so
    # the Redis buffer (used to resync a reconnecting client) must mirror THAT,
    # not the per-pass slice. `seq` is monotonic across the whole message life.
    pub = MessageStream()
    current_sha: str | None = None
    current_files: dict[str, str] = {}
    history_serialized: list[dict[str, str]] = []
    project_template = "blank"
    project_slug = ""
    project_name = ""
    project_design_preset_id: str | None = None
    project_image_gen_enabled: bool = True
    project_discovery_spec: dict[str, object] | None = None
    project_memory_context = ""
    restoration_adaptation_context = ""
    project_language: str = "ru"
    project_is_imported: bool = False
    runtime = GenerationRuntime()
    runtime.handle = None
    runtime.coordinator = None
    runtime.deadline_task = None

    async def _provision_legacy_runtime_with_progress() -> None:
        await _record_agent_step(
            {
                "step": None,
                "kind": "step",
                "action": "Подготавливаю среду проекта",
                "tool": "runtime",
                "path": "",
                "detail": "Запускаю контейнер и жду готовности перед сборкой.",
                "ok": True,
            }
        )
        await stack_routing.ensure_provisioned(
            project_id,
            project_slug,
            project_template,
            require_ready=True,
        )
        await _record_agent_step(
            {
                "step": None,
                "kind": "step",
                "action": "Среда готова",
                "tool": "runtime",
                "path": "",
                "detail": "Контейнер запущен, начинаю сборку приложения.",
                "ok": True,
            }
        )

    try:
        async with factory() as session:
            from omnia_api.services.restoration_adaptation import append_adaptation_context

            restoration_adaptation_context = await append_adaptation_context(
                session,
                run_id,
                project_id,
                user_id,
                current_snapshot_id,
                "",
            )
            if current_snapshot_id:
                snap = await session.get(Snapshot, current_snapshot_id)
                if snap is not None:
                    current_sha = snap.commit_sha
            proj = await session.get(Project, project_id)
            if proj is not None:
                project_template = proj.template
                project_slug = proj.slug
                project_name = proj.name or ""
                project_design_preset_id = proj.design_preset_id
                project_image_gen_enabled = proj.image_gen_enabled
                project_discovery_spec = proj.discovery_spec
                project_language = getattr(proj, "language", None) or "ru"
                project_is_imported = bool(getattr(proj, "source", "native") == "imported")
            res = await session.execute(
                select(Message)
                .where(Message.project_id == project_id)
                .where(Message.id != assistant_message_id)
                .order_by(Message.created_at.desc())
                # Load 40 so the writer's HISTORY_LIMIT (12 turns) is the real
                # control — long threads (discovery Q&A + follow-up tweaks) aren't
                # pre-truncated here (owner 2026-06-18: «реально понимай чат»).
                .limit(40)
            )
            rows = list(reversed(list(res.scalars().all())))
            history_serialized = [{"role": m.role, "content": m.content} for m in rows if m.content]
            if get_settings().use_project_memory:
                project_memory_context = await render_project_memory_context(session, project_id)
        print(f"[PP] ctx_loaded sha={current_sha} history={len(history_serialized)}", flush=True)

        # B4 — imported repos are ALWAYS surgical-edit only.  We enforce this
        # here (after the DB load sets project_is_imported) regardless of the
        # triage result: the build pipeline (palette/contrast/acceptance/vision/
        # dead-link/kit-injection) assumes an Omnia-authored template and must
        # NEVER touch foreign source code.
        if project_is_imported:
            surgical = True
        print(f"[PP] imported={project_is_imported} surgical_final={surgical}", flush=True)

        _agentic_container_turn = (
            agent_builder.is_agentic_enabled(
                get_settings().use_agentic_builder,
                get_settings().agentic_builder_canary_users,
                str(user_id),
            )
            and project_template in CONTAINER_NEXT
            and bool(project_slug)
            and not project_is_imported
            and (orchestrate or surgical)
        )
        _defer_max_runtime_provision = _agentic_container_turn and project_template == "max_miniapp"

        # Auto stack-routing, part 2: container-backed stacks need a live dev
        # container for the post-build hot_reload to land in. Provision it now —
        # at the START of the worker — so it warms up in parallel with the
        # (minutes-long) generation below. Idempotent + fail-soft: if the
        # container already exists this is a no-op; if the orchestrator hiccups
        # the build still ships the snapshot and hot_reload/«Запустить» retries.
        # Imported repos are never container-backed (detect_template returns
        # "blank"/"code", neither is in CONTAINER_NEXT), but gate defensively.
        if (
            project_template in CONTAINER_NEXT
            and project_slug
            and not project_is_imported
            and not _defer_max_runtime_provision
        ):
            await _provision_legacy_runtime_with_progress()

        if current_sha:
            current_files = await asyncio.to_thread(repo_svc.read_files, project_id, current_sha)
        print(f"[PP] files_loaded count={len(current_files)}", flush=True)

        # Kit files are Omnia-managed infra — keep them out of the model's context
        # (saves tokens and stops the model rewriting them from what it "saw").
        # Imported projects: do NOT strip kit files from context — the imported
        # repo has no kit, so the filter is a no-op anyway, but we skip it to
        # avoid any accidental mutation of the foreign files dict.
        if not project_is_imported:
            current_files = {p: c for p, c in current_files.items() if p not in KIT_FILES}

        # Auto-classify design preset on first prompt if not set yet.
        # Heuristic is sync+cheap; LLM-fallback (Haiku, ~150 tokens) only fires
        # if heuristic is ambiguous. Cached in projects.design_preset_id forever.
        if not project_design_preset_id and project_template != "max_miniapp":
            try:
                project_design_preset_id = await classify_preset(
                    project_name=project_name,
                    template=project_template,
                    first_prompt=prompt_text,
                    # V2.5-override — the persisted onboarding chips tie-break the
                    # classifier's LLM-fallback (catalog+cart→retail, booking→
                    # services, tone disambiguates). Confident industry signal
                    # still wins first; this only acts on the ambiguous path.
                    discovery_spec=project_discovery_spec,
                )
                # Persist so subsequent prompts skip the classifier entirely.
                async with factory() as cls_session:
                    cls_proj = await cls_session.get(Project, project_id)
                    if cls_proj is not None and not cls_proj.design_preset_id:
                        cls_proj.design_preset_id = project_design_preset_id
                        await cls_session.commit()
                print(
                    f"[PP] preset_classified preset_id={project_design_preset_id}",
                    flush=True,
                )
            except Exception as cls_exc:
                _log.warning("preset classify failed: %r", cls_exc)
                project_design_preset_id = None

        # ── Agentic builder (Phase 0) ───────────────────────────────────────
        # When USE_AGENTIC_BUILDER is on, a container-app FIRST BUILD runs a real
        # plan→act→observe→verify agent loop instead of one-shot text→regex: the
        # model reads/writes files in the live dev container, runs a real
        # typecheck, sees the actual errors and iterates until clean. Isolated
        # early-return path that reuses the standard commit/snapshot/finalise
        # helpers. When the flag is OFF this block is skipped and generation runs
        # exactly as before (byte-identical).
        ids = GenerationIds(run_id, project_id, user_id, user_message_id, assistant_message_id)
        project_info = ProjectGenerationFacts(
            template=project_template,
            slug=project_slug,
            name=project_name,
            design_preset_id=project_design_preset_id,
            discovery_spec=project_discovery_spec,
            image_gen_enabled=project_image_gen_enabled,
            language=project_language,
            is_imported=project_is_imported,
            memory_context=project_memory_context,
            restoration_context=restoration_adaptation_context,
        )
        baseline = SourceBaseline(current_snapshot_id, current_sha, current_files)
        if _agentic_container_turn:
            await run_agent_generation(
                _consume_free_generation=_consume_free_generation,
                _defer_max_runtime_provision=_defer_max_runtime_provision,
                _provision_legacy_runtime_with_progress=_provision_legacy_runtime_with_progress,
                baseline=baseline,
                capacity_dispatch_token=capacity_dispatch_token,
                factory=factory,
                force_model=force_model,
                ids=ids,
                is_free=is_free,
                model_id=model_id,
                orchestrate=orchestrate,
                progress=_progress,
                project_info=project_info,
                prompt_text=prompt_text,
                runtime=runtime,
                selected_elements=selected_elements,
            )
            return
        await run_streamed_generation(
            _consume_free_generation=_consume_free_generation,
            baseline=baseline,
            factory=factory,
            force_model=force_model,
            history_serialized=history_serialized,
            ids=ids,
            orchestrate=orchestrate,
            project_info=project_info,
            prompt_text=prompt_text,
            pub=pub,
            routing_model=routing_model,
            model_id=model_id,
            runtime=runtime,
            selected_elements=selected_elements,
            surgical=surgical,
        )

    except Exception as e:
        import traceback as _tb

        print(
            f"[PP] FATAL project={project_id} asst={assistant_message_id} "
            f"err={e!r}\n{_tb.format_exc()}",
            flush=True,
        )
        # Mark the assistant row as failed so the UI input unblocks instead of
        # spinning forever; otherwise tokens_out stays NULL and ChatPanel
        # treats the message as still-streaming.
        try:
            await set_generation_run_status(run_id, "failed", error=str(e))
            async with factory() as session:
                m = await session.get(Message, assistant_message_id)
                if m is not None and m.tokens_out is None:
                    m.content = f"[Ошибка: {e}]"[:1000]
                    m.tokens_out = 0
                    m.tokens_in = 0
                    await session.commit()
        except Exception:
            import traceback as _tb2

            print(f"[PP] failure_marker_write_failed\n{_tb2.format_exc()}", flush=True)
        await publish_event(
            project_id,
            "llm.error",
            {"message_id": str(assistant_message_id), "error": str(e)},
        )
    finally:
        if runtime.deadline_task is not None:
            runtime.deadline_task.cancel()
            with suppress(asyncio.CancelledError):
                await runtime.deadline_task
        if runtime.handle is not None:
            try:
                release_task: asyncio.Future[None] = asyncio.ensure_future(runtime.handle.release())
                try:
                    await asyncio.shield(release_task)
                except asyncio.CancelledError:
                    # Keep the executor ownership lock until cleanup finishes;
                    # shield alone leaves an untracked release running behind us.
                    with suppress(Exception):
                        await release_task
                    raise
            except Exception as release_exc:
                _log.warning(
                    "Project Cell generation lease release failed",
                    exc_info=release_exc,
                )
        # Стрим завершён (done / error / отмена / краш) — снимаем горячее
        # состояние, чтобы reconnect после конца не пытался досматривать
        # мёртвый поток. Best-effort: ошибка Redis тут не должна валить ответ.
        try:
            await clear_stream_state(project_id, assistant_message_id)
        except Exception:
            pass
