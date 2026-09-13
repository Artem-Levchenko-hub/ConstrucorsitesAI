from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.redis import publish_event
from omnia_api.services.file_extractor import (
    UnsafePathError,
    clean_chat_content,
)
from omnia_api.services.generation.agent_messages import _failed_build_body
from omnia_api.services.generation.asset_composition import resolve_assets_and_app_composition
from omnia_api.services.generation.container_realization import realize_container_snapshot
from omnia_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.file_transforms import _extract_files_and_edits
from omnia_api.services.generation.progress import MessageStream
from omnia_api.services.generation.publication import _finalize_message
from omnia_api.services.generation.static_acceptance import accept_static_candidate
from omnia_api.services.generation.static_quality import (
    apply_final_quality_guards,
    apply_initial_quality_guards,
    repair_dead_links,
    retry_catalog_audit,
)
from omnia_api.services.generation.stream_candidate import (
    generate_initial_output,
    recover_incomplete_output,
    render_catalog_output,
)
from omnia_api.services.generation.stream_preparation import prepare_model_stream
from omnia_api.services.generation.stream_publication import publish_streamed_candidate
from omnia_api.services.generation.surgical_recovery import (
    bind_interactive_edit,
    recover_container_edit,
    recover_html_edit,
    recover_selected_zone_edit,
    retry_unmatched_or_conflicting_edits,
)

_log = logging.getLogger("omnia_api.routers.messages")


async def run_streamed_generation(
    *,
    _consume_free_generation: Callable[[AsyncSession], Awaitable[None]],
    baseline: SourceBaseline,
    factory: async_sessionmaker[AsyncSession],
    force_model: str | None,
    history_serialized: list[dict[str, str]],
    ids: GenerationIds,
    orchestrate: bool,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    pub: MessageStream,
    routing_model: str,
    model_id: str,
    runtime: GenerationRuntime,
    selected_elements: list[dict[str, Any]] | None,
    surgical: bool,
) -> None:
    stream = prepare_model_stream(
        baseline=baseline,
        force_model=force_model,
        history_serialized=history_serialized,
        ids=ids,
        model_id=model_id,
        orchestrate=orchestrate,
        project_info=project_info,
        prompt_text=prompt_text,
        pub=pub,
        selected_elements=selected_elements,
        surgical=surgical,
    )
    _gen_mode = stream.generation_mode
    _is_code = project_info.template == "code"

    # ── Direct image generation — call the GRAPHICS model, not the text LLM.
    # When the user points at a zone and asks for a picture, build a
    # guaranteed-matching <edit> server-side: SEARCH is the EXACT <img> from
    # source (so it always applies), REPLACE swaps it for a fresh
    # data-omnia-gen tag. image_resolver below turns it into a real flux
    # photo. The only LLM is a cheap image-prompt craft — no HTML rewrite,
    # no risk of the text model mangling the edit.
    (
        accumulated,
        usage_data,
        stream_error,
    ) = await generate_initial_output(
        baseline=baseline,
        force_model=force_model,
        ids=ids,
        model_id=model_id,
        project_info=project_info,
        prompt_text=prompt_text,
        selected_elements=selected_elements,
        stream=stream,
        surgical=surgical,
    )

    if stream_error:
        print(f"[PP] stream_error err={stream_error!r}", flush=True)
        # The llm.error event below is delivered ONLY over the live WS
        # stream. A user who reloaded or dropped the socket during the
        # (multi-minute) build would otherwise be left with a chat row
        # that is blank AND still looks "streaming" (tokens_out NULL),
        # with no clue WHY the build stopped. Persist a human-readable
        # error body (when the model streamed nothing) + zero tokens so
        # the row finalises — mirroring the crash-path recovery in
        # _emergency_error. Keep any partial content the model managed.
        err_body = _failed_build_body(accumulated, stream_error)
        await _finalize_message(
            factory,
            ids.assistant_message_id,
            err_body,
            usage_data or {"tokens_in": 0, "tokens_out": 0},
            snapshot_id=None,
        )
        await publish_event(
            ids.project_id,
            "llm.error",
            {"message_id": str(ids.assistant_message_id), "error": str(stream_error)},
        )
        return

    print(f"[PP] stream_complete acc_len={len(accumulated)} usage={usage_data}", flush=True)

    # Phase L3 — catalog/IR mode. The LLM emitted a `PageIR` JSON
    # object (not HTML); convert it to a `<file path="src/index.html">`
    # block here so the rest of the pipeline keeps working unchanged.
    # Fail-soft: if the JSON parse / Pydantic validation fails, log
    # and fall through to the freeform HTML extractor — the model
    # may have ignored the IR-only instruction and returned HTML
    # anyway. We'd rather ship an imperfect site than nothing.
    #
    # Tier guard: only premium models go through IR conversion.
    # Cheap models (Haiku/Nano) are routed through multipass which
    # emits HTML — parsing that as JSON would always fail and just
    # log noise.

    # Only catalog mode parses the answer as PageIR JSON. Freeform/plain
    # emit HTML in <file> blocks and fall straight through to the extractor.
    # Container-backed Next.js apps emit .tsx <file> blocks, not PageIR JSON —
    # never run the catalog/IR parser on them (it would discard the files and
    # try to render a static index.html for a React app).
    (accumulated, usage_data) = await render_catalog_output(
        accumulated=accumulated,
        baseline=baseline,
        ids=ids,
        project_info=project_info,
        usage_data=usage_data,
        stream=stream,
    )
    # Fall through: the empty-content retry machinery (multipass /
    # model-switch) downstream is the final fallback.

    try:
        files, edit_conflicts = _extract_files_and_edits(accumulated, baseline.files)
        if edit_conflicts:
            _log.warning("edit conflicts on first pass: %s", edit_conflicts[:5])
    except (UnsafePathError, ValueError) as e:
        # Unparsable answer — persist an honest note, not the raw model dump
        # (which would render as code / a lying chip in the chat on reload).
        _final_err = (
            clean_chat_content(
                accumulated,
                {},
                surgical=surgical,
                fallback="Не получилось разобрать ответ модели. Повтори запрос.",
            )
            if get_settings().use_clean_chat_content
            else accumulated
        )
        await _finalize_message(
            factory, ids.assistant_message_id, _final_err, usage_data, snapshot_id=None
        )
        await publish_event(
            ids.project_id,
            "llm.error",
            {"message_id": str(ids.assistant_message_id), "error": str(e)},
        )
        return

    # --- Surgical retry: edit produced no applicable patch ----------
    # In edit mode the model returns <edit> SEARCH/REPLACE. If the SEARCH
    # didn't match the file byte-for-byte (or the markers were dropped),
    # `files` is empty even though `accumulated` is long (so the truncation
    # fallback below won't fire). Re-ask ONCE with a "copy exact bytes" nudge
    # before giving up — cheaper and far less surprising than shipping a full
    # rewrite. Stays single-shot on the same cheap model.
    (
        files,
        accumulated,
        usage_data,
    ) = await retry_unmatched_or_conflicting_edits(
        accumulated=accumulated,
        baseline=baseline,
        edit_conflicts=edit_conflicts,
        files=files,
        force_model=force_model,
        stream=stream,
        surgical=surgical,
        usage_data=usage_data,
    )

    # --- Surgical rewrite fallback: <edit> still couldn't land --------
    # SEARCH/REPLACE is fragile on a cheap model for BLOCK-level changes
    # ("сделай фон поинтереснее" — the hero markup is complex, and the model
    # can't reproduce it byte-for-byte). Rather than ship "ничего не
    # поменялось", regenerate the WHOLE file applying ONLY the change, then
    # GUARD against drift: accept the rewrite only if the page's original
    # copy survived (a scoped edit), never a silent re-design. Static
    # (index.html) projects only — fullstack edits stay on <edit>.
    # 1) ZONE-scoped rewrite — preferred when the user pointed at a zone.
    # Rewrite ONLY the enclosing landmark block (<section>/<header>/<footer>)
    # and splice it back, so the rest of the page (other sections + their
    # images) stays byte-identical. This is the "выбрал секцию → меняю только
    # её, не переписываю весь сайт" path the owner asked for.
    (
        files,
        accumulated,
        usage_data,
    ) = await recover_selected_zone_edit(
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        force_model=force_model,
        ids=ids,
        prompt_text=prompt_text,
        selected_elements=selected_elements,
        surgical=surgical,
        usage_data=usage_data,
    )

    # 2) Whole-file rewrite — last resort (no selection, no zone found, or the
    # zone rewrite was rejected). Regenerates the WHOLE file; guarded by the
    # text-preservation ratio so a silent re-design is rejected.
    (
        files,
        accumulated,
        usage_data,
    ) = await recover_html_edit(
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        force_model=force_model,
        history_serialized=history_serialized,
        ids=ids,
        prompt_text=prompt_text,
        selected_elements=selected_elements,
        surgical=surgical,
        usage_data=usage_data,
        stream=stream,
    )

    # --- Orphaned-anchor repair on surgical edits ---------------------
    # The dead-link pass below is skipped on surgical edits (it must not
    # touch PRE-EXISTING dead links the user didn't mention). But a removal
    # edit ("убери секцию отзывов") orphans nav links — `href="#reviews"`
    # now points at a section this very edit deleted. That dangling anchor
    # IS part of "what was asked", so repair it: delta-scoped (only ids the
    # edit removed), deterministic, no LLM, no regeneration — squarely the
    # kept inline-href-fixer policy.
    files = bind_interactive_edit(
        _is_code=_is_code,
        baseline=baseline,
        files=files,
        project_info=project_info,
        surgical=surgical,
    )

    # 3) CONTAINER-app rewrite fallback — the <edit> couldn't land on a
    # React/Next/entities app, and the two static fallbacks above are gated
    # on index.html (which container apps don't have), so they never fired.
    # Without this branch a failed <edit> on an entity/fullstack/spa app
    # dead-ends with "ничего не изменилось" — exactly the owner's "сайт
    # сломан → починить через чат не выходит, просто перестаёт что-то
    # делать". Rewrite ONLY the file(s) the edit targeted (full-file, via the
    # reliable writer), guarded by a content-preservation ratio so a scoped
    # fix is told apart from a silent redesign. Kill: USE_CONTAINER_EDIT_REWRITE.
    (
        files,
        accumulated,
        usage_data,
    ) = await recover_container_edit(
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        force_model=force_model,
        history_serialized=history_serialized,
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        selected_elements=selected_elements,
        surgical=surgical,
        usage_data=usage_data,
        stream=stream,
    )

    # --- Pass 2..N: empty-content fallback ----------------------
    # If we got nothing usable back AND the primary model has known
    # fallbacks, transparently re-run against them. The user sees a
    # short inline notice in the chat (delivered as an llm.chunk) and
    # the assistant message ends up labeled with whichever model
    # actually produced the final answer.
    (
        files,
        accumulated,
        usage_data,
        effective_model,
    ) = await recover_incomplete_output(
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        force_model=force_model,
        ids=ids,
        model_id=model_id,
        stream=stream,
        usage_data=usage_data,
    )
    model_id = effective_model
    stream.model_id = effective_model

    # --- Pass N+1: dead-link repair (static only) -------------------
    # The prompt forbids dead links, but weaker models still slip in
    # href="#" placeholders. Two-tier repair:
    #   1) Server-side inline (FREE) — rewrites href="#" → "#contacts"
    #      (or first available CTA-class section) without any LLM call.
    #      Catches ~95% of cases.
    #   2) LLM re-roll (₽30+) — only fires if >3 dead links REMAIN after
    #      step 1. Previously fired on ANY single dead link, costing the
    #      user a full second generation per project. Now reserved for
    #      severely broken output the inline fixer can't salvage.
    # Skip on surgical edits: rewriting a PRE-EXISTING dead link the user
    # didn't mention violates "change only what was asked". The edit prompt
    # already forbids dead links in any element the edit adds.
    (
        files,
        accumulated,
    ) = await repair_dead_links(
        _is_code=_is_code,
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        force_model=force_model,
        ids=ids,
        project_info=project_info,
        stream=stream,
        surgical=surgical,
    )

    # Kit files are Omnia-managed: drop any model attempt to write/delete them,
    # and re-inject the kit <link>/<script> into returned HTML if the model
    # dropped them (so animations/interactivity never silently break).
    # Imported projects: skip entirely — their HTML is foreign and must not
    # have Omnia's kit injected (it would break the layout and leak branding).
    (
        files,
        report,
    ) = await apply_initial_quality_guards(
        _is_code=_is_code,
        files=files,
        ids=ids,
        project_info=project_info,
        surgical=surgical,
    )

    # Phase L6 — audit-driven retry loop (1 retry max). Triggers only
    # when catalog/IR mode is active AND the first audit scored
    # below the threshold AND we haven't already retried. We append
    # the prior accumulated response + a concrete failure list as a
    # user turn and re-stream against the same model. The retry
    # response goes through the same IR parse → render pipeline; if
    # it parses, it replaces `files` and downstream (image_resolver
    # / repo commit) sees the corrected page.
    (
        files,
        accumulated,
        usage_data,
    ) = await retry_catalog_audit(
        accumulated=accumulated,
        baseline=baseline,
        files=files,
        force_model=force_model,
        ids=ids,
        project_info=project_info,
        report=report,
        stream=stream,
        usage_data=usage_data,
    )

    (files,) = await resolve_assets_and_app_composition(
        factory=factory,
        files=files,
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        stream=stream,
        surgical=surgical,
    )

    # ── Phase 11 — acceptance gate (freeform safety net) ──────────────
    # Render → check structure + responsiveness (+ optional vision). If it
    # fails, re-roll with concrete feedback up to ACCEPTANCE_MAX_RETRIES.
    # If freeform still fails, fall back to the catalog/IR path (guaranteed
    # valid page). Runs after image-resolve so screenshots carry real
    # images. All best-effort — any gate error ships the current files.
    (
        files,
        accumulated,
        _acc_fingerprint,
    ) = await accept_static_candidate(
        accumulated=accumulated,
        baseline=baseline,
        effective_model=effective_model,
        files=files,
        force_model=force_model,
        history_serialized=history_serialized,
        ids=ids,
        project_info=project_info,
        prompt_text=prompt_text,
        selected_elements=selected_elements,
        stream=stream,
        surgical=surgical,
    )

    # Phase 12 — deterministic design guards on the FINAL HTML, right before
    # commit (so snapshot / GitHub export / rollback all carry the fixed
    # page). The freeform writer (any model) drifts off the seeded palette
    # and can ship unreadable text; nothing above enforces it (ui_audit only
    # scores). Order matters: palette FIRST (snap colours to the project's
    # curated palette), THEN contrast (guarantee body readability against the
    # snapped palette). Both pure + idempotent + fail-soft.
    # Skip on surgical edits: enforce_palette re-snaps :root/body colours to
    # the project's deterministic palette on EVERY commit — on an edit that
    # silently changes the background even though the model preserved it
    # (a model-independent source of "поменял фон на ужасный цвет"). The
    # existing page is the source of truth for an edit; only a full build
    # earns the guards.
    (files,) = await apply_final_quality_guards(
        files=files,
        ids=ids,
        project_info=project_info,
        stream=stream,
        surgical=surgical,
    )

    # Entity-schema corruption (an unparseable entities/*.json) is detected in the
    # CONTAINER_NEXT guards below and surfaced as a «schema» card after commit, so a
    # DOA section is loud + fixable instead of shipping silently under a «готово».
    if files:
        # A6a — restore the managed auth columns if the model dropped them
        # while rewriting the Drizzle schema, so signup/signin keep working.
        # Runs before commit + hot_reload so git and the container agree.
        publication = await publish_streamed_candidate(
            _acc_fingerprint=_acc_fingerprint,
            _consume_free_generation=_consume_free_generation,
            _gen_mode=_gen_mode,
            accumulated=accumulated,
            baseline=baseline,
            factory=factory,
            files=files,
            force_model=force_model,
            ids=ids,
            model_id=model_id,
            orchestrate=orchestrate,
            project_info=project_info,
            prompt_text=prompt_text,
            routing_model=routing_model,
            surgical=surgical,
            usage_data=usage_data,
        )

        # Fullstack projects: push the same files into the live dev
        # container so the user sees the new code immediately via HMR.
        # Failure here is logged + surfaced as a chat notice but does
        # NOT roll the snapshot back — the canonical state is still in
        # git/MinIO and the user can hit "Запустить" again to retry.
        await realize_container_snapshot(
            factory=factory,
            ids=ids,
            new_sha=publication.commit_sha,
            new_snapshot_id=publication.snapshot_id,
            project=publication.project,
            runtime=runtime,
            snapshot_model_id=publication.model_id,
            files=publication.files,
        )
    else:
        # Модель ответила, но ни одного <file path="...">...</file> в выводе.
        # Раньше тут была тишина — UI получал только llm.done и думал, что
        # всё ок, хотя preview не обновлялся. Теперь шлём явный llm.error,
        # чтобы юзер видел, что произошло, и сообщение в чате становится
        # видимым (а не висит «пустым» из-за пустого snapshot_id).
        # Surgical edit that didn't land (SEARCH didn't match) gets a friendly,
        # actionable hint — never the build-path "switch models" text (the user
        # has no model picker, and this is an edit, not a fresh build).
        if surgical:
            hint = (
                "Не получилось применить точечную правку. Уточни, что именно и "
                "где поменять (например: «увеличь заголовок в первой секции» или "
                "«поменяй текст кнопки на …»), и я попробую снова."
            )
        else:
            hint = (
                "Не получилось собрать страницу с первого раза. Попробуй "
                "переформулировать запрос или повторить — иногда помогает."
            )
        # Persist the honest hint, NOT the model's raw attempt: with no files
        # committed there's nothing to chip, and saving raw <edit>/code would
        # render a lying "Правка" chip or dump code into the chat on reload.
        _final_no_files = (
            clean_chat_content(accumulated, {}, surgical=surgical, fallback=hint)
            if get_settings().use_clean_chat_content
            else accumulated
        )
        await _finalize_message(
            factory,
            ids.assistant_message_id,
            _final_no_files,
            usage_data,
            snapshot_id=None,
        )
        await publish_event(
            ids.project_id,
            "llm.error",
            {"message_id": str(ids.assistant_message_id), "error": hint},
        )

    await publish_event(
        ids.project_id,
        "llm.done",
        {
            "message_id": str(ids.assistant_message_id),
            "tokens_in": int(usage_data["tokens_in"]) if usage_data else None,
            "tokens_out": int(usage_data["tokens_out"]) if usage_data else None,
            "cost_rub": float(usage_data["cost_rub"]) if usage_data else None,
        },
    )
