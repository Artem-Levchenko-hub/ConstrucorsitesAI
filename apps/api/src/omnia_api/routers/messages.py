from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import (
    FREE_GENERATION_LIMIT,
    get_settings,
    model_for_role,
    tier_for_model,
)
from omnia_api.core.db import get_engine
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.ratelimit import rate_limit_prompt
from omnia_api.core.redis import (
    clear_stream_state,
    get_redis,
    publish_event,
    set_stream_state,
)
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.message import (
    ClientErrorReport,
    MessagePublic,
    PromptRequest,
    PromptResponse,
)
from omnia_api.schemas.project import is_fullstack, orchestrator_template
from omnia_api.services import (
    app_doctor,
    app_errors,
    image_edit,
    orchestrator_client,
    pipeline_debug,
    stack_routing,
    zone_edit,
)
from omnia_api.services import repo as repo_svc
from omnia_api.services.art_director_writer import (
    art_director_writer_generate,
    supports_app_brief,
)
from omnia_api.services.chip_pixel_gate import spec_from_discovery, spec_preview
from omnia_api.services.clarify import generate_clarify_questions
from omnia_api.services.contrast_guard import enforce_contrast
from omnia_api.services.director_polish import director_polish_generate
from omnia_api.services.discovery import (
    BUILD as DISCOVERY_BUILD,
)
from omnia_api.services.discovery import (
    DiscoveryResult,
    PlannedQuestion,
    _explicit_static,
    _has_account_intent,
    _has_conversion_intent,
    _infer_code_from_text,
    _infer_run_intent,
    _infer_run_intent_maybe,
    _infer_stack_from_text,
    _infer_web_pivot,
    _is_run_decline,
    classify_result_type,
    confident_enough_to_build,
    cumulative_idea,
    detect_appification,
    gather_answers,
    infer_niche_label,
    infer_result_type_from_text,
    plan_discovery_questions,
    recap_labels,
    resolve_result_type,
    result_type_to_stack,
    run_discovery,
    serve_planned_question,
    wants_build_now,
    zero_question_build,
)
from omnia_api.services.file_extractor import (
    UnsafePathError,
    apply_edits,
    clean_chat_content,
    extract_edits,
    extract_files,
)
from omnia_api.services.image_resolver import resolve_images
from omnia_api.services.intent_triage import ORCHESTRATE, decide_intent
from omnia_api.services.link_validator import (
    find_dead_links,
    repair_dead_links_inline,
    repair_orphaned_anchors_inline,
)
from omnia_api.services.llm_client import set_free_generation, stream_chat_completion
from omnia_api.services.multipass_generator import multipass_generate
from omnia_api.services.preset_classifier import classify_preset
from omnia_api.services.prompt_builder import (
    KIT_FILES,
    build_art_director_system,
    build_messages,
)
from omnia_api.services.queue import enqueue_entity_gate, enqueue_preview
from omnia_api.services.ui_audit import audit as ui_audit
from omnia_api.services.ui_audit import format_failures_for_retry
from omnia_api.services.vendor_profiles import vendor_directive
from omnia_api.services.visual_enricher import (
    enrich_files as enrich_visual_files,
)
from omnia_api.services.visual_enricher import (
    ensure_signature_floor,
)

RESERVED_BALANCE = Decimal("5.0000")  # минимум перед стартом генерации

# Snapshot/Message.model_id label for the orchestrated (role-mix) path. The real
# per-pass models (Sonnet director, DeepSeek polish, …) are logged per-call in the
# gateway's `usage` table; this label just marks the snapshot as orchestrated
# rather than a single user-picked model.
ORCHESTRATION_LABEL = "Оркестратор Sonnet+DeepSeek"

router = APIRouter(prefix="/api/projects", tags=["messages"])

# Strong references to fire-and-forget background tasks. Without this set,
# `asyncio.create_task(...)` returns a Task whose only reference is the
# anonymous expression — the GC can collect it mid-flight, silently aborting
# the prompt-processing coroutine and leaving the assistant message empty
# in the DB. https://docs.python.org/3/library/asyncio-task.html#creating-tasks
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


async def _emergency_error(
    project_id: UUID, assistant_message_id: UUID, err: str
) -> None:
    """Last-resort recovery when ``_process_prompt`` dies before it ever
    published an ``llm.error`` itself.

    Always publishes the WS event (so the frontend's ``apply()`` handler
    flips ``streamingRef`` and shows a toast) AND finalises the
    assistant message in DB with a human-readable error body + zero
    tokens (so the chat row stops looking "still streaming").

    Each step is wrapped — we'd rather log a secondary failure than
    leave the user staring at a stuck spinner.
    """
    import logging as _emerg_log
    _elog = _emerg_log.getLogger(__name__)
    try:
        await publish_event(
            project_id,
            "llm.error",
            {"message_id": str(assistant_message_id), "error": err[:500]},
        )
    except Exception as pub_exc:
        _elog.error("emergency publish_event failed: %r", pub_exc)
    try:
        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with factory() as session:
            msg = await session.get(Message, assistant_message_id)
            if msg is not None and msg.tokens_out is None:
                # Keep any partial content the model managed to stream.
                if not msg.content:
                    msg.content = f"[Ошибка: {err[:200]}]"
                msg.tokens_out = 0
                msg.tokens_in = msg.tokens_in or 0
                await session.commit()
    except Exception as db_exc:
        _elog.error("emergency finalize failed: %r", db_exc)


def _failed_build_body(accumulated: str, stream_error: object) -> str:
    """Body to persist on the assistant message when a build stream errors.

    Keep any partial content the model managed to stream; otherwise write a
    human-readable error so a user who reloaded (and lost the ephemeral
    ``llm.error`` WS event) still sees WHY the build stopped instead of a
    blank, forever-"streaming" chat row. Mirrors ``_emergency_error``.
    """
    return (
        accumulated
        if accumulated.strip()
        else f"[Ошибка генерации: {str(stream_error)[:300]}]"
    )


async def _probe_compile_errors(
    factory: async_sessionmaker[AsyncSession],
    project_id: UUID,
    assistant_message_id: UUID,
    slug: str,
) -> None:
    """After a clean hot-reload, poll the dev server for a Next.js compile error,
    then probe the live route for a server-side 5xx, and surface either as a card.

    Turbopack recompiles asynchronously, so we give it a few seconds and poll a
    handful of times (bounded ~9s) for a *compile* error. A compile-clean app can
    still 5xx when the route actually renders (server components run lazily), so
    after the compile check is clean we do one *runtime* probe that GETs the page.
    Runs as a background task so it never delays ``llm.done`` — the card arrives
    later via its own ``app.error`` event. Fully fail-soft (R-10): any
    orchestrator hiccup is swallowed — a missing card is acceptable, a crashed
    build is not.
    """
    for _ in range(3):
        await asyncio.sleep(3)
        try:
            status = await orchestrator_client.compile_status(project_id, slug=slug)
        except Exception as exc:
            # Probe is best-effort: a missing card is fine, a crashed build isn't.
            print(f"[PP] compile_status probe failed: {exc!r}", flush=True)
            return
        if status.get("ok", True):
            continue  # clean (or still compiling) — keep watching
        await app_errors.publish(
            factory,
            project_id,
            assistant_message_id,
            category="compile",
            detail=status.get("error") or "Next.js не смог скомпилировать приложение.",
            file=status.get("file"),
        )
        return

    # Compile is clean — now force a render and catch a server-side 5xx that
    # lazy per-route compilation would otherwise hide until the user opens it.
    try:
        runtime = await orchestrator_client.runtime_status(project_id, slug=slug)
    except Exception as exc:
        print(f"[PP] runtime_status probe failed: {exc!r}", flush=True)
        return
    if runtime.get("ok", True):
        return
    code = runtime.get("status_code")
    detail = runtime.get("error") or (
        f"Приложение вернуло ошибку сервера (HTTP {code}) при открытии страницы."
        if code
        else "Приложение упало с ошибкой сервера при открытии страницы."
    )
    await app_errors.publish(
        factory,
        project_id,
        assistant_message_id,
        category="runtime",
        detail=detail,
        file=runtime.get("file"),
    )


def _spawn_compile_probe(
    factory: async_sessionmaker[AsyncSession],
    project_id: UUID,
    assistant_message_id: UUID,
    slug: str,
) -> None:
    """Fire-and-forget the compile probe with a strong reference (see
    ``_BACKGROUND_TASKS``)."""
    task = asyncio.create_task(
        _probe_compile_errors(factory, project_id, assistant_message_id, slug)
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _probe_app_error(
    project_id: UUID, slug: str, *, compile_tries: int = 3, sleep: float = 3.0
) -> tuple[dict[str, Any] | None, str]:
    """Probe the live dev container for a REAL error → ``(status, category)``.

    Returns ``(None, "")`` when the app is compiling clean AND renders without a
    5xx (or the probe itself was inconclusive — a probe hiccup is never reported
    as "broken"). Otherwise ``(status_dict, "compile"|"runtime")`` where status
    carries ``error`` / ``file``. Mirrors ``_probe_compile_errors`` but RETURNS the
    verdict instead of publishing a card — the self-repair loop drives it."""
    for _ in range(max(1, compile_tries)):
        await asyncio.sleep(sleep)
        try:
            st = await orchestrator_client.compile_status(project_id, slug=slug)
        except Exception as exc:
            print(f"[PP] self_repair compile probe failed (inconclusive): {exc!r}", flush=True)
            return None, ""
        if st.get("ok", True):
            continue  # clean or still compiling — keep watching
        return st, "compile"
    try:
        rt = await orchestrator_client.runtime_status(project_id, slug=slug)
    except Exception as exc:
        print(f"[PP] self_repair runtime probe failed (inconclusive): {exc!r}", flush=True)
        return None, ""
    if rt.get("ok", True):
        return None, ""
    return rt, "runtime"


async def _run_app_self_repair(
    project_id: UUID,
    slug: str,
    files: dict[str, str],
    *,
    passes: int,
    on_notice: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[dict[str, str], dict[str, Any] | None, str]:
    """Bounded «probe → fix → hot-reload → re-probe» loop (Claude-Code verify→fix).

    Returns ``(changed_files, final_error_status, final_category)``:
    * ``changed_files`` — cumulative repaired files (``{}`` if nothing changed);
      the caller commits them as a follow-up snapshot so the fix survives a rebuild.
    * ``final_error_status`` — ``None`` when the app ends up GREEN; otherwise the
      last error status dict (the caller surfaces it as a card — old behaviour).

    Fully fail-soft (R-10): the model giving nothing usable, or any orchestrator
    hiccup, ends the loop and falls back to the card. ``files`` is not mutated."""
    changed: dict[str, str] = {}
    work = dict(files)
    last_status: dict[str, Any] | None = None
    last_category = ""
    applied = 0  # number of fixes actually hot-reloaded

    def _metric(outcome: str, cat: str) -> None:
        # One grep-able efficacy line per build (mirrors the originality[shadow]
        # metric): outcome=clean (no error) | healed (doctor fixed it green) |
        # failed (still broken after N passes / no usable fix). Lets ops measure
        # how often DeepSeek's container builds compile vs need / get auto-healed:
        #   docker logs omnia-prod-worker | grep self_repair_metric
        print(
            f"[PP] self_repair_metric outcome={outcome} passes={applied}/{passes} "
            f"cat={cat}",
            flush=True,
        )

    for i in range(max(0, passes)):
        status, category = await _probe_app_error(project_id, slug)
        if status is None:
            # green (or probe inconclusive → don't claim broken). On pass 0 with no
            # fix yet that's a clean build; if we already applied a fix, it healed.
            _metric("healed" if applied else "clean", last_category or "none")
            return changed, None, ""
        last_status, last_category = status, category
        fix = await app_doctor.propose_fix(
            category=category,
            detail=str(status.get("error") or ""),
            file_path=status.get("file"),
            files=work,
        )
        if not fix:
            break  # model gave nothing usable → stop, surface the card
        work.update(fix)
        changed.update(fix)
        try:
            await orchestrator_client.hot_reload(project_id=project_id, slug=slug, files=fix)
        except Exception as exc:
            print(f"[PP] self_repair hot_reload failed: {exc!r}", flush=True)
            break
        applied += 1
        print(f"[PP] self_repair pass={i + 1} fixed={list(fix)} cat={category}", flush=True)
        if on_notice:
            await on_notice(
                f"\n\n*Нашёл ошибку ({category}) — чиню и пересобираю "
                f"({i + 1}/{passes})…*\n\n"
            )
    # Final verdict after the last fix recompiled.
    status, category = await _probe_app_error(project_id, slug)
    if status is None:
        _metric("healed" if applied else "clean", last_category or "none")
        return changed, None, ""
    _metric("failed", last_category or category)
    return changed, (last_status or status), (last_category or category)


def _agent_result_message(res: Any, *, is_edit: bool) -> str:
    """User-facing chat text for an agentic-build run — NEVER leaks the raw summary.

    ``res.summary`` is the model's own prose ONLY when it actually FINISHED (the
    model called ``done``). On every NON-done exit (ran out of steps / stalled /
    looping / gateway error) ``summary`` is an internal English diagnostic — e.g.
    "hit step budget without calling done" / "stuck repeating ..." — which must
    stay in the logs, NOT the chat (the bug: it was surfaced verbatim as Omnia's
    reply). Partial files still commit, so on a non-finish we show a friendly RU
    message that keeps the user moving («Починить» / повтори), keyed to why the
    agent stopped."""
    if getattr(res, "done", False):
        return (getattr(res, "summary", "") or "").strip() or (
            "Готово — правка применена." if is_edit else "Готово — приложение собрано."
        )
    if getattr(res, "stop_reason", "") == "max_steps":
        # Status prose only — a «Продолжить» card is published alongside (see the
        # agent path) and carries the resume action, so don't repeat it here.
        return (
            "Не успел доделать правку за отведённые шаги — часть уже на месте."
            if is_edit
            else "Собрал приложение не полностью за отведённые шаги — часть уже на месте."
        )
    return (
        ("Не удалось завершить правку" if is_edit else "Сборка прервана")
        + ". Попробуй ещё раз или нажми «Починить»."
    )


# A follow-up that means «finish the partially-built app», not a fresh surgical
# edit. The agentic builder operates on the LIVE dev container, which still holds
# the partial build from the prior turn — so «продолжи» re-enters the BUILD loop
# (full prompt + the build step budget) and the agent reads what's there and
# finishes it, instead of being treated as a 12-step minimal edit.
_CONTINUE_KEYWORDS: frozenset[str] = frozenset(
    {
        "продолж",  # продолжи / продолжай / продолжить
        "доделай", "доделать", "доведи", "довести",
        "дособери", "дособерите", "достро",  # дострой/достроить
        "доработай", "допиши", "заверши сбор", "закончи сбор",
        "не доделал", "не докончил", "до конца",
        "finish the build", "continue building", "keep building",
    }
)


def _is_continue_request(prompt: str) -> bool:
    """True when a follow-up asks to FINISH the partial agentic build (see above).

    Caller gates this on a non-first-build CONTAINER project so it only fires when
    there is actually a partial app to continue."""
    t = (prompt or "").strip().lower()
    return any(k in t for k in _CONTINUE_KEYWORDS)


def _spawn_process_prompt(**kwargs: object) -> None:
    """Fire-and-forget _process_prompt with a guaranteed strong reference.

    Any exception that escapes the coroutine is BOTH logged via structlog
    fallback AND surfaced to the frontend via ``llm.error`` + DB finalize
    so the user never sees a stuck "AI читает контекст" spinner — the
    chat row gets an explicit error body and ``streamingRef`` unlocks.
    """
    import logging

    log = logging.getLogger(__name__)
    project_id: UUID = kwargs["project_id"]  # type: ignore[assignment]
    assistant_message_id: UUID = kwargs["assistant_message_id"]  # type: ignore[assignment]
    task = asyncio.create_task(_process_prompt(**kwargs))  # type: ignore[arg-type]
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task[None]) -> None:
        _BACKGROUND_TASKS.discard(t)
        if t.cancelled():
            log.warning("_process_prompt task cancelled")
            # Cancellation = caller no longer cares; still tell the UI
            # so the spinner clears instead of hanging.
            _emerg = asyncio.create_task(_emergency_error(
                project_id, assistant_message_id, "task cancelled",
            ))
            _BACKGROUND_TASKS.add(_emerg)
            _emerg.add_done_callback(_BACKGROUND_TASKS.discard)
            return
        exc = t.exception()
        if exc is not None:
            log.error("_process_prompt failed", exc_info=exc)
            _emerg = asyncio.create_task(_emergency_error(
                project_id,
                assistant_message_id,
                f"{type(exc).__name__}: {exc}",
            ))
            _BACKGROUND_TASKS.add(_emerg)
            _emerg.add_done_callback(_BACKGROUND_TASKS.discard)

    task.add_done_callback(_on_done)


async def _run_clarify(
    project_id: UUID, assistant_message_id: UUID, prompt: str, language: str = "ru"
) -> None:
    """Pre-generation clarify turn: stream 3–4 questions into the assistant
    message, persist them, finalize. NO files, NO snapshot, NO generation — the
    user's answers (next message) drive the real build via history."""
    text = await generate_clarify_questions(prompt, language=language)
    await publish_event(
        project_id,
        "llm.chunk",
        {"message_id": str(assistant_message_id), "delta": text},
    )
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        msg = await session.get(Message, assistant_message_id)
        if msg is not None:
            msg.content = text
            msg.tokens_in = 0
            msg.tokens_out = 0
            await session.commit()
    await publish_event(
        project_id,
        "llm.done",
        {"message_id": str(assistant_message_id), "snapshot_id": None},
    )


def _spawn_clarify(
    project_id: UUID, assistant_message_id: UUID, prompt: str, language: str = "ru"
) -> None:
    """Fire-and-forget _run_clarify with a strong ref + error finalize (mirrors
    _spawn_process_prompt, so a clarify failure never hangs the UI spinner)."""
    import logging

    log = logging.getLogger(__name__)
    task = asyncio.create_task(
        _run_clarify(project_id, assistant_message_id, prompt, language=language)
    )
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task[None]) -> None:
        _BACKGROUND_TASKS.discard(t)
        exc = None if t.cancelled() else t.exception()
        if exc is not None:
            log.error("_run_clarify failed", exc_info=exc)
            _emerg = asyncio.create_task(
                _emergency_error(
                    project_id,
                    assistant_message_id,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            _BACKGROUND_TASKS.add(_emerg)
            _emerg.add_done_callback(_BACKGROUND_TASKS.discard)

    task.add_done_callback(_on_done)


async def _run_text_turn(
    project_id: UUID, assistant_message_id: UUID, text: str
) -> None:
    """Stream a pre-computed assistant message (no LLM, no build) and finalize.

    Used by the progressive-discovery ASK turn: the next question was already
    decided in ``post_prompt``, so we just publish it as one chunk + persist +
    done. Mirrors ``_run_clarify`` minus the gateway call."""
    await publish_event(
        project_id,
        "llm.chunk",
        {"message_id": str(assistant_message_id), "delta": text, "seq": 1},
    )
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        msg = await session.get(Message, assistant_message_id)
        if msg is not None:
            msg.content = text
            msg.tokens_in = 0
            msg.tokens_out = 0
            await session.commit()
    await publish_event(
        project_id,
        "llm.done",
        {"message_id": str(assistant_message_id), "snapshot_id": None},
    )


# Owner 2026-06-19 — the one-click installer card streamed back on a run/install
# intent. The `<install-bundle>` marker is parsed by the web ChatMessage into a
# prominent «Скачать установщик» button (downloads the .zip — which carries run.bat).
_INSTALL_CARD_TEXT = (
    "Готово — собрал установщик. Нажми кнопку ниже: скачается архив проекта. "
    "Если это программа (Python/Node), внутри лежит **run.bat** — распакуй и сделай "
    "двойной клик по нему (на Mac — run.command): он сам поставит зависимости и "
    "запустит. Если это сайт — открой его кнопкой «Открыть» сверху.\n\n"
    "<install-bundle></install-bundle>"
)

# Owner 2026-06-19 «спрашивай, если сомневаешься» — the clarify question shown when
# run/install intent is plausible but uncertain. The «Да…» chip re-enters as strong
# run-intent → installer card; the «Нет…» chip is caught as a decline.
_RUN_ASK_TEXT = (
    "Похоже, ты хочешь запустить проект у себя на компьютере. Собрать установщик "
    "(архив с run.bat — распаковал, двойной клик, и оно само поставится и "
    "запустится)? Или продолжим дорабатывать проект?"
)
_RUN_DECLINE_REPLY = (
    "Понял, установщик не собираю. Напиши, что доработать или добавить — сделаю."
)


def _spawn_text_turn(
    project_id: UUID, assistant_message_id: UUID, text: str
) -> None:
    """Fire-and-forget _run_text_turn with a strong ref + error finalize (so a
    publish hiccup never hangs the UI spinner; mirrors _spawn_clarify)."""
    import logging

    log = logging.getLogger(__name__)
    task = asyncio.create_task(
        _run_text_turn(project_id, assistant_message_id, text)
    )
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task[None]) -> None:
        _BACKGROUND_TASKS.discard(t)
        exc = None if t.cancelled() else t.exception()
        if exc is not None:
            log.error("_run_text_turn failed", exc_info=exc)
            _emerg = asyncio.create_task(
                _emergency_error(
                    project_id,
                    assistant_message_id,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            _BACKGROUND_TASKS.add(_emerg)
            _emerg.add_done_callback(_BACKGROUND_TASKS.discard)

    task.add_done_callback(_on_done)


def _snapshot_payload(s: Snapshot) -> dict[str, object]:
    return {
        "id": str(s.id),
        "project_id": str(s.project_id),
        "commit_sha": s.commit_sha,
        "prompt_text": s.prompt_text,
        "model_id": s.model_id,
        "parent_id": str(s.parent_id) if s.parent_id else None,
        "preview_url": preview_public_url(s.preview_key),
        "is_rollback_target": s.is_rollback_target,
        "created_at": s.created_at.isoformat(),
    }


def _compose_build_prompt(result: DiscoveryResult) -> str:
    """Fold the discovery brief (+ recommended stack hint) into the generator
    prompt. Stack provisioning is wired separately (P1 subtask 5); until then the
    recommendation rides in the brief so the build is aware of the intended shape."""
    brief = result.brief.strip()
    if result.stack == "code":
        brief = (
            f"{brief}\n\n[Это запрос на код/программу, НЕ сайт. Выдай рабочую "
            "программу/скрипт на запрошенном языке (файлы + README), без HTML/"
            "вёрстки.]"
        )
    elif result.stack and result.stack != "static":
        brief = (
            f"{brief}\n\n[Рекомендованный стек: {result.stack} — полноценное "
            "приложение с данными.]"
        )
    return brief


def _build_onboarding_survey(plan: object) -> list[dict[str, object]] | None:
    """Assemble the whole onboarding popup from the planned question batch + a
    palette question (owner 2026-06-19 — «несколько вопросов сразу + палитра»).

    Returns the full survey (every planned text question, each with its chips +
    «Другое», then a clickable preset-palette question) so the workspace renders
    ONE form instead of a chat turn per question. None when the plan is empty."""
    if not isinstance(plan, list) or not plan:
        return None
    out: list[dict[str, object]] = []
    for q in plan:
        if not isinstance(q, dict):
            continue
        msg = str(q.get("message") or "").strip()
        if not msg:
            continue
        out.append(
            {
                "message": msg,
                "kind": "text",
                "choices": [c for c in (q.get("choices") or []) if isinstance(c, str)],
                "allow_custom": bool(q.get("allow_custom", True)),
                "multi_select": bool(q.get("multi_select", False)),
            }
        )
    if not out:
        return None
    # Palette question — clickable preset swatches (owner: «нажать на палитру
    # которая нравится»). Reuses the Awwwards preset catalog; the pick rides back
    # as PromptRequest.design_preset_id. First few presets keep the popup compact.
    from omnia_api.services.design_presets import PRESETS as _PRESETS

    options = [
        {
            "id": pid,
            "name": preset.name,
            "one_liner": preset.one_liner,
            "bg": preset.palette.get("bg", "#ffffff"),
            "accent": preset.palette.get("accent", "#0a0a0a"),
        }
        for pid, preset in list(_PRESETS.items())[:6]
    ]
    out.append(
        {
            "message": "Какая палитра вам ближе? (можно пропустить — подберём сами)",
            "kind": "palette",
            "choices": [],
            "allow_custom": True,
            "multi_select": False,
            "options": options,
        }
    )
    return out


_RESULT_TYPE_QUESTION = (
    "Что именно сделать — лендинг с заявкой, приложение с аккаунтами, "
    "или интерактивный инструмент?"
)
_RESULT_TYPE_CHOICES = (
    "Лендинг с заявкой",
    "Приложение с аккаунтами",
    "Интерактивный инструмент",
    "Просто сайт",
)


async def _maybe_result_type_question(
    prompt: str, language: str
) -> PlannedQuestion | None:
    """One clarifying question about the RESULT TYPE when genuinely ambiguous
    (RT-1 bug 3). Returns a PlannedQuestion to PREPEND to the design plan (so the
    existing serve/index machinery runs the design interview right after the type
    answer — no separate-turn off-by-one, no blind build), or None when the type
    is clear. Gated by result_type_clarify_question; off → always None (today)."""
    s = get_settings()
    if not (s.use_result_type_router and s.result_type_clarify_question):
        return None
    if infer_result_type_from_text(prompt) is not None:
        return None  # keyword net is sure
    rt, conf = await classify_result_type(prompt, language=language)
    if rt is not None and conf >= 0.6:
        return None  # classifier is sure
    return PlannedQuestion(
        message=_RESULT_TYPE_QUESTION,
        choices=_RESULT_TYPE_CHOICES,
        allow_custom=True,
        multi_select=False,
    )


async def _batch_discovery_turn(
    project: Project,
    history: list[dict[str, str]],
    prompt: str,
    *,
    asked_count: int,
    force_build: bool,
    language: str = "ru",
) -> DiscoveryResult:
    """Batch discovery (owner rule 13 #1 — NORTH STAR pillar 2).

    On the FIRST turn we plan the WHOLE set of product-tailored questions in ONE
    upfront gateway pass (unless the prompt is rich enough to skip the popup
    entirely) and stash it on ``project.discovery_plan`` (committed with the
    message rows by the caller). Every turn then SERVES the next pre-computed
    question with NO further gateway call — zero wait between steps. Once the plan
    is exhausted (all questions answered) — or the user forces it — we build,
    reusing ``run_discovery``'s battle-tested brief/stack compilation.

    Never raises (R-10): ``plan_discovery_questions`` degrades to a deterministic
    batch, and the exhausted/forced path delegates to the fail-soft builder.

    ``language`` is threaded into gateway calls so the model replies in the
    project's language. RU (the default) leaves prompts unchanged.
    """
    if force_build:
        return await run_discovery(
            history, prompt, asked_count=asked_count, force_build=True,
            language=language,
        )
    # Plan once, on the first turn, when nothing is stashed yet. A first prompt
    # that already pins the design wins the zero-question shortcut and never gets
    # a plan (the popup never appears).
    if asked_count == 0 and not project.discovery_plan:
        # Code/script request → no design interview at all (owner 2026-06-19). Build
        # straight away (run_discovery short-circuits code intent to a code build) —
        # never plan palette/audience questions for a program.
        if _infer_code_from_text(prompt):
            return await run_discovery(
                history, prompt, asked_count=asked_count, force_build=True,
                language=language,
            )
        zero = zero_question_build(history, prompt)
        if zero is not None:
            return zero
        questions = await plan_discovery_questions(prompt, language=language)
        # RT-1 (bug 3): when the RESULT TYPE is genuinely ambiguous, PREPEND a single
        # type-clarify question as plan[0] so the existing serve/index machinery asks
        # the type first and flows straight into the design interview (no separate
        # turn, no off-by-one blind build). Gated/off by default → plan is unchanged.
        _type_q = await _maybe_result_type_question(prompt, language)
        if _type_q is not None:
            questions = [_type_q, *questions]
        project.discovery_plan = [q.to_dict() for q in questions]
    # LIVE niche (pillar 2 causality): re-infer on the CUMULATIVE answers (idea +
    # every reply), not just the first prompt, so the badge sharpens turn-by-turn
    # as the conversation reveals more. Deterministic; unrecognised → "" (no
    # suffix).
    niche = infer_niche_label(cumulative_idea(history, prompt))
    # Confidence-skip (pillar 2 — «лучший онбординг — его отсутствие»): once the
    # gathered answers pin a recognised niche + ≥2 design axes, build now instead
    # of asking the rest of the batch — the decisive user gets a shorter path.
    # Fail-soft: an unclear interview keeps serving the planned questions.
    if confident_enough_to_build(
        history, prompt, asked_count=asked_count, niche=niche
    ) and serve_planned_question(project.discovery_plan or [], asked_count) is not None:
        return await run_discovery(
            history, prompt, asked_count=asked_count, force_build=True,
            language=language,
        )
    ask = serve_planned_question(project.discovery_plan or [], asked_count)
    if ask is not None:
        # Answer-recap (pillar 2 — «вас услышали»): echo the answers gathered so
        # far back as «✓ …» chips above the next question, so the loop visibly
        # reacts to what the user said.
        recap = recap_labels(gather_answers(history, prompt, asked_count))
        # LIVE design-preview (pillars 2×3 — «покажи ЧТО построим»): resolve the
        # cumulative answers into design tokens so the popup paints a mini-hero
        # that morphs turn-by-turn. Same spec_from_discovery the gauntlet uses.
        design_preview = spec_preview(spec_from_discovery(history, prompt))
        return replace(ask, niche=niche, recap=recap, design_preview=design_preview)
    # Plan exhausted — every question answered → build from the full Q&A.
    return await run_discovery(
        history, prompt, asked_count=asked_count, force_build=True,
        language=language,
    )


async def _ensure_owner(session: SessionDep, project_id: UUID, user_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


@router.post(
    "/{project_id}/client-error",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def report_client_error(
    project_id: UUID,
    payload: ClientErrorReport,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Response:
    """Surface an uncaught JS error from the live preview as a chat card.

    The inspector (inside the previewed page) forwards uncaught exceptions /
    unhandled rejections to the workspace shell, which posts them here. We attach
    the card to the latest *finalised* assistant message so it persists across a
    reload, deduping repeats (the same broken page re-fires on every load).

    Fail-soft + conservative (R-10): gated by ``use_error_cards``; no assistant
    message yet → nothing to attach to → 204; a duplicate → 204. Owner-scoped via
    ``_ensure_owner`` (404 for a foreign/unknown project). The public ``/p/<slug>``
    has no workspace parent, so it never reaches this endpoint.
    """
    await _ensure_owner(session, project_id, current_user.id)
    if not get_settings().use_error_cards:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    res = await session.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .where(Message.role == "assistant")
        .where(Message.tokens_out.is_not(None))
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    msg = res.scalar_one_or_none()
    if msg is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    title, file = app_errors.client_card_signature(
        payload.message, payload.source, payload.line
    )
    if app_errors.has_client_card(msg.content or "", title, file):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    detail = app_errors.client_card_detail(
        payload.message, payload.stack, payload.route, payload.crumbs
    )

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    await app_errors.publish(
        factory,
        project_id,
        msg.id,
        category="client",
        title=title,
        detail=detail,
        file=file,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/prompt",
    response_model=PromptResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_prompt)],
)
async def post_prompt(
    project_id: UUID,
    payload: PromptRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> PromptResponse:
    project = await _ensure_owner(session, project_id, current_user.id)

    # Free-tier gate: the first FREE_GENERATION_LIMIT generations per user are
    # free (wow-effect onboarding) and skip the wallet floor check; the gateway
    # also skips the debit (metadata.free=true). After that, normal balance rules.
    # `UNLIMITED_GENERATIONS=true` (testing escape hatch) forces every gen to be
    # free → skips this wallet-floor check AND the gateway debit (metadata.free).
    is_free = get_settings().unlimited_generations or (
        (current_user.free_generations_used or 0) < FREE_GENERATION_LIMIT
    )
    if not is_free:
        wallet = await session.get(Wallet, current_user.id)
        if wallet is None or wallet.balance_rub < RESERVED_BALANCE:
            raise ApiError("wallet_empty", "insufficient balance", 402)

    # Select-mode picks (serialized for JSONB + the background task). Computed
    # first because the triage below needs the count of picked elements.
    selected_dump = (
        [el.model_dump() for el in payload.selected_elements]
        if payload.selected_elements
        else None
    )

    # Smart triage — the server decides whether this prompt earns the full
    # Director(Opus)→Polish→Audit orchestration or a single cheap model. First
    # build, structural/backend/redesign change, or a batch of edits at once →
    # orchestrate; a lone cosmetic touch-up → cheap. Keeps Opus off "recolour
    # the login button" work so the client pays pennies for the long tail.
    # "First build" = the project has no real generation yet. A new project is
    # seeded with a STARTER snapshot (routers/projects.py — prompt_text=None), so
    # keying off `current_snapshot_id is None` mislabels EVERY first real prompt
    # as a follow-up and drops it to the cheap path. Treat "current snapshot is
    # the starter" (no prompt_text) as the first build instead.
    _cur_snapshot = (
        await session.get(Snapshot, project.current_snapshot_id)
        if project.current_snapshot_id is not None
        else None
    )
    is_first_build = _cur_snapshot is None or _cur_snapshot.prompt_text is None

    # Run/install intent (owner 2026-06-19): on a FOLLOW-UP, "как запустить / хочу
    # запустить / установщик / дай поиграть" → DON'T build; hand back a one-click
    # installer-download card (the .zip already ships a run.bat launcher), so the
    # user goes from ask → installer in one click. Gated to a project that already
    # has something built. Consumed in the turn-routing branch below.
    _has_built = not is_first_build and project.current_snapshot_id is not None
    run_intent = _has_built and _infer_run_intent(payload.prompt)
    # «Спрашивай, если сомневаешься» (owner 2026-06-19): plausible-but-uncertain run
    # intent → ASK "собрать установщик?" with yes/no chips instead of guessing.
    # A decline ("нет, доработать") gets a short "what to change?" reply, not a
    # garbage build. Strong intent above always wins.
    run_decline = _has_built and not run_intent and _is_run_decline(payload.prompt)
    run_ask = (
        _has_built
        and not run_intent
        and not run_decline
        and _infer_run_intent_maybe(payload.prompt)
    )

    # Onboarding-survey palette pick (owner 2026-06-19): the popup submits the
    # chosen design preset here so the build uses it directly. Validated against
    # the known catalog (an unknown id is ignored); persisted with the project
    # changes the handler commits below. Additive — absent on normal prompts.
    if payload.design_preset_id:
        from omnia_api.services.design_presets import PRESETS as _PRESETS

        if payload.design_preset_id in _PRESETS:
            project.design_preset_id = payload.design_preset_id

    # ── Onboarding interview routing ──────────────────────────────────────
    # On a brand-new project we don't build straight away: we first run a short
    # discovery so the first generation works from a real brief, not a one-line
    # idea. Two regimes (progressive supersedes the legacy batch clarify):
    #   * progressive discovery (default) — ask ONE elementary question at a time,
    #     adapt, and build when the model decides it has enough (services/discovery).
    #   * legacy clarify — a single batch of 3–4 questions (kept as a flag fallback).
    # A select-mode pick, an explicit skip_clarify, or a non-first-build prompt all
    # bypass the interview entirely and go straight to generation.
    settings = get_settings()

    # Code→web pivot (owner 2026-06-19): a `code` project has no live preview. A
    # FOLLOW-UP asking to RUN it as a web page ("сделай веб-вид", "в браузере",
    # "запусти здесь") flips it onto the previewable `static` web template (instant
    # /p/<slug>, no container) so the next build is an openable page. Non-destructive
    # (existing source stays — the build PORTS it). Gated to a real follow-up on a
    # code project + explicit web intent → never touches first-build routing or a
    # normal code edit. Fail-soft (R-10). `pivoted_to_web` forces the full-build
    # path below (a fresh page is a build, not a surgical edit).
    pivoted_to_web = False
    if (
        not is_first_build
        and project.template == "code"
        and settings.use_auto_stack_routing
        and _infer_web_pivot(payload.prompt)
    ):
        try:
            pivoted_to_web = await stack_routing.pivot_code_to_web(session, project)
        except Exception as _pv_exc:
            await session.rollback()
            # A failed commit leaves `project` expired; a later attribute access
            # would lazy-load on the poisoned session → MissingGreenlet → 500 for
            # the WHOLE request. Re-fetch a clean, attached instance so the build
            # proceeds normally (as the un-pivoted project).
            project = await session.get(Project, project_id) or project
            logging.getLogger(__name__).warning("code→web pivot failed: %r", _pv_exc)

    # Static→app escalation (P-H1, owner 2026-06-21): a FOLLOW-UP on a STATIC
    # project that clearly asks to become a real app ("переделай в полноценное
    # приложение: вход, кабинет, база записей") must escalate the stack
    # static→container, not surgical-edit the flat page — the H1 blind spot (until
    # now a follow-up could NEVER escalate, since `switch_to_stack` runs only inside
    # the first-build branch). Non-destructive, like the code→web pivot above:
    # `pivot_static_to_app` flips the template only — the static snapshot stays
    # rollback-able, the orchestrated build writes the app on top, and the container
    # scaffold comes from the orchestrator (nextjs_entities has no api-side scaffold
    # dir). Gated to a real follow-up on a static-class project (not a container,
    # not a `code` project — those have their own pivots) + a confident
    # app-ification ask, behind the default-OFF flag. `escalated_to_app` forces the
    # full-build path below (a freshly-templated app is a BUILD, not a surgical
    # edit). Fail-soft (R-10): a hiccup falls back to the un-escalated project.
    escalated_to_app = False
    if (
        not is_first_build
        and not selected_dump
        and not is_fullstack(project.template)
        and project.template != "code"
        and settings.use_followup_appification
        and settings.use_auto_stack_routing
        and detect_appification(payload.prompt)
    ):
        _esc_stack = _infer_stack_from_text(payload.prompt)  # → nextjs_entities
        if _esc_stack:
            try:
                escalated_to_app = bool(
                    await stack_routing.pivot_static_to_app(
                        session, project, _esc_stack
                    )
                )
            except Exception as _esc_exc:
                await session.rollback()
                project = await session.get(Project, project_id) or project
                logging.getLogger(__name__).warning(
                    "static→app escalation failed: %r", _esc_exc
                )

    discovery_result: DiscoveryResult | None = None
    do_clarify = False
    effective_prompt = payload.prompt
    interview_eligible = (
        is_first_build and not payload.skip_clarify and not selected_dump
    )
    if interview_eligible and settings.use_progressive_discovery:
        # Gather the prior conversation (questions already asked + answers) to
        # drive the next discovery turn. The newest message (payload.prompt) is
        # passed separately — it isn't persisted yet.
        _rows = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.project_id == project_id)
                    .order_by(Message.created_at.asc())
                    # 40 so discovery sees the whole interview thread, not just the
                    # last ~20 rows, before it classifies/builds (owner 2026-06-18).
                    .limit(40)
                )
            ).scalars().all()
        )
        _history = [
            {"role": m.role, "content": m.content} for m in _rows if m.content
        ]
        _asked = sum(1 for m in _rows if m.role == "assistant")
        if settings.use_batch_discovery:
            # Plan all 3–4 product-tailored questions in ONE upfront pass, then
            # serve them with zero per-question wait (owner rule 13 #1). The plan
            # is stashed on ``project`` and persisted by the commit below.
            discovery_result = await _batch_discovery_turn(
                project,
                _history,
                payload.prompt,
                asked_count=_asked,
                force_build=wants_build_now(payload.prompt),
                language=project.language,
            )
        else:
            discovery_result = await run_discovery(
                _history,
                payload.prompt,
                asked_count=_asked,
                force_build=wants_build_now(payload.prompt),
                language=project.language,
            )
        if discovery_result.action == DISCOVERY_BUILD:
            # Build now — the compiled brief (with the recommended stack folded in)
            # becomes the generator's prompt; the raw idea stays as the user turn.
            effective_prompt = _compose_build_prompt(discovery_result)
            # Auto stack-routing: if discovery picked a container stack for this
            # still-static project, flip the template + re-scaffold its git +
            # provision the dev container, so the build yields a real app instead
            # of a flat page. Fail-soft (R-10) — a hiccup falls back to a static
            # build rather than dead-ending onboarding.
            if settings.use_auto_stack_routing:
                try:
                    await stack_routing.switch_to_stack(
                        session, project, discovery_result.stack
                    )
                except Exception as _sr_exc:
                    await session.rollback()
                    logging.getLogger(__name__).warning(
                        "stack_routing switch failed (static fallback): %r", _sr_exc
                    )
            # Persist the chip→spec the user steered onboarding toward, so
            # downstream gates can check the live render against what was picked
            # (V2.5.0). Set AFTER stack-routing so a routing rollback can't wipe
            # it; committed with the message rows below. Fail-soft (R-10) — a
            # marshalling hiccup must never block the build.
            try:
                _spec = spec_from_discovery(_history, payload.prompt)
                project.discovery_spec = _spec.to_dict() if _spec else None
            except Exception as _ds_exc:
                logging.getLogger(__name__).warning(
                    "discovery_spec marshal failed (skipping): %r", _ds_exc
                )
        # else: ASK — we stream the question below, no generation this turn.
    elif interview_eligible and settings.use_clarify_interview:
        # Legacy batch clarify (fires only with zero prior messages so the
        # answers — the NEXT message — generate normally). Reply "генерируй" to skip.
        _has_prior_msg = (
            await session.execute(
                select(Message.id).where(Message.project_id == project_id).limit(1)
            )
        ).first() is not None
        do_clarify = not _has_prior_msg

    discovery_ask = (
        discovery_result is not None and discovery_result.action != DISCOVERY_BUILD
    )

    # First-build stack escalation when the interview was skipped.
    # `switch_to_stack` only ever runs inside the discovery BUILD branch above,
    # but the discovery interview is bypassed entirely on the quiz / "just
    # generate" path (`skip_clarify=True`) and on a select-mode first build. On
    # those paths an unmistakable app request ("CRM, вход, личный кабинет, база
    # записей") would still build as freeform static with dead login buttons —
    # the exact blind spot behind «полноценное приложение с 1 генерации». So
    # when discovery did NOT run, reuse discovery's own deterministic safety-net
    # (`_infer_stack_from_text`) to escalate static→container on the FIRST build.
    # Strictly first-build only: a fresh starter has no user content or rollback
    # history, so re-scaffolding is non-destructive (unlike a follow-up — see
    # PROPOSAL P-H1). Fail-soft (R-10): a hiccup falls back to a static build.
    if (
        is_first_build
        and discovery_result is None
        and not discovery_ask
        and not do_clarify
        and not selected_dump
        and settings.use_auto_stack_routing
    ):
        # Code intent (program/script, any language) takes priority over the
        # backend net — "напиши скрипт на python" must not be pulled into an
        # auth-backed web app (owner 2026-06-18). And static is opt-in: if nothing
        # specific fires, default to `spa` (interactive React) unless the user
        # EXPLICITLY asked for a plain static HTML page — same policy as discovery.
        if settings.use_result_type_router:
            # RT-1: decide the RESULT TYPE first (semantic LLM + keyword safety-net),
            # then map type→stack. Each sub-slice is independently gated so OFF = the
            # legacy net behaviour, byte-identical.
            _rt_llm, _rt_conf = await classify_result_type(
                payload.prompt, language=project.language
            )
            _rt = resolve_result_type(payload.prompt, _rt_llm, _rt_conf)
            if _rt is None:
                # Unsure → legacy nets (preserves today's behaviour for the un-typed tail).
                _inferred_stack = (
                    _infer_code_from_text(payload.prompt)
                    or _infer_stack_from_text(payload.prompt)
                    or (None if _explicit_static(payload.prompt) else "spa")
                )
            else:
                # web_app from FRAMING only ships when the appify slice is on; without
                # it (and without a real account ask) framing falls back to today's path.
                if (
                    _rt == "web_app"
                    and not settings.result_type_firstbuild_appify
                    and not _has_account_intent(payload.prompt)
                    and _infer_stack_from_text(payload.prompt) != "nextjs_entities"
                ):
                    # web_app from FRAMING alone (no real backend signal) demotes when
                    # the appify slice is off. But a genuine data/CRUD backend prompt
                    # (legacy net → nextjs_entities) keeps web_app regardless — never
                    # downgrade a real backend app to a no-backend spa.
                    _rt = "landing" if _has_conversion_intent(payload.prompt) else "site"
                # landing→entities suppression (BS-7) ships only with the lead-sink slice.
                if (
                    _rt == "landing"
                    and not settings.result_type_landing_lead_sink
                    and _infer_stack_from_text(payload.prompt) == "nextjs_entities"
                ):
                    _inferred_stack = "nextjs_entities"  # keep today's escalation
                else:
                    _inferred_stack = result_type_to_stack(_rt)
                    if _inferred_stack == "static" and not _explicit_static(payload.prompt):
                        _inferred_stack = "spa"
        else:
            _inferred_stack = (
                _infer_code_from_text(payload.prompt)
                or _infer_stack_from_text(payload.prompt)
                or (None if _explicit_static(payload.prompt) else "spa")
            )
        if _inferred_stack:
            try:
                await stack_routing.switch_to_stack(
                    session, project, _inferred_stack
                )
            except Exception as _sr_exc:
                await session.rollback()
                logging.getLogger(__name__).warning(
                    "first-build stack_routing switch failed (static fallback): %r",
                    _sr_exc,
                )

    intent = decide_intent(
        effective_prompt,
        # A code→web pivot or a static→app escalation just re-templated the project;
        # the new page/app doesn't exist yet, so it's a full BUILD, never a surgical
        # edit — treat it like a first build for triage (owner 2026-06-19; P-H1).
        is_first_prompt=is_first_build or pivoted_to_web or escalated_to_app,
        selected_count=len(selected_dump or []),
        # App-ification triage rule (P-H1), flag-gated so it's a no-op until enabled.
        appify_enabled=settings.use_followup_appification,
    )
    orchestrate = intent == ORCHESTRATE

    # Model choice is server-side — the user never picks. `force_model` is the
    # hidden admin override (env FORCE_MODEL). Otherwise the triage decides:
    # orchestrate → director (Opus) drives prompt-routing + Director→Polish;
    # cheap → a single reliable Haiku shot (role `edit`).
    force_model = settings.force_model or None
    routing_model = force_model or model_for_role("director" if orchestrate else "edit")

    # Explicit, DISTINCT timestamps (owner 2026-06-19 bug): both rows committed in
    # one transaction would otherwise share `func.now()` (= transaction time) →
    # identical created_at → the chat, ordered by created_at, can't tell which came
    # first and renders the assistant reply ABOVE the user's prompt («промпт уехал
    # вниз»). +1ms guarantees user-before-assistant ordering everywhere.
    _now = datetime.now(UTC)
    user_msg = Message(
        project_id=project_id,
        role="user",
        content=payload.prompt,
        model_id=None,  # user turns have no model
        selected_elements=selected_dump,
        created_at=_now,
    )
    assistant_msg = Message(
        project_id=project_id,
        role="assistant",
        content="",
        # Orchestrated runs carry the mix label; cheap runs log the real model.
        model_id=force_model or (ORCHESTRATION_LABEL if orchestrate else routing_model),
        created_at=_now + timedelta(milliseconds=1),
    )
    session.add_all([user_msg, assistant_msg])

    # Detect & persist the project language ONCE, on the first build.  The
    # first prompt is the strongest language signal we have.  A connected
    # user's explicit `default_language` preference beats auto-detection.
    # Fail-soft (R-10): any hiccup must never block the build.
    if is_first_build and project.language == "ru":
        try:
            from omnia_api.services.lang_detect import detect_language

            detected = (
                current_user.default_language
                if current_user.default_language
                else detect_language(payload.prompt)
            )
            if detected and detected != project.language:
                project.language = detected
        except Exception as _ld_exc:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "language detection failed (keeping default): %r", _ld_exc
            )

    await session.commit()
    await session.refresh(user_msg)
    await session.refresh(assistant_msg)

    if discovery_ask:
        # Progressive discovery: stream the next short question, no build this
        # turn. The user's reply (next message) continues the discovery; the
        # generator only runs once discovery decides it has enough.
        assert discovery_result is not None  # discovery_ask ⇒ result exists
        _spawn_text_turn(project_id, assistant_msg.id, discovery_result.message)
    elif do_clarify:
        # Ask first — no generation this turn. The user's answers (next message)
        # flow into the real build via chat history.
        _spawn_clarify(project_id, assistant_msg.id, payload.prompt, language=project.language)
    elif run_intent:
        # Run/install intent (owner 2026-06-19): no build — stream a one-click
        # installer-download card. The user clicks «Скачать установщик», gets the
        # .zip (with run.bat), double-clicks → installed + running.
        _spawn_text_turn(project_id, assistant_msg.id, _INSTALL_CARD_TEXT)
    elif run_decline:
        # Declined the installer offer → don't build from a bare "нет"; ask what
        # to change so the next turn is a real edit.
        _spawn_text_turn(project_id, assistant_msg.id, _RUN_DECLINE_REPLY)
    elif run_ask:
        # Uncertain run intent → ASK "собрать установщик?" (yes/no chips set on the
        # response below); no build this turn.
        _spawn_text_turn(project_id, assistant_msg.id, _RUN_ASK_TEXT)
    else:
        _spawn_process_prompt(
            project_id=project_id,
            user_id=current_user.id,
            user_message_id=user_msg.id,
            assistant_message_id=assistant_msg.id,
            current_snapshot_id=project.current_snapshot_id,
            # On a discovery BUILD this is the compiled brief; otherwise the raw
            # prompt. The full Q&A still rides along via chat history.
            prompt_text=effective_prompt,
            model_id=routing_model,
            force_model=force_model,
            is_free=is_free,
            orchestrate=orchestrate,
            selected_elements=selected_dump,
        )

    # Reset the orchestrator's hibernate timer — a user submitting a new prompt
    # is the strongest possible "this project is active" signal. The hibernate
    # loop subscribes to `activity:*` on Redis and resets its in-memory
    # last_activity[project_id] when this lands. Fire-and-forget — a Redis
    # hiccup must not kill a live prompt.
    try:
        await get_redis().publish(f"activity:{project_id}", "")
    except Exception:
        pass

    # Tell the workspace how this turn will be handled so it can set the right
    # expectation instantly: a surgical edit keeps the current preview and shows
    # "точечная правка" copy; a build shows the full-generation experience.
    turn_mode = (
        "clarify"
        if (discovery_ask or do_clarify or run_intent or run_ask or run_decline)
        else ("build" if orchestrate else "edit")
    )
    # Quick-reply chips ride on the ASK turn only — the workspace renders them
    # under the streamed question so the user can tap an answer instead of typing.
    if discovery_ask:
        assert discovery_result is not None  # discovery_ask ⇒ result exists
        ask_choices = list(discovery_result.choices)
        allow_custom = discovery_result.allow_custom
        multi_select = discovery_result.multi_select
        # Onboarding-frame metadata (pillar 2): «Вопрос N из M» + niche banner.
        # 0 → None so the workspace hides the counter on the legacy per-question
        # path (no upfront plan → unknown total).
        question_index = discovery_result.question_index or None
        question_total = discovery_result.question_total or None
        niche = discovery_result.niche or None
        recap = list(discovery_result.recap)
        design_preview = discovery_result.design_preview
        # Owner 2026-06-19 — on the FIRST discovery turn return the WHOLE survey
        # (every planned question + a clickable palette question) so the workspace
        # renders ONE popup form, not a chat turn per question. Only the first turn
        # (index 1) with a stashed plan; follow-up turns keep the single-question
        # fields (back-compat for a client that ignores `survey`).
        survey = (
            _build_onboarding_survey(project.discovery_plan)
            if question_index == 1
            else None
        )
    else:
        # Uncertain run-intent question (owner 2026-06-19): tappable yes/no chips.
        # «Да…» re-enters as strong run-intent → installer card; «Нет…» is caught
        # as a decline → "what to change?" reply.
        ask_choices = (
            ["Да, собрать установщик", "Нет, доработать проект"] if run_ask else []
        )
        allow_custom = True
        multi_select = False
        question_index = None
        question_total = None
        niche = None
        recap = []
        design_preview = None
        survey = None
    return PromptResponse(
        message_id=assistant_msg.id,
        snapshot_id=None,
        mode=turn_mode,
        choices=ask_choices,
        allow_custom=allow_custom,
        multi_select=multi_select,
        question_index=question_index,
        question_total=question_total,
        niche=niche,
        recap=recap,
        design_preview=design_preview,
        survey=survey,
    )


@router.get("/{project_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Sequence[Message]:
    await _ensure_owner(session, project_id, current_user.id)
    res = await session.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return list(res.scalars().all())


# Fallback chain when the primary model returns junk (empty / <50 chars /
# malformed). Gemini Flash on the free tier silently truncates ~10% of
# responses to 1-2 tokens; rather than make the user notice + retry by hand,
# we transparently re-run the same prompt against a stable model and keep
# the conversation moving. Order: most capable that's *not* the primary,
# guarded by `model != primary`.
_EMPTY_RESPONSE_FALLBACKS: dict[str, list[str]] = {
    "gemini-2.5-pro": ["gemini-2.5-flash", "claude-haiku-4-5", "gpt-5-nano"],
    "gemini-2.5-flash": ["claude-haiku-4-5", "gpt-5-nano"],
    # proxyapi.ru occasionally short-replies even for Haiku (we've seen
    # acc_len=3 / tokens_out=2 on long prompts). Fallback to gpt-5-nano on
    # the same proxyapi balance — different upstream, same key, same money.
    "claude-haiku-4-5": ["gpt-5-nano", "gigachat-2-pro"],
    "claude-sonnet-4-6": ["claude-haiku-4-5", "gpt-5-nano"],
    "claude-opus-4-7": ["claude-sonnet-4-6", "claude-haiku-4-5"],
    # DeepSeek (vsegpt) is the worker-role default now. A vsegpt hiccup, or an
    # over-long chain-of-thought that truncates the visible answer, degrades to
    # the reliable proxyapi route (Haiku → Sonnet) instead of an empty preview.
    "deepseek-chat": ["claude-haiku-4-5", "claude-sonnet-4-6"],
    "deepseek-v4-flash-thinking": ["claude-haiku-4-5", "claude-sonnet-4-6"],
    # The freeform WRITER (`deepseek-v4-pro`) and the route model
    # (`deepseek-v4-pro-thinking`) can shadow-drop the whole page — 0 chars, the
    # reasoning field eats the token budget and the visible answer is empty.
    # Neither had a fallback, so an empty writer shipped a BLANK build (owner
    # trace 2026-06-03, msg 9c83ada4: writer_raw chars=0 → same-model multipass
    # retry → `non-JSON content` → dead). Switch to a DIFFERENT live model: Kimi
    # (the art_director brain — already loaded every build, writes HTML well),
    # then the cheap deepseek-chat. Stays on vsegpt (proxyapi is gone).
    "deepseek-v4-pro": ["kimi-k2.6-thinking", "deepseek-chat"],
    "deepseek-v4-pro-thinking": ["kimi-k2.6-thinking", "deepseek-chat"],
    # GPT-5 family are reasoning models and may shadow-drop output even with
    # reasoning_effort=minimal; fall back to Haiku (same proxyapi key).
    "gpt-5": ["claude-haiku-4-5", "gpt-5-nano"],
    "gpt-5-nano": ["claude-haiku-4-5", "gigachat-2-pro"],
    "gpt-5-mini": ["claude-haiku-4-5", "gpt-5-nano"],
    "gpt-4.1": ["claude-haiku-4-5", "gpt-5"],
}


def _looks_truncated(accumulated: str, files: dict[str, str]) -> bool:
    """Heuristic: did the upstream return usable content?

    True when files extracted = 0 AND the raw text is either empty, very
    short, or trailed off mid-syntax (`<` / ` ```html\\n<` / unfinished tag).
    We deliberately don't try to be too clever: better to occasionally retry
    a perfectly fine refusal than to leave the user staring at an empty
    preview.
    """
    if files:
        return False
    stripped = accumulated.strip()
    if not stripped:
        return True
    if len(stripped) < 50:
        return True
    # Common patterns we've actually seen Gemini cut off with.
    tail = stripped[-10:]
    if tail.endswith("<") or tail.endswith("```html\n<") or tail.endswith("```html"):
        return True
    return False


_EDIT_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")
_EDIT_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)
_EDIT_TAG_RE = re.compile(r"<[^>]+>")


def _visible_words(html: str) -> set[str]:
    """Lowercased set of visible words (≥3 chars) in HTML — tags + script/style
    bodies stripped. Measures how much of a page's CONTENT survived a fallback
    rewrite so a scoped edit (text preserved) is told apart from a silent
    re-design (text replaced)."""
    no_code = _EDIT_SCRIPT_STYLE_RE.sub(" ", html)
    text = _EDIT_TAG_RE.sub(" ", no_code)
    return {w.lower() for w in _EDIT_WORD_RE.findall(text)}


def _text_preserved_ratio(old_html: str, new_html: str) -> float:
    """Fraction of the OLD page's visible words still present in the NEW page.
    ~1.0 = same content (a scoped edit — e.g. only the background changed);
    low = the model rewrote the copy (a re-design we must NOT silently ship)."""
    old = _visible_words(old_html)
    if not old:
        return 1.0
    return len(old & _visible_words(new_html)) / len(old)


_HTML_START_RE = re.compile(r"<!doctype html|<html[ >]", re.I)
_HTML_FENCE_RE = re.compile(r"```(?:html)?\s*(.*?)```", re.S | re.I)


def _salvage_html(text: str) -> str | None:
    """Pull a full HTML document out of a rewrite that forgot the <file> wrapper
    (or fenced it in ```html). The rewrite model sometimes streams raw HTML; this
    rescues it instead of dropping the whole edit. Returns the page or None."""
    t = text.strip()
    fence = _HTML_FENCE_RE.search(t)
    if fence and "<" in fence.group(1):
        t = fence.group(1).strip()
    m = _HTML_START_RE.search(t)
    if not m:
        return None
    html = t[m.start() :]
    end = html.lower().rfind("</html>")
    if end != -1:
        html = html[: end + len("</html>")]
    return html if len(html) > 800 else None


_KIT_LINK = '<link rel="stylesheet" href="assets/omnia-kit.css">'
# anime.min.js must load BEFORE omnia-kit.js — the kit's defer callback reads
# window.anime at DOMContentLoaded. Both are KIT_FILES (stripped from model
# output + re-injected here), so the order is guaranteed regardless of the model.
_ANIME_SCRIPT = '<script src="assets/anime.min.js" defer></script>'
_KIT_SCRIPT = '<script src="assets/omnia-kit.js" defer></script>'

# Container-backed React templates that hot-reload `<file path=…>` blocks into a
# dev container. They render React (not static index.html), so they skip the
# static-only guards (dead-link repair, omnia-kit CSS/JS injection) and the
# landing-page acceptance gate, and they DO hot-reload generated files into their
# dev container. `nextjs_entities` is the Base44-style entity-engine stack; `spa`
# is the Vite + React no-backend stack (Phase 7.2) — despite the historical name,
# it's container-backed and file-extracted, so it belongs to this group, not the
# freeform-HTML path. (tgbot/api are backend-only and handled elsewhere.)
CONTAINER_NEXT = ("fullstack", "nextjs_entities", "spa", "realtime")


# A6a — managed auth columns the AI must never drop when it rewrites
# src/lib/db/schema.ts for its own entity. The template ships them (+ a comment)
# yet the model still strips them, which breaks signup/signin (insert/select on a
# column that no longer exists). We re-inject them deterministically.
_AUTH_USERS_COLUMNS = (
    '  passwordHash: text("password_hash"),\n'
    '  role: text("role").notNull().default("user"),\n'
)


# A6b — the four Auth.js tables the AI must never drop. `src/lib/auth.ts` (a
# fixed template file) does `import { accounts, sessions, users,
# verificationTokens } from "@/lib/db/schema"`, so if the model's schema.ts
# rewrite omits them the whole app fails to compile ("Export users doesn't
# exist") and 500s. This is the canonical block from the template schema.ts.
_AUTH_TABLES_BLOCK = '''
// ─── Auth tables (re-injected by Omnia — the model dropped them) ───────────
export const users = pgTable("users", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: text("name"),
  email: text("email").notNull().unique(),
  emailVerified: timestamp("email_verified", { withTimezone: true }),
  image: text("image"),
  passwordHash: text("password_hash"),
  role: text("role").notNull().default("user"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const accounts = pgTable("accounts", {
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  type: text("type").$type<AdapterAccountType>().notNull(),
  provider: text("provider").notNull(),
  providerAccountId: text("provider_account_id").notNull(),
  refresh_token: text("refresh_token"),
  access_token: text("access_token"),
  expires_at: integer("expires_at"),
  token_type: text("token_type"),
  scope: text("scope"),
  id_token: text("id_token"),
  session_state: text("session_state"),
}, (account) => ({
  pk: primaryKey({ columns: [account.provider, account.providerAccountId] }),
}));

export const sessions = pgTable("sessions", {
  sessionToken: text("session_token").primaryKey(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
});

export const verificationTokens = pgTable("verification_tokens", {
  identifier: text("identifier").notNull(),
  token: text("token").notNull(),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
}, (vt) => ({
  pk: primaryKey({ columns: [vt.identifier, vt.token] }),
}));

'''

# pg-core named imports the auth block needs.
_AUTH_PGCORE_IMPORTS = ("integer", "pgTable", "primaryKey", "text", "timestamp", "uuid")


def _ensure_named_imports(src: str, module: str, needed: tuple[str, ...]) -> str:
    """Ensure `src` imports every name in `needed` from `module`, merging into an
    existing `import { ... } from "module"` line (no duplicate-identifier errors)
    or prepending a fresh one."""
    import re

    pat = re.compile(
        r'import\s+\{([^}]*)\}\s+from\s+"' + re.escape(module) + r'"\s*;'
    )
    m = pat.search(src)
    if m:
        existing = {x.strip() for x in m.group(1).split(",") if x.strip()}
        merged = sorted(existing | set(needed))
        line = "import { " + ", ".join(merged) + ' } from "' + module + '";'
        return src[: m.start()] + line + src[m.end() :]
    line = "import { " + ", ".join(sorted(needed)) + ' } from "' + module + '";\n'
    return line + src


def _inject_auth_tables(src: str) -> str:
    """Re-inject the four Auth.js tables when the model dropped them entirely.

    Merges the required imports (drizzle-orm/pg-core names, `sql`, and the
    `AdapterAccountType` type) then inserts the canonical table block before the
    model's first `export const`. Fail-soft: on any surprise, returns `src`."""
    try:
        out = _ensure_named_imports(src, "drizzle-orm/pg-core", _AUTH_PGCORE_IMPORTS)
        out = _ensure_named_imports(out, "drizzle-orm", ("sql",))
        if "AdapterAccountType" not in out:
            out = (
                'import type { AdapterAccountType } from "next-auth/adapters";\n'
                + out
            )
        idx = out.find("\nexport const ")
        if idx == -1:
            idx = len(out)
        out = out[:idx] + "\n" + _AUTH_TABLES_BLOCK + out[idx:]
        print(
            "[PP] auth_schema_guard: re-injected dropped users/accounts/sessions/"
            "verificationTokens tables",
            flush=True,
        )
        return out
    except Exception as exc:
        print(f"[PP] auth_schema_guard: inject failed {exc!r}", flush=True)
        return src


def _preserve_auth_schema(files: dict[str, str]) -> dict[str, str]:
    """Keep the rewritten Drizzle schema's auth surface intact.

    Two failure modes the model causes when it rewrites ``src/lib/db/schema.ts``
    to add its own tables:
      1. Drops the whole ``users``/``accounts``/``sessions``/``verificationTokens``
         block — ``auth.ts`` imports them by name, so the app 500s on compile.
      2. Keeps ``users`` but strips the ``password_hash``/``role`` columns the
         Credentials provider depends on — signup/login then fail at runtime.

    This guard repairs both. Idempotent + fail-soft: on any parse surprise it
    returns ``files`` as-is.
    """
    path = "src/lib/db/schema.ts"
    src = files.get(path)
    if not src:
        return files
    if 'pgTable("users"' not in src:
        # Mode 1: the entire auth-tables block is gone — re-inject all four.
        return {**files, path: _inject_auth_tables(src)}
    if "password_hash" in src:
        return files
    import re

    m = re.search(r'export const users\s*=\s*pgTable\(\s*"users"\s*,\s*\{', src)
    if not m:
        return files
    # Walk braces from just after the opening `{` to find this object's close,
    # tolerating nested `{ ... }` (e.g. timestamp("x", { withTimezone: true })).
    depth, i, n = 1, m.end(), len(src)
    while i < n and depth > 0:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return files
    close_brace = i - 1  # index of the object's closing `}`
    patched = src[:close_brace] + _AUTH_USERS_COLUMNS + src[close_brace:]
    print("[PP] auth_schema_guard: re-injected users.passwordHash/role", flush=True)
    return {**files, path: patched}


def _normalize_entity_filenames(files: dict[str, str]) -> dict[str, str]:
    """Align each ``entities/<X>.json`` filename with its declared ``name``.

    The entity registry resolves a definition STRICTLY by ``entities/<Name>.json``
    (``registry.ts`` ``entityPath`` → ``${name}.json``; ``listEntities`` keys by
    filename), and every consumer references an entity by its ``name`` — the SDK
    (``entities.Client``), reference fields (``"entity": "Client"``), and the
    writer's screens (``<CrudResource entity="Client">``). The contract is
    "filename === Name" (SYSTEM_PROMPT.md, and the starter ``Task.json``).

    But the art-director brief sometimes lists entity files in lowercase / plural
    form (``clients.json``) and the writer copies that filename VERBATIM
    (art_director_writer.py:565 «заведи каждый entities/<Имя>.json ДОСЛОВНО»). The
    result: ``entities/clients.json`` declares ``{"name": "Client"}`` while the
    runtime stats ``entities/Client.json`` → ENOENT → ``loadEntity`` returns null
    → every read/write 404s («unknown entity 'Client'») → the whole app is dead
    (empty lists everywhere, no record can be created), even though the brief,
    the screens and the references are all internally consistent.

    This guard renames such files to match their internal ``name``. Deterministic,
    idempotent, fail-soft: only touches paths under ``entities/`` whose JSON parses
    and carries a valid identifier ``name`` that differs from the filename stem;
    never clobbers a file already named correctly; a no-op for already-correct
    apps and for any stack without ``entities/*.json``.
    """
    import json as _json
    import re as _re

    ident = _re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    prefix, suffix = "entities/", ".json"

    def stem_of(p: str) -> str:
        return p[len(prefix) : -len(suffix)]

    # Stems already present — never overwrite a correctly-named sibling.
    existing = {
        stem_of(p) for p in files if p.startswith(prefix) and p.endswith(suffix)
    }
    out = dict(files)
    renamed: list[str] = []
    for path, content in list(files.items()):
        if not (path.startswith(prefix) and path.endswith(suffix)):
            continue
        stem = stem_of(path)
        try:
            name = _json.loads(content).get("name")
        except Exception:
            continue  # malformed JSON — leave it for the registry/normalize() path
        if not isinstance(name, str) or not ident.match(name) or name == stem:
            continue
        if name in existing:
            continue  # a file already declares this Name — don't clobber it
        out.pop(path, None)
        out[f"{prefix}{name}{suffix}"] = content
        existing.discard(stem)
        existing.add(name)
        renamed.append(f"{stem}->{name}")
    if renamed:
        print(f"[PP] entity_filename_guard: {', '.join(renamed)}", flush=True)
    return out


def _warn_unparseable_entity_json(files: dict[str, str]) -> list[str]:
    """Observability for BS-31: the writer sometimes emits INVALID JSON for an
    entity file (an unterminated string, a dropped key name, …). Today such a
    file passes every guard untouched — `_normalize_entity_filenames` skips
    unparseable files (it cannot read their ``name``) and the runtime
    ``loadEntity`` then silently returns ``null`` (registry.ts:87-89), so
    ``GET /api/entities/<X>`` answers 404 ``unknown entity`` and a declared
    entity is DOA with ZERO signal anywhere. On real CRM builds this hit the
    CENTRAL entity (``Client``) on 2/2 consecutive live gens.

    This does NOT repair, regenerate, or drop the file — that action is
    policy-adjacent and cross-surface (see PROPOSAL P-ENTITYJSON / rule 15). It
    only makes the corruption loud in the build log, at the moment it is
    introduced, so the dominant failure mode is diagnosable instead of silent.
    Returns the list of entity names whose JSON does not parse.
    """
    import json as _json

    bad: list[str] = []
    for path, content in files.items():
        if not (path.startswith("entities/") and path.endswith(".json")):
            continue
        try:
            _json.loads(content)
        except Exception as exc:
            name = path[len("entities/") : -len(".json")]
            bad.append(name)
            print(
                f"[PP] entity_json_INVALID: {name}.json does not parse ({exc}); "
                f"entity will resolve as 404 'unknown entity' at runtime — DOA",
                flush=True,
            )
    return bad


def _extract_files_and_edits(
    accumulated: str, base_files: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Объединяет два формата ответа AI:

    * ``<file path="...">`` — полное содержимое (новый файл / полный rewrite).
    * ``<edit path="...">`` с SEARCH/REPLACE-блоками — точечные правки. Намного
      дешевле по токенам: модель отдаёт ~200-500 символов diff вместо 25K
      переписанного файла.

    Контракт мерджа: если модель прислала и ``<file>``, и ``<edit>`` для
    одного path — побеждает ``<file>`` (явный полный replace > патч).
    Edit с конфликтом (SEARCH не нашёлся или нашёлся >1 раз) попадает в
    ``conflicts`` и в результат не входит — caller может решить попросить
    модель прислать <file> вместо.

    Возвращает ``(files_to_commit, conflicts)``. ``files_to_commit`` идёт
    дальше в обычный commit-flow.
    """
    files = extract_files(accumulated)
    edits = extract_edits(accumulated)
    if not edits:
        return files, []
    # Edit base = текущее состояние МИНУС те файлы, которые модель явно
    # переписала через <file>. Это предотвращает гонку «применили патч к
    # старой версии, потом перезаписали полной новой».
    edit_base = {p: c for p, c in base_files.items() if p not in files}
    patched, conflicts = apply_edits(edits, edit_base)
    return {**patched, **files}, conflicts


def _ensure_kit_linked(files: dict[str, str]) -> dict[str, str]:
    """Гарантировать, что каждая возвращённая HTML-страница подключает Omnia-кит.

    Если модель «уронила» теги — переинжектим их перед </head> (иначе анимации и
    интерактив тихо ломаются). Идемпотентно: страницы со ссылкой пропускаем.
    """
    out = dict(files)
    for path, content in files.items():
        if not path.lower().endswith((".html", ".htm")):
            continue
        has_css = "assets/omnia-kit.css" in content
        has_anime = "assets/anime.min.js" in content
        has_js = "assets/omnia-kit.js" in content
        if has_css and has_anime and has_js:
            continue
        inject = ""
        if not has_css:
            inject += "  " + _KIT_LINK + "\n"
        if not has_anime:
            inject += "  " + _ANIME_SCRIPT + "\n"
        if not has_js:
            inject += "  " + _KIT_SCRIPT + "\n"
        if "</head>" in content:
            content = content.replace("</head>", inject + "</head>", 1)
        else:
            content = inject + content
        out[path] = content
    return out


def _with_vendor_directive(
    messages: list[dict[str, str]], model_id: str, *, json_strict: bool
) -> list[dict[str, str]]:
    """Return a copy of ``messages`` with the per-vendor block appended to the
    LAST user turn. The system turn stays byte-identical → Anthropic prompt
    cache still hits. No-op (returns the same list) for GENERIC/uncalibrated
    models so there's zero regression on models we haven't tuned.

    Used by the single-shot / freeform path; the catalog Director→Polish and
    multipass paths inject the directive in their own message builders.
    """
    directive = vendor_directive(model_id, json_strict=json_strict)
    if not directive:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            turn = dict(out[i])
            turn["content"] = f"{turn['content']}\n\n{directive}"
            out[i] = turn
            break
    return out


# B3 — LLM-as-judge audit. Runs ONLY on a borderline rubric score (the band
# where the deterministic 10-point check is least decisive); clear-cut scores
# skip it so we don't pay Sonnet on every generation.
_AUDIT_JUDGE_LOW = 6
_AUDIT_JUDGE_HIGH = 7
_AUDIT_JUDGE_SYSTEM = (
    "Ты — придирчивый арт-директор Omnia.AI. Тебе дают объективный аудит "
    "лендинга (балл из 10 + список нарушений) и его HTML. Реши, годится ли "
    "страница в продакшен или нужна перегенерация. Ответь РОВНО одним словом: "
    "PASS или RETRY. Без пояснений."
)


async def _audit_judge_wants_retry(
    *,
    html: str,
    report: Any,
    model: str,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> bool | None:
    """LLM second opinion (role ``audit``, Sonnet) on a BORDERLINE rubric score.

    Returns True (re-roll), False (ship as-is), or None when the judge errored
    or gave no clear verdict — the caller then keeps the deterministic rubric
    decision. Best-effort: never raises, never streamed to the user.
    """
    failed = "; ".join(f.check_id for f in report.failures) or "—"
    judge_messages = [
        {"role": "system", "content": _AUDIT_JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Объективный аудит: {report.score}/{report.max}. "
                f"Нарушения: {failed}.\n\nHTML:\n{html[:6000]}\n\n"
                "Вердикт одним словом: PASS или RETRY."
            ),
        },
    ]
    parts: list[str] = []
    try:
        async for ev in stream_chat_completion(
            judge_messages,
            model,
            str(user_id),
            str(project_id),
            str(message_id),
        ):
            if "delta" in ev:
                parts.append(str(ev["delta"]))
            elif "error" in ev:
                return None
    except Exception:
        return None
    verdict = "".join(parts).strip().upper()
    if "RETRY" in verdict:
        return True
    if "PASS" in verdict:
        return False
    return None


async def _catalog_fallback_generate(
    *,
    history: list[dict[str, str]],
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    preset_id: str | None,
    project_id: UUID,
    user_id: UUID,
    assistant_message_id: UUID,
    current_files: dict[str, str],
    discovery_spec: dict[str, object] | None = None,
) -> dict[str, str] | None:
    """Acceptance fallback — regenerate a page via the catalog/IR path.

    Freeform output that fails the acceptance gate after its retries falls
    here: the catalog path emits validated PageIR JSON that renders to a
    structurally guaranteed page (the director model holds the strict schema).
    Returns rendered files, or None on any failure — the caller then keeps the
    freeform attempt rather than shipping nothing (R-10 fail-soft).
    """
    import json as _json

    from pydantic import ValidationError as _VE

    from omnia_api.core.config import model_for_role as _mfr
    from omnia_api.sections import PageIR as _PageIR
    from omnia_api.sections import apply_smart_defaults as _asd
    from omnia_api.sections.renderer import render_to_files as _rtf
    from omnia_api.services.lean_prompt import build_catalog_messages as _bcm

    try:
        cat_messages = _bcm(
            history=history,
            user_prompt=prompt_text,
            selected_elements=selected_elements,
            preset_id=preset_id,
            project_id=str(project_id),
            # V2.5c — regeneration must honour the same chip-spec the gate judges
            # against, else reject→regen ignores chips and loops forever (V2.5d).
            discovery_spec=discovery_spec,
        )
        parts: list[str] = []
        async for ev in stream_chat_completion(
            cat_messages,
            _mfr("director"),
            str(user_id),
            str(project_id),
            str(assistant_message_id),
        ):
            if "delta" in ev:
                parts.append(str(ev["delta"]))
        raw = "".join(parts).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        ir = _PageIR.model_validate(_json.loads(raw))
        ir = _asd(ir, preset_id=preset_id)
        kit_css = current_files.get("src/assets/omnia-kit.css", "")
        kit_js = current_files.get("src/assets/omnia-kit.js", "")
        return dict(_rtf(ir, kit_css=kit_css, kit_js=kit_js))
    except (_json.JSONDecodeError, _VE, ValueError, KeyError) as exc:
        print(f"[PP] catalog_fallback_parse_failed err={exc!r}", flush=True)
        return None
    except Exception as exc:
        print(f"[PP] catalog_fallback_failed err={exc!r}", flush=True)
        return None


_IMG_PROMPT_SYSTEM = (
    "Ты пишешь КОРОТКИЙ детальный промпт НА АНГЛИЙСКОМ для модели генерации фото "
    "(flux) под премиум-бренд. По запросу пользователя и контексту бренда верни "
    "ОДНУ строку 20–40 слов: subject/scene, lighting, angle, lens, mood, palette. "
    "БЕЗ текста и логотипов в кадре, без кавычек, без префиксов, только сам промпт."
)


async def _craft_image_prompt(
    user_request: str,
    preset_id: str | None,
    old_img_tag: str,
    force_model: str | None,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> tuple[str, dict[str, Any] | None]:
    """Turn a (often vague, Russian) image request into a detailed EN flux prompt
    via the cheap ``image_prompt`` role — the only LLM on the direct-image path.
    Fail-soft: returns a sensible template prompt if the call errors/empties."""
    _alt = image_edit.alt_of(old_img_tag)
    ctx = (
        f"Бренд/пресет: {preset_id or 'премиум, тёмная элегантная эстетика'}. "
        f"Текущая картинка (alt): {_alt or '—'}. "
        f"Запрос пользователя: {user_request}"
    )
    parts: list[str] = []
    usage: dict[str, Any] | None = None
    try:
        async for ev in stream_chat_completion(
            [
                {"role": "system", "content": _IMG_PROMPT_SYSTEM},
                {"role": "user", "content": ctx},
            ],
            model_for_role("image_prompt", override=force_model),
            str(user_id),
            str(project_id),
            str(message_id),
        ):
            if "delta" in ev:
                parts.append(str(ev["delta"]))
            elif "usage" in ev:
                usage = ev["usage"]  # type: ignore[assignment]
    except Exception as exc:
        print(f"[PP] craft_image_prompt failed {exc!r}", flush=True)
    prompt = " ".join("".join(parts).split()).strip().strip('"')[:600]
    if len(prompt) < 12:
        prompt = (
            f"atmospheric premium brand photograph, {user_request}, moody elegant "
            "lighting, dark refined palette, shallow depth of field, 85mm"
        )
    return prompt, usage


async def _process_prompt(
    project_id: UUID,
    user_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
    current_snapshot_id: UUID | None,
    prompt_text: str,
    model_id: str,
    force_model: str | None = None,
    is_free: bool = False,
    orchestrate: bool = True,
    selected_elements: list[dict[str, Any]] | None = None,
) -> None:
    import logging as _log_mod
    _log = _log_mod.getLogger(__name__)
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
    print(f"[PP] start project={project_id} asst_msg={assistant_message_id} model={model_id} free={is_free} force={force_model} surgical={surgical}", flush=True)

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    accumulated = ""
    # Resumable-stream accumulator. `_run_stream` resets its per-pass
    # `state["accumulated"]` on every fallback re-run, but the CLIENT's content
    # is the concatenation of every delta ever published for this message — so
    # the Redis buffer (used to resync a reconnecting client) must mirror THAT,
    # not the per-pass slice. `seq` is monotonic across the whole message life.
    pub: dict[str, object] = {"seq": 0, "content": ""}
    usage_data: dict[str, float | int] | None = None
    current_sha: str | None = None
    current_files: dict[str, str] = {}
    history_serialized: list[dict[str, str]] = []
    project_template = "blank"
    project_slug = ""
    project_name = ""
    project_design_preset_id: str | None = None
    project_image_gen_enabled: bool = True
    project_discovery_spec: dict[str, object] | None = None
    project_language: str = "ru"
    project_is_imported: bool = False

    try:
        async with factory() as session:
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
            history_serialized = [
                {"role": m.role, "content": m.content} for m in rows if m.content
            ]
        print(f"[PP] ctx_loaded sha={current_sha} history={len(history_serialized)}", flush=True)

        # B4 — imported repos are ALWAYS surgical-edit only.  We enforce this
        # here (after the DB load sets project_is_imported) regardless of the
        # triage result: the build pipeline (palette/contrast/acceptance/vision/
        # dead-link/kit-injection) assumes an Omnia-authored template and must
        # NEVER touch foreign source code.
        if project_is_imported:
            surgical = True
        print(f"[PP] imported={project_is_imported} surgical_final={surgical}", flush=True)

        # Auto stack-routing, part 2: container-backed stacks need a live dev
        # container for the post-build hot_reload to land in. Provision it now —
        # at the START of the worker — so it warms up in parallel with the
        # (minutes-long) generation below. Idempotent + fail-soft: if the
        # container already exists this is a no-op; if the orchestrator hiccups
        # the build still ships the snapshot and hot_reload/«Запустить» retries.
        # Imported repos are never container-backed (detect_template returns
        # "blank"/"code", neither is in CONTAINER_NEXT), but gate defensively.
        if project_template in CONTAINER_NEXT and project_slug and not project_is_imported:
            await stack_routing.ensure_provisioned(
                project_id, project_slug, project_template
            )

        if current_sha:
            current_files = await asyncio.to_thread(
                repo_svc.read_files, project_id, current_sha
            )
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
        if not project_design_preset_id:
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
        if (
            get_settings().use_agentic_builder
            and project_template in CONTAINER_NEXT
            and project_slug
            and not project_is_imported
            and (orchestrate or surgical)
        ):
            from omnia_api.services import agent_builder

            # Phase 2: container EDITS go through the agent too (read→edit→build→
            # fix), not blind SEARCH/REPLACE — fixes the "точечные правки" pain.
            # First-build/rebuild = full build prompt; a surgical follow-up = a
            # minimal edit prompt with a smaller step budget.
            # CONTINUE («продолжи») on a non-first-build re-enters the BUILD loop to
            # finish a partial app (the agent reads the live container state), so it
            # is neither a from-scratch build nor a 12-step minimal edit.
            # «продолжи» only makes sense when there is a prior build to finish.
            # NB: `is_first_build` lives in the POST handler (post_prompt), NOT in
            # this worker fn — use the in-scope `current_snapshot_id` (None on a
            # brand-new project's first build) so this never NameErrors.
            _has_prior_build = current_snapshot_id is not None
            _is_continue = _has_prior_build and _is_continue_request(prompt_text)
            _is_edit = (not orchestrate) and not _is_continue

            async def _agent_emit(event: str, data: dict[str, Any]) -> None:
                # Surface each agent step as a STRUCTURED transcript event so the
                # frontend renders a live Claude-Code-style step list (tool + path)
                # instead of gray "[агент] …" text. One WS event type `agent.step`
                # carries every loop signal via `kind` (step/escalate/stalled/retry).
                kind = (event.rsplit(".", 1)[-1] or "step").lower()
                action = str(data.get("action", "") or "")
                if kind == "escalate":
                    action = action or f"усиливаю модель → {data.get('to', '')}"
                elif kind == "stalled":
                    action = action or "застрял — меняю подход"
                elif kind == "retry":
                    action = action or f"повтор запроса (#{data.get('attempt', 0)})"
                await publish_event(
                    project_id,
                    "agent.step",
                    {
                        "message_id": str(assistant_message_id),
                        "step": data.get("step"),
                        "kind": kind,
                        "action": action,
                        "path": str(data.get("path", "") or ""),
                    },
                )

            _agent_executor = agent_builder.make_container_executor(
                project_id=project_id, slug=project_slug
            )
            # Seed the agent with the project layout + the CrudResource component
            # up-front so it does NOT burn steps re-discovering the fixed template
            # (the #1 latency sink observed in the first live runs). Fail-soft.
            _seed_parts: list[str] = []
            try:
                _ents = await orchestrator_client.agent_list_dir(
                    project_id, project_slug, "entities")
                _seed_parts.append(f"entities/ contains:\n{_ents}")
                _dash = await orchestrator_client.agent_list_dir(
                    project_id, project_slug, "src/app/(app)/dashboard")
                _seed_parts.append(
                    f"src/app/(app)/dashboard/ contains:\n{_dash}")
                _crud = await orchestrator_client.agent_read_file(
                    project_id, project_slug,
                    "src/components/omnia/crud-resource.tsx")
                if _crud:
                    _seed_parts.append(
                        "src/components/omnia/crud-resource.tsx (the entity-page "
                        "component — render <CrudResource entity=\"Name\"/> in each "
                        "page):\n" + _crud[:4000])
            except Exception as _seed_exc:
                print(f"[PP] agent seed-context skipped: {_seed_exc!r}", flush=True)
            _seed_block = (
                "\n\nPROJECT CONTEXT (already gathered — do NOT re-explore these):\n"
                + "\n\n".join(_seed_parts)
            ) if _seed_parts else ""
            # Per-stack agent prompt: entities keep their finely-tuned prompt; any
            # OTHER container stack (realtime, …) gets the shared LOOP_PROTOCOL + its
            # own SYSTEM_PROMPT.md, so the agent builds it with the right primitives
            # (e.g. the realtime hub + members ACL) instead of the entity guide.
            _orch_name = orchestrator_template(project_template)
            _stack_guide = (
                agent_builder.load_stack_system_prompt(_orch_name)
                if _orch_name and _orch_name != "nextjs-entities"
                else None
            )
            _stack_system = (
                agent_builder.build_system_prompt(_stack_guide)
                if _stack_guide
                else agent_builder.SYSTEM_PROMPT
            )
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
                _agent_system = _stack_system
                _agent_steps = int(get_settings().agent_builder_max_steps)
            elif _is_edit:
                _sel_block = ""
                try:
                    if selected_elements:
                        _sel_block = (
                            "\n\nПользователь выделил элемент(ы) в превью: "
                            + str(selected_elements)[:800]
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
                _agent_system = agent_builder.EDIT_SYSTEM_PROMPT
                # Budget above the no-write/stall thresholds so the loop's
                # escalate-to-stronger-model actually fires before max_steps when a
                # cheap model explores without writing (the "Починить" did nothing bug).
                _agent_steps = 18
            else:
                _agent_user = (
                    f"Собери приложение по запросу пользователя:\n\n{prompt_text}\n\n"
                    f"Тип проекта: {project_template}.{_seed_block}\n\n"
                    f"Действуй: объяви нужные entities/<Name>.json, напиши страницы "
                    f"(включая обязательный dashboard/page.tsx индекс), затем build и "
                    f"чини ошибки до чистоты, затем done. Минимизируй разведку — "
                    f"раскладка выше уже дана."
                )
                _agent_system = _stack_system
                _agent_steps = int(get_settings().agent_builder_max_steps)
            # Cost-safe: dedicated `agent` role = deepseek-v4-pro (cheap, not the
            # opus 1-req/sec bottleneck). Swap via ROLE_MODELS env.
            _agent_model = model_for_role("agent", override=force_model)
            # Cheap default; the loop upgrades to this stronger model the first
            # time a stall-guard nudges (cycle / repeat / no-write) — smart only
            # when stuck, so cost stays bounded (no full strong-model run).
            _escalate_model = model_for_role("agent_escalation", override=force_model)
            _agent_res = await agent_builder.run_agent_build(
                system_prompt=_agent_system,
                user_prompt=_agent_user,
                model=_agent_model,
                escalate_model=_escalate_model,
                execute=_agent_executor,
                max_steps=_agent_steps,
                emit=_agent_emit,
                user_id=str(user_id),
                project_id=str(project_id),
                require_green_before_done=get_settings().agent_require_green_before_done,
            )
            _all_files = dict(_agent_res.files)
            _total_steps = _agent_res.steps
            # AUTO-CONTINUE (builds only): one run is capped at `_agent_steps`, but a
            # full first build usually needs more. Instead of stopping at that cap and
            # making the user click «Продолжить» against an arbitrary limit, keep
            # running SEGMENTS — each re-reads the live container it's been writing to
            # (finish-the-partial-build prompt) — until the agent calls done, OR a
            # whole segment writes NOTHING new (genuinely stuck → stop, don't spin),
            # OR the segment backstop (`agent_max_segments`) is hit. Only a max_steps
            # exit auto-continues; a stalled/looping/exploring/error exit means the
            # agent is stuck, so we stop and surface the «Продолжить» card.
            _max_segments = max(1, int(get_settings().agent_max_segments))
            _seg = 1
            while (
                not _is_edit
                and not _agent_res.done
                and _agent_res.stop_reason == "max_steps"
                and _seg < _max_segments
            ):
                await _agent_emit(
                    "agent.step",
                    {
                        "step": _total_steps,
                        "action": f"продолжаю сборку (этап {_seg + 1})",
                        "path": "",
                    },
                )
                _cont_res = await agent_builder.run_agent_build(
                    system_prompt=_stack_system,
                    user_prompt=(
                        "Приложение уже ЧАСТИЧНО собрано в этом проекте (часть файлов "
                        "уже на месте в контейнере). Доведи сборку ДО КОНЦА: запусти "
                        "build, посмотри текущие файлы, допиши недостающие "
                        "entities/<Name>.json и страницы (включая dashboard/page.tsx), "
                        "почини ошибки до чистоты, затем done. НЕ начинай с нуля и НЕ "
                        f"переписывай работающее.{_seed_block}"
                    ),
                    model=_agent_model,
                    execute=_agent_executor,
                    max_steps=_agent_steps,
                    emit=_agent_emit,
                    user_id=str(user_id),
                    project_id=str(project_id),
                    require_green_before_done=get_settings().agent_require_green_before_done,
                )
                _seg += 1
                _total_steps += _cont_res.steps
                _new = {p: c for p, c in _cont_res.files.items() if _all_files.get(p) != c}
                _all_files.update(_cont_res.files)
                _agent_res = _cont_res
                if not _new and not _cont_res.done:
                    print(f"[PP] agentic_build auto-continue stuck seg={_seg}", flush=True)
                    break

            files = _all_files

            # Layer C — backend-guardrail self-heal. The ban on writing backend is
            # lifted (the agent may author custom server logic), so STATICALLY
            # verify the one unsafe thing it must never do — reach the DB raw
            # (@/lib/db / drizzle / pg outside the engine), the only way to bypass
            # auth+ownership+membership. A static scan over the written files (cheap,
            # deterministic, no container). When USE_AGENT_GATE_FEEDBACK is on, any
            # escape is fed back to the agent to fix (bounded), so the model writes
            # freely and the GATE — not a template — guarantees safety. When off,
            # it is advisory-only (logs) → prod generation is byte-unchanged.
            try:
                from omnia_api.services import agent_gate_feedback as _agf
                from omnia_api.services.backend_guardrail import (
                    check_backend as _check_backend,
                )

                _guard_attempt = 0
                _guard_max = max(0, int(get_settings().agent_gate_max_attempts))
                while (
                    get_settings().use_agent_gate_feedback
                    and not _is_edit
                ):
                    _gv = _check_backend(files)
                    _outcomes = [
                        _agf.GateOutcome(
                            name="backend_guardrail",
                            passed=_gv.safe,
                            failures=[f"{v.path}: {v.rule}" for v in _gv.violations],
                        )
                    ]
                    _instr = _agf.build_fix_instruction(
                        _outcomes, _guard_attempt, _guard_max
                    )
                    if _instr is None:
                        break  # clean, or out of retry budget
                    _guard_attempt += 1
                    print(
                        f"[PP] agent_gate_feedback heal attempt={_guard_attempt} "
                        f"violations={len(_gv.violations)}",
                        flush=True,
                    )
                    _heal = await agent_builder.run_agent_build(
                        system_prompt=_agent_system,
                        user_prompt=_instr + _seed_block,
                        model=_escalate_model or _agent_model,
                        execute=_agent_executor,
                        max_steps=_agent_steps,
                        emit=_agent_emit,
                        user_id=str(user_id),
                        project_id=str(project_id),
                    )
                    files.update(_heal.files)
                # Advisory log regardless of the heal flag — operators SEE a raw-DB
                # escape even when self-heal is off (the silent-failure guard).
                _final_guard = _check_backend(files)
                if not _final_guard.safe:
                    print(
                        f"[PP] backend_guardrail VIOLATIONS: {_final_guard.summary}",
                        flush=True,
                    )
            except Exception as _guard_exc:  # never let the guardrail break a build
                print(f"[PP] agent_gate_feedback skipped: {_guard_exc!r}", flush=True)

            # Honest chat: never surface the raw internal summary on a non-done exit
            # (the "hit step budget without calling done" leak). See helper.
            accumulated = _agent_result_message(_agent_res, is_edit=_is_edit)
            print(
                f"[PP] agentic_build done={_agent_res.done} segs={_seg} "
                f"total_steps={_total_steps} files={len(files)} stop={_agent_res.stop_reason}",
                flush=True,
            )

            # Phase 3 — runtime smoke gate: a clean typecheck does NOT prove the
            # app actually serves. Probe the live container for a 5xx render and
            # surface it honestly instead of shipping a broken app as «готово».
            # Deterministic (one HTTP probe), fail-soft.
            _runtime_ok = True  # fail-soft default: a probe error ≠ a broken app
            try:
                _rt = await orchestrator_client.runtime_status(
                    project_id, slug=project_slug, path="/")
                _runtime_ok = bool(_rt.get("ok"))
                if not _runtime_ok:
                    _rt_err = _rt.get("error") or _rt.get("status_code") or "5xx"
                    accumulated += (
                        f"\n\n⚠️ Рантайм-проверка: приложение отвечает ошибкой "
                        f"({_rt_err}). Открой превью и нажми «Починить» или уточни запрос."
                    )
                    print(f"[PP] agentic_smoke runtime FAIL {_rt.get('status_code')}", flush=True)
                else:
                    print("[PP] agentic_smoke runtime ok", flush=True)
            except Exception as _sm_exc:
                print(f"[PP] agentic_smoke skipped: {_sm_exc!r}", flush=True)

            # Honesty gate: a page that SERVES can still be typecheck-RED — a client
            # TS error (e.g. TS2739 «missing columns, fields») is NOT a 5xx, so the
            # smoke probe is green while the app is broken. The «looped-but-serves»
            # override below must NOT report «Готово» on a red typecheck (the
            # "делает вид что работает" lie). Run one real typecheck; on a DEFINITE
            # red, surface the first error honestly + keep the fixable card. Fail-soft:
            # an agent_build exception leaves _typecheck_ok=True (unknown ≠ broken).
            _typecheck_ok = True
            try:
                _tc = await orchestrator_client.agent_build(project_id, project_slug)
                _typecheck_ok = bool(_tc.get("ok", True))
                if not _typecheck_ok:
                    _tc_detail = str(_tc.get("detail") or "").strip()
                    _tc_first = (
                        _tc_detail.splitlines()[0][:240]
                        if _tc_detail
                        else "ошибка типизации"
                    )
                    accumulated += (
                        f"\n\n⚠️ Почти готово, но осталась ошибка: {_tc_first}. "
                        f"Нажми «Починить» — доведу до чистоты."
                    )
                    print(f"[PP] agentic_typecheck RED: {_tc_first}", flush=True)
                else:
                    print("[PP] agentic_typecheck clean", flush=True)
            except Exception as _tc_exc:
                print(f"[PP] agentic_typecheck skipped: {_tc_exc!r}", flush=True)

            # Design DNA — give this entity/agent app a DISTINCT identity (seeded
            # accent + font pairing) so it stops looking identical to every other
            # one ("дизайн одинаковый"). The agent never writes globals.css (it is
            # baked), so we inject a per-project :root override into the container's
            # globals.css directly. Reuses the curated, WCAG-checked, per-project
            # seeded tokens; touches only safe brand knobs (never the canvas
            # neutrals). Fail-soft: any error leaves the default theme.
            if not _is_edit:
                try:
                    from omnia_api.services import design_dna

                    _gcss = await orchestrator_client.agent_read_file(
                        project_id, project_slug, "src/app/globals.css"
                    )
                    if _gcss:
                        _newg = design_dna.inject_into_globals(
                            _gcss, str(project_id), None
                        )
                        if _newg != _gcss:
                            await orchestrator_client.hot_reload(
                                project_id,
                                project_slug,
                                {"src/app/globals.css": _newg},
                            )
                            print("[PP] design_dna injected globals.css", flush=True)
                except Exception as _dd_exc:
                    print(f"[PP] design_dna skipped: {_dd_exc!r}", flush=True)

            # Honest result: a loop-guard abort (looping/exploring) that STILL
            # wrote files, whose app SERVES, AND whose typecheck is CLEAN is not a
            # failure — the work landed, the guard just tripped. Don't scare the
            # user with «Сборка прервана» when the preview is live and clean. A red
            # typecheck (above) excludes this path → no false «Готово».
            if (
                not _agent_res.done
                and _agent_res.stop_reason in ("looping", "exploring")
                and files
                and _runtime_ok
                and _typecheck_ok
            ):
                accumulated = (
                    "Готово — правка применена."
                    if _is_edit
                    else "Готово — приложение собрано."
                )
                print(
                    "[PP] agentic_build looped-but-serves → reported as done",
                    flush=True,
                )

            # Runtime functional gate — the behavioural "works + does not leak" proof
            # (research finding: this gate was defined+unit-tested but UNWIRED; only
            # the static guardrail ran). Drive the live realtime preview and feed a red
            # verdict back to the agent as a BLOCKING outcome so a broken/leaky feature
            # self-heals BEFORE the snapshot lands — a clean typecheck is exactly what a
            # model hallucinates completion around. Realtime-only (the functional gate
            # is self-contained), fail-soft, off by default → prod ship unchanged.
            try:
                if (
                    get_settings().use_runtime_gates
                    and not _is_edit
                    and _orch_name == "nextjs-realtime"
                ):
                    from omnia_api.services import agent_gate_feedback as _agf2
                    from omnia_api.services.functional_gate import run_functional_gate

                    _rg_attempt = 0
                    _rg_max = max(0, int(get_settings().agent_gate_max_attempts))
                    while True:
                        _st = await orchestrator_client.get_status(project_id)
                        _base = _st.get("dev_url") if isinstance(_st, dict) else None
                        if not _base:
                            print("[PP] runtime_gate: no dev_url — skip", flush=True)
                            break
                        _fv = await run_functional_gate(_base)
                        _fout = [
                            _agf2.outcome_from_checks(
                                "functional", _fv.passed, _fv.checks
                            )
                        ]
                        _fix = _agf2.build_fix_instruction(_fout, _rg_attempt, _rg_max)
                        if _fix is None:
                            if not _fv.passed:
                                accumulated += (
                                    "\n\n⚠️ Проверка работоспособности/безопасности не "
                                    "прошла — открой превью и уточни запрос."
                                )
                                print(
                                    "[PP] runtime_gate functional FAIL (out of attempts)",
                                    flush=True,
                                )
                            else:
                                print("[PP] runtime_gate functional PASS", flush=True)
                            break
                        _rg_attempt += 1
                        print(
                            f"[PP] runtime_gate functional heal attempt={_rg_attempt}",
                            flush=True,
                        )
                        _fheal = await agent_builder.run_agent_build(
                            system_prompt=_stack_system,
                            user_prompt=_fix + _seed_block,
                            model=_escalate_model or _agent_model,
                            execute=_agent_executor,
                            max_steps=_agent_steps,
                            emit=_agent_emit,
                            user_id=str(user_id),
                            project_id=str(project_id),
                        )
                        files.update(_fheal.files)
            except Exception as _rg_exc:  # a gate must never crash the build
                print(f"[PP] runtime_gate skipped: {_rg_exc!r}", flush=True)

            if files:
                new_sha = await asyncio.to_thread(
                    repo_svc.commit_files,
                    project_id,
                    files,
                    f"AI(agent): {prompt_text[:50]}",
                    current_sha,
                )
                async with factory() as session:
                    snapshot = Snapshot(
                        project_id=project_id,
                        commit_sha=new_sha,
                        prompt_text=prompt_text,
                        model_id=model_id,
                        parent_id=current_snapshot_id,
                    )
                    session.add(snapshot)
                    await session.flush()
                    _agent_snap_id = snapshot.id
                    project = await session.get(Project, project_id)
                    if project is not None:
                        project.current_snapshot_id = snapshot.id
                    msg = await session.get(Message, assistant_message_id)
                    if msg is not None:
                        msg.content = accumulated
                        msg.snapshot_id = snapshot.id
                        msg.tokens_out = msg.tokens_out or 0
                    if is_free:
                        user_row = await session.get(User, user_id)
                        if user_row is not None:
                            user_row.free_generations_used = (
                                user_row.free_generations_used or 0
                            ) + 1
                    await session.commit()
                    await session.refresh(snapshot)
                await asyncio.to_thread(enqueue_preview, _agent_snap_id)
                await publish_event(
                    project_id,
                    "snapshot.created",
                    {"snapshot": _snapshot_payload(snapshot)},
                )
            else:
                # Nothing written — mark the row done so the UI unblocks.
                async with factory() as session:
                    msg = await session.get(Message, assistant_message_id)
                    if msg is not None:
                        msg.content = accumulated
                        msg.tokens_out = msg.tokens_out or 0
                    await session.commit()

            # Resumable partial build: the agent ran out of step budget without
            # calling done, but any partial files already committed above. Surface
            # a NEUTRAL «Продолжить» card (not an error) — the web UI renders it
            # amber with a button that sends «продолжи», which _is_continue_request
            # routes back into the build loop on the live container. Published AFTER
            # the msg.content=accumulated overwrite above (else it'd be wiped) and
            # BEFORE llm.done so the card both persists and animates in.
            if (
                not getattr(_agent_res, "done", False)
                and getattr(_agent_res, "stop_reason", "") == "max_steps"
            ):
                await app_errors.publish(
                    factory,
                    project_id,
                    assistant_message_id,
                    category="incomplete",
                    title="Сборка не завершена",
                    detail=(
                        "Агент исчерпал лимит шагов, не закончив. Часть уже сохранена "
                        "— нажми «Продолжить», доделаю с того же места, не начиная заново."
                    ),
                    fixable=True,
                )

            await publish_event(
                project_id,
                "llm.done",
                {
                    "message_id": str(assistant_message_id),
                    "tokens_in": None,
                    "tokens_out": None,
                    "cost_rub": None,
                },
            )
            await clear_stream_state(project_id, assistant_message_id)
            return

        messages = build_messages(
            current_files,
            history_serialized,
            prompt_text,
            project_template,
            selected_elements,
            preset_id=project_design_preset_id,
            image_gen_enabled=project_image_gen_enabled,
            # Stable seed for the `ui-ux-pro-max` UX-guidelines sample —
            # re-prompts inside one project always surface the same rules.
            project_id=str(project_id),
            # Phase F.2 — flows the model id to the prompt assembler so it
            # trims detail-polish blocks for budget/balanced tiers. Budget
            # single-shot (multipass disabled) gets ~6 KB shorter prompt;
            # balanced gets ~3 KB shorter; premium keeps the full brief.
            model_id=model_id,
            # Lean edit-only prompt (preserve everything, surgical <edit>) when
            # the triage routed this to a cheap targeted edit.
            edit_mode=surgical,
            # V2.5c — generation-side of the chip→design causality bridge: the
            # persisted onboarding chip choices steer the writer's palette /
            # theme / sections (the gauntlet already JUDGES against this spec).
            discovery_spec=project_discovery_spec,
            # Phase A3 — language parameterisation.  "ru" is the default;
            # non-RU injects a top-of-prompt OVERRIDE so all content is
            # generated in the project language instead of Russian.
            language=project_language,
            # B4 — imported repos use a stack-neutral generic edit identity
            # instead of the Omnia static/Next.js-specific ones.
            is_imported=project_is_imported,
        )
        print(f"[PP] messages_built count={len(messages)} surgical={surgical}", flush=True)

        # Phase 11 — resolve the generation mode ONCE from the routing model so
        # the prompt we built (freeform vs catalog) and the way we parse the
        # answer below never disagree. The empty-response fallback may switch
        # the model later, but the mode is fixed by the first pass's prompt.
        from omnia_api.core.config import generation_mode as _generation_mode
        _gen_mode = _generation_mode(model_id, str(project_id))
        # Non-web templates never emit a web PageIR. `code` (any-language source)
        # and the Python backends (`tgbot`/`api`) write source via <file> blocks,
        # so force them off catalog mode — must match build_messages' own guard so
        # the prompt we built and the way we parse below agree (owner 2026-06-18).
        _is_code = project_template == "code"
        if _gen_mode == "catalog" and project_template in ("code", "tgbot", "api"):
            _gen_mode = "freeform"
        print(f"[PP] gen_mode={_gen_mode}", flush=True)
        if pipeline_debug.enabled():
            try:
                _s = get_settings()
                pipeline_debug.dump(
                    project_id,
                    assistant_message_id,
                    "_route.md",
                    f"preset_id={project_design_preset_id}\n"
                    f"gen_mode={_gen_mode}\n"
                    f"model_id={model_id}\n"
                    f"template={project_template}\n"
                    f"image_gen_enabled={project_image_gen_enabled}\n"
                    f"use_art_director_freeform={_s.use_art_director_freeform}\n"
                    f"use_visual_enricher={_s.use_visual_enricher}\n"
                    f"use_acceptance_gate={_s.use_acceptance_gate}\n"
                    f"acceptance_score_only={_s.acceptance_score_only}\n"
                    f"acceptance_min_score={_s.acceptance_min_score}\n"
                    f"use_section_catalog={_s.use_section_catalog}\n"
                    f"use_originality={_s.use_originality}\n"
                    f"use_vision_audit={_s.use_vision_audit}\n"
                    f"use_design_judge={_s.use_design_judge}\n",
                )
            except Exception as _rt_exc:
                print(f"[PP] debug_route_failed {_rt_exc!r}", flush=True)

        # ──────────────────────────────────────────────────────────────
        # Inner stream loop, extracted so we can retry the whole thing
        # against a fallback model when the primary returns junk.
        # `accumulated` and `usage_data` are mutated through closure refs
        # via the dict trick — Python doesn't let us rebind outer names
        # cleanly from a nested coroutine.
        # ──────────────────────────────────────────────────────────────
        state: dict[str, object] = {
            "accumulated": "",
            "usage": None,
            "error": None,
            # Set True once the freeform (Art-Director → Writer) path runs, so the
            # two post-writer stages below (image-resolve, design-judge) emit their
            # own llm.pass events ONLY for a freeform build — never for edits,
            # catalog or multipass, where the progress bar uses a different stage
            # set. See _emit_stage.
            "freeform": False,
        }

        # Phase B.3 — surface the two post-writer freeform stages (Картинки,
        # Проверка) on the SAME llm.pass channel the Art-Director/Writer passes
        # use, so PassProgressBar fills all four segments (Замысел → Вёрстка →
        # Картинки → Проверка) instead of stalling at 2/4. Gated on the freeform
        # build path and best-effort — a progress ping must never abort a build.
        async def _emit_stage(pass_name: str, stage: str) -> None:
            if not state.get("freeform"):
                return
            try:
                await publish_event(
                    project_id,
                    "llm.pass",
                    {
                        "message_id": str(assistant_message_id),
                        "pass": pass_name,
                        "stage": stage,
                    },
                )
            except Exception:
                pass

        # Phase B — multipass for budget models is ON by default.
        # `effective_multipass_models` = CHEAP_MODELS ∪ env override.
        # First-time user on Haiku/Nano gets the 4-pass enterprise output
        # without any env setup. Operator can ADD non-budget models via
        # `MULTIPASS_MODELS=gpt-5-mini,…` or kill the whole pipeline with
        # `MULTIPASS_MODELS=off`. See services/multipass_generator.py.
        multipass_set = get_settings().effective_multipass_models

        async def _run_stream(
            use_model: str,
            *,
            force_multipass: bool = False,
            force_single_shot: bool = False,
            force_all: str | None = None,
            allow_art_director: bool = True,
        ) -> None:
            """Drain one stream from the gateway into `state`.

            For models in the multipass set (or when the caller explicitly
            passes `force_multipass=True`), runs through the multi-pass
            generator (skeleton → assembly) instead of a single shot. Both
            paths yield the same event shape downstream, so the file
            extractor and fallback loop don't care which one ran.

            `force_multipass=True` is the A.5 escape hatch — when a single
            shot returns junk we retry the *same* model via multipass before
            switching models, because the failure mode is usually
            prompt-overwhelm (cheap model loses focus over 28+ KB) rather
            than the model being incapable.

            `force_single_shot=True` pins the plain single-shot path even for
            a cheap model that would otherwise be in the multipass set. Used
            by targeted fix passes (dead-link repair) that must edit the
            existing files, not regenerate the whole page via multipass.
            """
            state["accumulated"] = ""
            state["usage"] = None
            state["error"] = None

            # Phase L7 — Director→Polish 2-pass branch. Wins when the
            # operator has opted into both `USE_SECTION_CATALOG` AND
            # `USE_DIRECTOR_POLISH`, AND the model is in the premium
            # tier. Catalog already gives us JSON IR; Director→Polish
            # splits structural choice and content polish across two
            # calls of the same model for higher final quality at the
            # cost of latency × 2 / tokens × 2.
            _settings = get_settings()
            # Director→Polish is the standard catalog path. `force_all` is None on
            # the orchestrated first pass (each pass uses its role model — Opus
            # director, DeepSeek polish) and is set to a single model id when an
            # admin override or an empty-response fallback forces ONE model across
            # every pass. The premium gate still holds: the routing model is the
            # director (Opus).
            # `orchestrate` is the triage verdict (closure from _process_prompt).
            # When False (a cheap targeted edit) we skip BOTH Director→Polish AND
            # the 4-pass multipass and run a single reliable shot — so "recolour
            # the button" never spins up the premium pipeline and burns budget.
            # FIXED build orchestration (owner 2026-06-01): Art-Director (Opus)
            # writes the ultra-detailed brief, Writer (DeepSeek) executes it into
            # HTML. Wins for EVERY orchestrated build when the flag is on —
            # independent of model tier, so the design pipeline is never silently
            # downgraded to plain/catalog. A forced single model (admin override /
            # empty-response fallback → force_all) or a cheap targeted edit
            # (orchestrate=False) skips it and keeps its own single model.
            _adw_active = (
                allow_art_director
                and not force_single_shot
                and not force_all
                and orchestrate
                and _settings.use_art_director_freeform
                # The writer emits freeform HTML, so only run when the pipeline
                # parses HTML (freeform). On prod freeform = 100%, so this is
                # always-on for builds; it just avoids feeding HTML to the
                # catalog/plain JSON parser if freeform is ever switched off.
                and _gen_mode == "freeform"
                # Container-backed stacks WITHOUT a .tsx writer variant
                # (fullstack / spa) stay off this path: the art-director writer's
                # default pass emits ONE static index.html, the wrong artifact for
                # a React app. But app stacks that DO have a dedicated .tsx writer
                # (nextjs_entities — art_director_writer._APP_TEMPLATES /
                # _WRITER_INSTRUCTION_TEMPLATE_APP) get the SAME 2-pass art-
                # direction: an APP brief (oklch theme tokens + IA) → a .tsx writer
                # that honours it → the omnia:brief event. Without this the
                # flagship entity apps fell through to a bare single-shot .tsx —
                # no brief, hardcoded colours, dead narration/swatches. Flag =
                # instant rollback to that single-shot path.
                and (
                    project_template not in CONTAINER_NEXT
                    or (
                        _settings.use_art_director_entities
                        and supports_app_brief(project_template)
                    )
                )
            )
            _dp_active = (
                not force_single_shot
                and orchestrate
                and _settings.use_director_polish
                and _settings.use_section_catalog
                and tier_for_model(use_model) == "premium"
                # Freeform mode writes HTML directly — no Director→Polish 2-pass
                # (that path is a catalog/IR enhancement). The acceptance gate
                # is freeform's quality mechanism instead.
                and _gen_mode != "freeform"
                # Catalog/IR (Director→Polish) renders static HTML — never for a
                # container-backed Next.js app, which needs .tsx files.
                and project_template not in CONTAINER_NEXT
            )
            if _adw_active:
                # Mark the freeform path so the post-writer image-resolve and
                # design-judge stages emit their llm.pass progress (4-segment bar).
                state["freeform"] = True
                # Infra cost (2026-06-16): pass 1 (Art-Director, prose brief) gets
                # a brief-lean system that drops the code-implementation blocks it
                # never uses; pass 2 (the writer) keeps the FULL `messages` system,
                # so the final HTML is unchanged. Fail-soft → full prompt on any
                # error or when the flag is off.
                _ad_system: str | None = None
                if _settings.use_lean_art_director_prompt:
                    try:
                        _ad_system = build_art_director_system(
                            project_template,
                            project_design_preset_id,
                            project_image_gen_enabled,
                            model_id=model_id,
                            project_id=str(project_id),
                            user_prompt=prompt_text,
                            discovery_spec=project_discovery_spec,
                        )
                    except Exception as _ad_exc:
                        _log.warning(
                            "lean art-director prompt failed, using full: %r", _ad_exc
                        )
                        _ad_system = None
                source = art_director_writer_generate(
                    base_messages=messages,
                    user_prompt=prompt_text,
                    art_director_model=model_for_role("art_director", override=force_model),
                    writer_model=model_for_role("freeform_writer", override=force_model),
                    user_id=user_id,
                    project_id=project_id,
                    message_id=assistant_message_id,
                    template=project_template,
                    art_director_system=_ad_system,
                    language=project_language,
                )
            elif _dp_active:
                source = director_polish_generate(
                    base_messages=messages,
                    user_prompt=prompt_text,
                    director_model=force_all,
                    polish_model=force_all,
                    user_id=user_id,
                    project_id=project_id,
                    message_id=assistant_message_id,
                    language=project_language,
                )
            elif (
                not force_single_shot
                and orchestrate
                and (force_multipass or use_model in multipass_set)
            ):
                source = multipass_generate(
                    base_messages=messages,
                    user_prompt=prompt_text,
                    model=force_all,
                    user_id=user_id,
                    project_id=project_id,
                    message_id=assistant_message_id,
                )
            else:
                # B2 — the `single_shot` role (Opus) owns the non-catalog
                # freeform fallback: with catalog/IR OFF an orchestrated build
                # still needs one strong model. With catalog ON (prod default)
                # this branch only runs for cheap targeted edits / forced
                # single-shot fixes, which keep their own model — single_shot
                # must never drag a "recolour the button" tweak onto Opus.
                if (
                    not force_all
                    and not force_single_shot
                    and orchestrate
                    and not _settings.use_section_catalog
                ):
                    _ss_model = model_for_role("single_shot", override=force_model)
                elif (
                    not force_all
                    and not force_single_shot
                    and orchestrate
                    and _gen_mode == "freeform"
                ):
                    # The leap: premium freeform writes the whole page as bespoke
                    # HTML — the design-critical pass — so it runs on the strongest
                    # model via the `freeform_writer` role (Opus), not whatever the
                    # route resolved. Same guards as single_shot above: a cheap
                    # targeted edit (force_* / not orchestrate) never lands here, so
                    # a small tweak can't drag onto Opus.
                    _ss_model = model_for_role("freeform_writer", override=force_model)
                else:
                    _ss_model = force_all or use_model
                # Per-vendor directive on the freeform/single-shot path. IR JSON
                # is expected ONLY on premium + catalog mode; otherwise the model
                # emits freeform HTML, so json_strict must stay False (a "JSON
                # only" nudge would corrupt an HTML response).
                # IR JSON is expected ONLY on premium + catalog mode. Freeform
                # emits HTML, so a "JSON only" vendor nudge would corrupt the
                # response — never set json_strict in freeform mode.
                _expects_ir = (
                    _settings.use_section_catalog
                    and _gen_mode != "freeform"
                    and tier_for_model(_ss_model) == "premium"
                    # Fullstack/entity apps emit .tsx <file> blocks, never PageIR
                    # JSON — a "JSON only" vendor nudge would corrupt the response.
                    and project_template not in CONTAINER_NEXT
                )
                source = stream_chat_completion(
                    _with_vendor_directive(messages, _ss_model, json_strict=_expects_ir),
                    _ss_model,
                    str(user_id),
                    str(project_id),
                    str(assistant_message_id),
                )

            async for event in source:
                if "delta" in event:
                    state["accumulated"] = str(state["accumulated"]) + event["delta"]
                    pub["seq"] = int(pub["seq"]) + 1
                    pub["content"] = str(pub["content"]) + event["delta"]
                    await publish_event(
                        project_id,
                        "llm.chunk",
                        {
                            "message_id": str(assistant_message_id),
                            "delta": event["delta"],
                            # Monotonic per-message counter — lets a reconnecting
                            # client dedup buffered vs live deltas and detect gaps.
                            "seq": int(pub["seq"]),
                        },
                    )
                    # Mirror the cumulative client-visible content into Redis on
                    # every delta so a reconnect (F5) resyncs to the exact current
                    # state — no hole between the buffer and live deltas. Same-host
                    # Redis: a ≤tens-of-KB SET at stream rate is cheap. Best-effort:
                    # the buffer is only a reconnect aid, so a write hiccup must
                    # never abort a stream that's otherwise delivering fine.
                    try:
                        await set_stream_state(
                            project_id,
                            assistant_message_id,
                            str(pub["content"]),
                            int(pub["seq"]),
                        )
                    except Exception:
                        pass
                elif "usage" in event:
                    state["usage"] = event["usage"]
                elif "error" in event:
                    state["error"] = event["error"]
                    return
                elif "pass" in event:
                    # B.3 — pass-progress events. Fan out via WS so the
                    # frontend can show "Шаг 1/2: Структура" / "Шаг 2/2:
                    # Сборка" indicators. Frontend wiring deferred — this
                    # publishes the channel today so the UI patch is a
                    # one-file change later.
                    await publish_event(
                        project_id,
                        "llm.pass",
                        {
                            "message_id": str(assistant_message_id),
                            "pass": event["pass"],
                            "stage": event["stage"],
                            **{
                                k: v
                                for k, v in event.items()
                                if k not in ("pass", "stage")
                            },
                        },
                    )
                elif "brief" in event:
                    # V3.10a — surface the art-director brief (palette / fonts /
                    # motion / sections) to the client on the same channel as
                    # llm.chunk/llm.pass, so the live render can narrate the
                    # design reasoning as it builds (Pillar 3 → V3.10). Debug-
                    # only before; now flows as an event. Lands before llm.done.
                    # v2.21 #1(A): also stash it so the deterministic pre-commit
                    # step can BAKE it into the shared static page → a stranger
                    # on /p/<slug> sees the same birth reveal (ONE BRIEF, EVERY
                    # SURFACE). See services/brief_narration.inject_brief_narration.
                    state["brief"] = event["brief"]
                    await publish_event(
                        project_id,
                        "omnia:brief",
                        {
                            "message_id": str(assistant_message_id),
                            **event["brief"],
                        },
                    )

        # ── Direct image generation — call the GRAPHICS model, not the text LLM.
        # When the user points at a zone and asks for a picture, build a
        # guaranteed-matching <edit> server-side: SEARCH is the EXACT <img> from
        # source (so it always applies), REPLACE swaps it for a fresh
        # data-omnia-gen tag. image_resolver below turns it into a real flux
        # photo. The only LLM is a cheap image-prompt craft — no HTML rewrite,
        # no risk of the text model mangling the edit.
        _direct_image_edit: str | None = None
        _img_req = image_edit.is_image_request(prompt_text)
        # A "new background" request ("сделай новый фон" / "поменяй фон", no named
        # colour) also routes here — IF the clicked zone actually has a full-bleed
        # bg image to regenerate. The deterministic server-built <edit> avoids the
        # text model reconstructing the (already-changed) hero and missing every
        # SEARCH — exactly the "ничего не сделал" the owner hit on "сделай новый фон".
        _bg_req = image_edit.is_background_request(prompt_text)
        if (
            surgical
            and selected_elements
            and project_image_gen_enabled
            and current_files.get("index.html")
            and (_img_req or _bg_req)
        ):
            _idx_src = current_files["index.html"]
            _z = zone_edit.find_enclosing_block(
                _idx_src, zone_edit.distinctive_anchors(selected_elements)
            )
            _scope = _idx_src[_z[0] : _z[1]] if _z else _idx_src
            _img_hit = image_edit.find_first_img(_scope)
            _bg = _img_hit is not None and image_edit.is_fullbleed_bg(_img_hit[2])
            # A pure background request only takes this path when the target really
            # is a full-bleed bg image; otherwise "поменяй фон" is a colour edit and
            # belongs on the normal edit path.
            if _img_hit is not None and (_img_req or _bg):
                _old_img_tag = _img_hit[2]
                _gp, _gp_usage = await _craft_image_prompt(
                    prompt_text,
                    project_design_preset_id,
                    _old_img_tag,
                    force_model,
                    user_id,
                    project_id,
                    assistant_message_id,
                )
                _new_img_tag = image_edit.rebuild_img_with_gen(_old_img_tag, _gp)
                _sr_pairs: list[tuple[str, str]] = [(_old_img_tag, _new_img_tag)]
                if _bg:
                    # A full-bleed bg image stays invisible behind the heavy dark
                    # overlay AND the WebGL shader — lighten the masking gradient(s)
                    # and dim the shader in the same zone so the regenerated image
                    # is actually seen (text stays readable).
                    _sr_pairs.extend(image_edit.lighten_overlay_edits(_scope))
                    _sr_pairs.extend(image_edit.dim_shader_edits(_scope))
                _blocks = "".join(
                    f"<<<<<<< SEARCH\n{_s}\n=======\n{_r}\n>>>>>>> REPLACE\n"
                    for _s, _r in _sr_pairs
                )
                _direct_image_edit = (
                    (
                        "Генерирую новое фоновое изображение и осветляю затемнение, "
                        "чтобы оно было видно.\n"
                        if _bg
                        else "Генерирую новое изображение для выделенной зоны.\n"
                    )
                    + f'<edit path="index.html">\n{_blocks}</edit>\n'
                )
                if _gp_usage:
                    usage_data = _gp_usage  # type: ignore[assignment]
                print(
                    f"[PP] direct_image edit built bg={_bg} pairs={len(_sr_pairs)} "
                    f"gp={_gp[:60]!r}",
                    flush=True,
                )

        # --- Pass 1: primary model (or the server-built image edit) ---
        if _direct_image_edit is not None:
            accumulated = _direct_image_edit
            stream_error = None
            await publish_event(
                project_id,
                "llm.chunk",
                {
                    "message_id": str(assistant_message_id),
                    "delta": "*Генерирую изображение для выделенной зоны…*\n\n",
                },
            )
        else:
            await _run_stream(model_id, force_all=force_model)
            accumulated = str(state["accumulated"])
            usage_data = state["usage"]  # type: ignore[assignment]
            stream_error = state["error"]

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
                assistant_message_id,
                err_body,
                usage_data or {"tokens_in": 0, "tokens_out": 0},
                snapshot_id=None,
            )
            await publish_event(
                project_id,
                "llm.error",
                {"message_id": str(assistant_message_id), "error": str(stream_error)},
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
        from omnia_api.core.config import get_settings as _get_settings
        # Only catalog mode parses the answer as PageIR JSON. Freeform/plain
        # emit HTML in <file> blocks and fall straight through to the extractor.
        # Container-backed Next.js apps emit .tsx <file> blocks, not PageIR JSON —
        # never run the catalog/IR parser on them (it would discard the files and
        # try to render a static index.html for a React app).
        if _gen_mode == "catalog" and project_template not in CONTAINER_NEXT:
            import json as _json

            from pydantic import ValidationError as _ValidationError

            from omnia_api.sections import PageIR, render_page  # noqa: F401
            from omnia_api.sections.renderer import render_to_files
            raw = accumulated.strip()
            # Strip ```json fences the model may have added despite instructions.
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            try:
                ir_dict = _json.loads(raw)
                ir = PageIR.model_validate(ir_dict)
                # Phase L8 — Smart Defaults engine: fill weak/null fields
                # (CTA hrefs → conversion anchor, palette → preset HEX,
                # pricing featured tier, footer copyright with year,
                # favicon per industry, dark_mode coercion, anchor dedup).
                # Pure + idempotent — safe on every IR. preset_id is the
                # classifier's pinned preset for this project.
                from omnia_api.sections import apply_smart_defaults
                ir = apply_smart_defaults(ir, preset_id=project_design_preset_id)
                # Reuse the existing omnia-kit on disk for the project's template.
                kit_css = current_files.get("src/assets/omnia-kit.css", "")
                kit_js = current_files.get("src/assets/omnia-kit.js", "")
                rendered = render_to_files(ir, kit_css=kit_css, kit_js=kit_js)
                # Re-pack into the <file path="..."> format the downstream
                # extractor expects. Order matters: index.html first.
                blocks = [
                    f'<file path="{p}">\n{c}\n</file>' for p, c in rendered.items()
                ]
                accumulated = "\n".join(blocks)
                print(
                    f"[PP] catalog_ir_ok sections={len(ir.sections)} "
                    f"html_len={len(rendered.get('index.html', ''))}",
                    flush=True,
                )
            except (_json.JSONDecodeError, _ValidationError, ValueError) as ir_exc:
                _log.warning(
                    "catalog IR parse/validate failed; retrying IR once with the "
                    "director model before falling back to freeform: %r",
                    ir_exc,
                )
                print(f"[PP] catalog_ir_fail err={ir_exc!r}", flush=True)
                # HARDENING (Phase M) — the polish model (cheap, gpt-5-nano) emitted
                # schema-invalid PageIR. Retry the IR ONCE with the strong director
                # model (Opus reliably holds the strict schema). Not streamed to the
                # user (no raw JSON in chat); on success we swap `accumulated` for the
                # rendered <file> blocks so a real site always ships. The free-gen
                # contextvar rides along, so this retry is not billed on a free gen.
                from omnia_api.core.config import model_for_role as _model_for_role
                try:
                    _retry_parts: list[str] = []
                    _retry_usage: dict | None = None
                    async for _rev in stream_chat_completion(
                        messages,
                        _model_for_role("director"),
                        str(user_id),
                        str(project_id),
                        str(assistant_message_id),
                    ):
                        if "delta" in _rev:
                            _retry_parts.append(str(_rev["delta"]))
                        elif "usage" in _rev:
                            _retry_usage = _rev["usage"]  # type: ignore[assignment]
                    _raw2 = "".join(_retry_parts).strip()
                    if _raw2.startswith("```"):
                        _raw2 = _raw2.split("\n", 1)[1] if "\n" in _raw2 else _raw2[3:]
                        if _raw2.endswith("```"):
                            _raw2 = _raw2[:-3]
                        _raw2 = _raw2.strip()
                    _ir2 = PageIR.model_validate(_json.loads(_raw2))
                    from omnia_api.sections import apply_smart_defaults as _asd
                    _ir2 = _asd(_ir2, preset_id=project_design_preset_id)
                    # NB: kit_css/kit_js are assigned in the try-block AFTER the
                    # validate that just failed, so they're unbound here — fetch
                    # them fresh from current_files (always in scope).
                    _kit_css = current_files.get("src/assets/omnia-kit.css", "")
                    _kit_js = current_files.get("src/assets/omnia-kit.js", "")
                    _rendered2 = render_to_files(_ir2, kit_css=_kit_css, kit_js=_kit_js)
                    accumulated = "\n".join(
                        f'<file path="{p}">\n{c}\n</file>' for p, c in _rendered2.items()
                    )
                    if _retry_usage:
                        usage_data = _retry_usage  # type: ignore[assignment]
                    print(
                        f"[PP] catalog_ir_recovered_via_director sections={len(_ir2.sections)}",
                        flush=True,
                    )
                except Exception as _ir_exc2:
                    print(f"[PP] catalog_ir_retry_failed err={_ir_exc2!r}", flush=True)
                    # Fall through: the empty-content retry machinery (multipass /
                    # model-switch) downstream is the final fallback.

        try:
            files, edit_conflicts = _extract_files_and_edits(
                accumulated, current_files
            )
            if edit_conflicts:
                _log.warning(
                    "edit conflicts on first pass: %s", edit_conflicts[:5]
                )
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
                factory, assistant_message_id, _final_err, usage_data, snapshot_id=None
            )
            await publish_event(
                project_id,
                "llm.error",
                {"message_id": str(assistant_message_id), "error": str(e)},
            )
            return

        # --- Surgical retry: edit produced no applicable patch ----------
        # In edit mode the model returns <edit> SEARCH/REPLACE. If the SEARCH
        # didn't match the file byte-for-byte (or the markers were dropped),
        # `files` is empty even though `accumulated` is long (so the truncation
        # fallback below won't fire). Re-ask ONCE with a "copy exact bytes" nudge
        # before giving up — cheaper and far less surprising than shipping a full
        # rewrite. Stays single-shot on the same cheap model.
        if surgical and not files and accumulated.strip():
            retry_note = (
                "Правка не применилась: SEARCH-блок не совпал с файлом побайтно "
                "или не нашёлся уникально. Повтори ответ — найди нужный фрагмент в "
                "показанном текущем файле, скопируй его в SEARCH ТОЧНО как есть (те "
                "же пробелы, переводы строк, кавычки) и добавь 1–2 соседние строки "
                "для уникальности. Только <edit>, меняй ровно запрошенное, остальное "
                "не трогай."
            )
            messages.append({"role": "assistant", "content": accumulated})
            messages.append({"role": "user", "content": retry_note})
            print("[PP] surgical_retry (no patch applied) -> re-ask", flush=True)
            # Escalate the retry to a stronger reasoning model — it reproduces
            # byte-exact SEARCH blocks far more reliably than the cheap edit model
            # (restores d61214b, clobbered by 2133cfd on a stale base).
            _esc_model = model_for_role("edit_escalation", override=force_model)
            await _run_stream(
                _esc_model, force_single_shot=True, force_all=_esc_model
            )
            _retry_acc = str(state["accumulated"])
            if _retry_acc.strip():
                accumulated = accumulated + "\n" + _retry_acc
                if state["usage"] and isinstance(state["usage"], dict):
                    usage_data = state["usage"]  # type: ignore[assignment]
                try:
                    files, _ = _extract_files_and_edits(_retry_acc, current_files)
                except (UnsafePathError, ValueError):
                    files = {}
            print(f"[PP] surgical_retry applied files={len(files)}", flush=True)

        # --- Surgical conflict retry: PARTIAL apply -----------------------
        # Some SEARCH blocks matched, others didn't (a cheap model mis-copies one
        # long class line — e.g. drops a char in `min-h-[95vh]`). The applied edits
        # stay; the failed ones used to vanish silently (files non-empty → the
        # not-files retry above never fired), so the user saw part of the request
        # ignored. Re-ask ONCE for ONLY the conflicted edits, with the exact
        # conflict list + a "anchor on short text/id, not long class strings"
        # nudge, and merge the result on top of the already-patched file.
        if surgical and files and edit_conflicts and accumulated.strip():
            _base_after_first = {**current_files, **files}
            _conf_note = (
                "Часть правок НЕ применилась — SEARCH не совпал с файлом:\n"
                + "\n".join(f"— {c}" for c in edit_conflicts[:6])
                + "\n\nПовтори ТОЛЬКО непрошедшие правки. Якорь SEARCH бери КОРОТКИЙ "
                "и ПРОСТОЙ: видимый ТЕКСТ (слова заголовка/кнопки/цены), атрибут "
                '`id="…"` или одно уникальное слово — НЕ копируй длинные `class="…"` '
                "со [скобочными] значениями (`min-h-[95vh]` и т.п.): cheap-модель их "
                "портит и SEARCH промахивается. Скопируй фрагмент из показанного файла "
                "символ-в-символ. Только <edit>, ровно непрошедшее, остальное не трогай."
            )
            messages.append({"role": "assistant", "content": accumulated})
            messages.append({"role": "user", "content": _conf_note})
            print(
                f"[PP] surgical_conflict_retry conflicts={len(edit_conflicts)}",
                flush=True,
            )
            _esc_model = model_for_role("edit_escalation", override=force_model)
            await _run_stream(
                _esc_model, force_single_shot=True, force_all=_esc_model
            )
            _cr_acc = str(state["accumulated"])
            if _cr_acc.strip():
                try:
                    _cr_files, _ = _extract_files_and_edits(_cr_acc, _base_after_first)
                except (UnsafePathError, ValueError):
                    _cr_files = {}
                if _cr_files:
                    files = {**files, **_cr_files}
                    accumulated = accumulated + "\n" + _cr_acc
                    if state["usage"] and isinstance(state["usage"], dict):
                        usage_data = state["usage"]  # type: ignore[assignment]
                    print(
                        f"[PP] surgical_conflict_retry applied files={list(files.keys())}",
                        flush=True,
                    )
                else:
                    print("[PP] surgical_conflict_retry no_new_files", flush=True)

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
        if surgical and not files and selected_elements and current_files.get(
            "index.html"
        ):
            from omnia_api.services import zone_edit as _ze
            from omnia_api.services.prompt_builder import build_zone_edit_messages

            _old_index_src = current_files["index.html"]
            _span = _ze.find_enclosing_block(
                _old_index_src, _ze.distinctive_anchors(selected_elements)
            )
            if _span is not None:
                _block = _old_index_src[_span[0] : _span[1]]
                _z_notice = (
                    "\n\n*Меняю только выделенную зону, остальную страницу не "
                    "трогаю…*\n\n"
                )
                accumulated = accumulated + _z_notice
                await publish_event(
                    project_id,
                    "llm.chunk",
                    {"message_id": str(assistant_message_id), "delta": _z_notice},
                )
                print(
                    f"[PP] zone_edit start span={_span} block_len={len(_block)}",
                    flush=True,
                )
                _z_parts: list[str] = []
                _z_usage: dict[str, Any] | None = None
                try:
                    async for _ev in stream_chat_completion(
                        build_zone_edit_messages(
                            _block, prompt_text, selected_elements
                        ),
                        model_for_role("freeform_writer", override=force_model),
                        str(user_id),
                        str(project_id),
                        str(assistant_message_id),
                    ):
                        if "delta" in _ev:
                            # Accumulate SILENTLY — the model streams a raw
                            # <section>, which is neither <file> nor <edit>, so
                            # publishing it would dump raw HTML into the chat. We
                            # add a clean "Правка" chip once it lands.
                            _z_parts.append(str(_ev["delta"]))
                        elif "usage" in _ev:
                            _z_usage = _ev["usage"]  # type: ignore[assignment]
                except Exception as _ze_exc:
                    print(f"[PP] zone_edit stream_err {_ze_exc!r}", flush=True)
                _z_acc = "".join(_z_parts)
                _new_block = _ze.extract_block(_z_acc)
                _old_root_id = _ze.root_id(_block)
                # Accept only a real block whose root id matches (proves the model
                # returned the SAME zone rewritten, not a different/empty thing).
                if _new_block and (
                    _old_root_id is None or _ze.root_id(_new_block) == _old_root_id
                ):
                    files = {
                        "index.html": _ze.splice(_old_index_src, _span, _new_block)
                    }
                    if _z_usage:
                        usage_data = _z_usage  # type: ignore[assignment]
                    # Clean chip in chat — never the raw <section>.
                    accumulated = accumulated + (
                        '\n<edit path="index.html">\n'
                        "Обновил выделенную зону, остальную страницу не трогал.\n"
                        "</edit>\n"
                    )
                    print(
                        f"[PP] zone_edit applied new_block_len={len(_new_block)}",
                        flush=True,
                    )
                else:
                    print(
                        "[PP] zone_edit rejected (no/invalid block or id mismatch)",
                        flush=True,
                    )

        # 2) Whole-file rewrite — last resort (no selection, no zone found, or the
        # zone rewrite was rejected). Regenerates the WHOLE file; guarded by the
        # text-preservation ratio so a silent re-design is rejected.
        if surgical and not files and current_files.get("index.html"):
            from omnia_api.services.prompt_builder import build_edit_rewrite_messages

            rw_msgs = build_edit_rewrite_messages(
                current_files, history_serialized, prompt_text, selected_elements
            )
            _rw_notice = (
                "\n\n*Точечно не вышло — переписываю страницу аккуратно, сохраняя "
                "остальное…*\n\n"
            )
            accumulated = accumulated + _rw_notice
            await publish_event(
                project_id,
                "llm.chunk",
                {"message_id": str(assistant_message_id), "delta": _rw_notice},
            )
            print("[PP] surgical_rewrite_fallback start", flush=True)
            _rw_parts: list[str] = []
            _rw_usage: dict[str, Any] | None = None
            try:
                # Use the reliable full-file writer (freeform_writer), not the
                # cheap edit model — the cheap model shadow-dropped the rewrite
                # (empty output) on prod. This pass runs only on the rare edit
                # that <edit> couldn't express, so the slightly richer model is
                # worth a dependable result.
                async for _ev in stream_chat_completion(
                    rw_msgs,
                    model_for_role("freeform_writer", override=force_model),
                    str(user_id),
                    str(project_id),
                    str(assistant_message_id),
                ):
                    if "delta" in _ev:
                        _d = str(_ev["delta"])
                        _rw_parts.append(_d)
                        pub["seq"] = int(pub["seq"]) + 1
                        pub["content"] = str(pub["content"]) + _d
                        await publish_event(
                            project_id,
                            "llm.chunk",
                            {
                                "message_id": str(assistant_message_id),
                                "delta": _d,
                                "seq": int(pub["seq"]),
                            },
                        )
                    elif "usage" in _ev:
                        _rw_usage = _ev["usage"]  # type: ignore[assignment]
            except Exception as _rw_exc:
                print(f"[PP] surgical_rewrite_fallback stream_err {_rw_exc!r}", flush=True)
            _rw_acc = "".join(_rw_parts)
            accumulated = accumulated + _rw_acc
            try:
                _rw_files, _ = _extract_files_and_edits(_rw_acc, current_files)
            except (UnsafePathError, ValueError):
                _rw_files = {}
            _old_index = current_files.get("index.html", "")
            _new_index = _rw_files.get("index.html", "")
            # The writer sometimes streams raw HTML without the <file> wrapper —
            # salvage it instead of dropping the whole edit.
            if not _new_index:
                _salvaged = _salvage_html(_rw_acc)
                if _salvaged:
                    _new_index = _salvaged
                    _rw_files = {"index.html": _salvaged}
                    print(
                        f"[PP] surgical_rewrite_fallback salvaged_html len={len(_salvaged)}",
                        flush=True,
                    )
            _ratio = (
                _text_preserved_ratio(_old_index, _new_index) if _new_index else 0.0
            )
            # ≥0.6 of the original words must survive — a real scoped edit keeps
            # the copy; a re-design replaces it. Reject the drift, keep the page.
            if _new_index and _ratio >= 0.6:
                files = _rw_files
                if _rw_usage:
                    usage_data = _rw_usage  # type: ignore[assignment]
                print(
                    f"[PP] surgical_rewrite_fallback applied ratio={_ratio:.2f}",
                    flush=True,
                )
            else:
                print(
                    f"[PP] surgical_rewrite_fallback rejected ratio={_ratio:.2f} "
                    f"new_len={len(_new_index)}",
                    flush=True,
                )

        # --- Orphaned-anchor repair on surgical edits ---------------------
        # The dead-link pass below is skipped on surgical edits (it must not
        # touch PRE-EXISTING dead links the user didn't mention). But a removal
        # edit ("убери секцию отзывов") orphans nav links — `href="#reviews"`
        # now points at a section this very edit deleted. That dangling anchor
        # IS part of "what was asked", so repair it: delta-scoped (only ids the
        # edit removed), deterministic, no LLM, no regeneration — squarely the
        # kept inline-href-fixer policy.
        if surgical and files and project_template not in CONTAINER_NEXT and not _is_code:
            _before = files
            files = repair_orphaned_anchors_inline(current_files, files)
            if files != _before:
                print("[PP] orphaned_anchors repaired", flush=True)

        # 3) CONTAINER-app rewrite fallback — the <edit> couldn't land on a
        # React/Next/entities app, and the two static fallbacks above are gated
        # on index.html (which container apps don't have), so they never fired.
        # Without this branch a failed <edit> on an entity/fullstack/spa app
        # dead-ends with "ничего не изменилось" — exactly the owner's "сайт
        # сломан → починить через чат не выходит, просто перестаёт что-то
        # делать". Rewrite ONLY the file(s) the edit targeted (full-file, via the
        # reliable writer), guarded by a content-preservation ratio so a scoped
        # fix is told apart from a silent redesign. Kill: USE_CONTAINER_EDIT_REWRITE.
        if (
            surgical
            and not files
            and get_settings().use_container_edit_rewrite
            and project_template in CONTAINER_NEXT
            and current_files
        ):
            from omnia_api.services.prompt_builder import (
                build_container_rewrite_messages,
            )

            # Pick targets: the path(s) the failed <edit> named, then any path the
            # prompt mentions ("Файл: src/…" — the «Починить» card includes it),
            # restricted to files we actually have. Cap to a few so we never
            # blind-rewrite the whole app.
            _targets: dict[str, str] = {}
            try:
                for _p in extract_edits(accumulated):
                    if _p in current_files:
                        _targets[_p] = current_files[_p]
            except (UnsafePathError, ValueError):
                pass
            for _m in re.finditer(
                r"(?:Файл|file)\s*[:=]\s*([^\s,;]+)", prompt_text, re.I
            ):
                _cand = _m.group(1).strip().strip("`\"'.")
                if _cand in current_files and _cand not in _targets:
                    _targets[_cand] = current_files[_cand]
            if not _targets:
                for _cand in ("src/app/page.tsx", "src/App.tsx", "app/page.tsx"):
                    if _cand in current_files:
                        _targets[_cand] = current_files[_cand]
                        break
            _targets = dict(list(_targets.items())[:4])

            if _targets:
                _crc_notice = (
                    "\n\n*Точечно не вышло — переписываю нужные файлы аккуратно, "
                    "сохраняя остальное…*\n\n"
                )
                accumulated = accumulated + _crc_notice
                await publish_event(
                    project_id,
                    "llm.chunk",
                    {"message_id": str(assistant_message_id), "delta": _crc_notice},
                )
                print(
                    f"[PP] container_rewrite_fallback start targets={list(_targets)}",
                    flush=True,
                )
                _crc_parts: list[str] = []
                _crc_usage: dict[str, Any] | None = None
                try:
                    async for _ev in stream_chat_completion(
                        build_container_rewrite_messages(
                            _targets,
                            history_serialized,
                            prompt_text,
                            selected_elements,
                        ),
                        model_for_role("freeform_writer", override=force_model),
                        str(user_id),
                        str(project_id),
                        str(assistant_message_id),
                    ):
                        if "delta" in _ev:
                            _d = str(_ev["delta"])
                            _crc_parts.append(_d)
                            pub["seq"] = int(pub["seq"]) + 1
                            pub["content"] = str(pub["content"]) + _d
                            await publish_event(
                                project_id,
                                "llm.chunk",
                                {
                                    "message_id": str(assistant_message_id),
                                    "delta": _d,
                                    "seq": int(pub["seq"]),
                                },
                            )
                        elif "usage" in _ev:
                            _crc_usage = _ev["usage"]  # type: ignore[assignment]
                except Exception as _crc_exc:
                    print(
                        f"[PP] container_rewrite_fallback stream_err {_crc_exc!r}",
                        flush=True,
                    )
                _crc_acc = "".join(_crc_parts)
                accumulated = accumulated + _crc_acc
                try:
                    _crc_files, _ = _extract_files_and_edits(_crc_acc, current_files)
                except (UnsafePathError, ValueError):
                    _crc_files = {}
                # Accept only files we asked for, and only when real content
                # survived: a scoped fix keeps the file's text; a redesign
                # replaces it. Code-heavy files (few visible words) bypass the
                # ratio — there the word-overlap metric is noise.
                _accepted: dict[str, str] = {}
                for _p, _new in _crc_files.items():
                    if _p not in _targets:
                        continue
                    _old = _targets[_p]
                    _ratio = _text_preserved_ratio(_old, _new)
                    _few_words = len(_visible_words(_old)) < 15
                    if (
                        _new.strip()
                        and len(_new) > 80
                        and (_ratio >= 0.45 or _few_words or len(_old) < 400)
                    ):
                        _accepted[_p] = _new
                        print(
                            f"[PP] container_rewrite accept {_p} ratio={_ratio:.2f}",
                            flush=True,
                        )
                    else:
                        print(
                            f"[PP] container_rewrite reject {_p} ratio={_ratio:.2f} "
                            f"new_len={len(_new)}",
                            flush=True,
                        )
                if _accepted:
                    files = _accepted
                    if _crc_usage:
                        usage_data = _crc_usage  # type: ignore[assignment]

        # --- Pass 2..N: empty-content fallback ----------------------
        # If we got nothing usable back AND the primary model has known
        # fallbacks, transparently re-run against them. The user sees a
        # short inline notice in the chat (delivered as an llm.chunk) and
        # the assistant message ends up labeled with whichever model
        # actually produced the final answer.
        retried_models: list[str] = []
        effective_model = model_id
        if _looks_truncated(accumulated, files):
            # A.5 — retry the *same* model via multipass before switching
            # models. Most "empty response" failures on cheap models are
            # prompt-overwhelm (Haiku/Nano lose focus on a 28+ KB single
            # shot); splitting into 4 narrow passes (skeleton → content
            # → visual → assembly) usually recovers without spending a
            # second model's quota. Skipped when the single shot already
            # ran through multipass (then the failure is the model, not
            # the prompt size — go straight to model-switch fallbacks).
            already_multipass = model_id in multipass_set
            if not already_multipass:
                notice = (
                    f"\n\n*Модель `{effective_model}` дала пустой ответ "
                    f"({len(accumulated)} симв.). Пробую тот же "
                    f"`{effective_model}` в multipass-режиме (4 узких прохода)…*\n\n"
                )
                accumulated = accumulated + notice
                await publish_event(
                    project_id,
                    "llm.chunk",
                    {"message_id": str(assistant_message_id), "delta": notice},
                )
                print(
                    f"[PP] same_model_multipass_retry {effective_model}",
                    flush=True,
                )
                await _run_stream(
                    model_id, force_multipass=True, force_all=force_model,
                    allow_art_director=False,
                )
                mp_acc = str(state["accumulated"])
                mp_usage = state["usage"]
                mp_err = state["error"]
                accumulated = accumulated + mp_acc
                if mp_usage and isinstance(mp_usage, dict):
                    usage_data = mp_usage  # type: ignore[assignment]
                retried_models.append(f"{model_id}#multipass")
                if mp_err:
                    print(
                        f"[PP] same_model_multipass_error "
                        f"{model_id}: {mp_err!r}",
                        flush=True,
                    )
                else:
                    try:
                        files, _ = _extract_files_and_edits(
                            accumulated, current_files
                        )
                    except (UnsafePathError, ValueError):
                        files = {}
                    # If multipass of the same model recovered the output
                    # there's nothing left to fall back to — skip the
                    # model-switch loop entirely.
                    if not _looks_truncated(mp_acc, files):
                        if retried_models:
                            model_id = effective_model

            for fb_model in _EMPTY_RESPONSE_FALLBACKS.get(model_id, []):
                # A.5 — if a prior retry (same-model multipass) already
                # recovered the output, don't burn another model's quota.
                if not _looks_truncated(accumulated, files):
                    break
                notice = (
                    f"\n\n*Модель `{effective_model}` вернула пустой ответ "
                    f"({len(accumulated)} символов). Переключаюсь на "
                    f"`{fb_model}`…*\n\n"
                )
                # Append to accumulated AND broadcast to UI so user sees what's happening.
                accumulated = accumulated + notice
                await publish_event(
                    project_id,
                    "llm.chunk",
                    {"message_id": str(assistant_message_id), "delta": notice},
                )
                print(f"[PP] empty_fallback {effective_model} -> {fb_model}", flush=True)
                await _run_stream(fb_model, force_all=fb_model)
                fb_acc = str(state["accumulated"])
                fb_usage = state["usage"]
                fb_err = state["error"]
                accumulated = accumulated + fb_acc
                if fb_usage and isinstance(fb_usage, dict):
                    # Keep token counts of the successful model — that's the
                    # one actually billed (gateway charges per call).
                    usage_data = fb_usage  # type: ignore[assignment]
                retried_models.append(fb_model)
                effective_model = fb_model
                if fb_err:
                    print(f"[PP] fallback_error {fb_model}: {fb_err!r}", flush=True)
                    continue
                try:
                    files, _ = _extract_files_and_edits(accumulated, current_files)
                except (UnsafePathError, ValueError):
                    files = {}
                if not _looks_truncated(fb_acc, files):
                    break

            if retried_models:
                # Reflect the actually-used model on the message row so the
                # chat header shows e.g. "claude-haiku-4-5" instead of the
                # original Gemini that failed.
                model_id = effective_model

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
        _DEAD_LINK_LLM_THRESHOLD = 3
        # Skip on surgical edits: rewriting a PRE-EXISTING dead link the user
        # didn't mention violates "change only what was asked". The edit prompt
        # already forbids dead links in any element the edit adds.
        if files and not surgical and project_template not in CONTAINER_NEXT and not _is_code:
            initial_dead = find_dead_links(files)
            if initial_dead:
                files = repair_dead_links_inline(files)
                post_inline = find_dead_links(files)
                print(
                    f"[PP] dead_links initial={len(initial_dead)} "
                    f"after_inline={len(post_inline)}",
                    flush=True,
                )
                dead = post_inline
            else:
                dead = []
            # OWNER 2026-06-14: auto full-page regeneration is OFF by default
            # (auto_regenerate_enabled). The inline href fixer above already ran
            # (a targeted edit, kept); the LLM re-roll regenerates whole files, so
            # it only fires when auto-regen is explicitly re-enabled.
            if (
                get_settings().auto_regenerate_enabled
                and len(dead) > _DEAD_LINK_LLM_THRESHOLD
            ):
                print(f"[PP] dead_links remain={len(dead)} -> LLM repair pass", flush=True)
                prior_answer = accumulated
                notice = (
                    "\n\n*Проверка ссылок: часть кнопок вела в никуда — "
                    "перегенерирую с рабочими ссылками…*\n\n"
                )
                accumulated = accumulated + notice
                await publish_event(
                    project_id,
                    "llm.chunk",
                    {"message_id": str(assistant_message_id), "delta": notice},
                )
                repair_request = (
                    "В предыдущем ответе есть ссылки/кнопки, ведущие в никуда:\n"
                    + "\n".join(f"— {d}" for d in dead[:20])
                    + "\n\nВерни ПОЛНЫЕ исправленные файлы целиком (в тех же "
                    "<file>-блоках). Каждая ссылка обязана вести на существующий "
                    "якорь (создай секцию с нужным id), tel:/mailto:/мессенджер или "
                    'реальную страницу. Ни одного href="#", пустого href или '
                    "javascript:void(0). Больше ничего не меняй."
                )
                messages.append({"role": "assistant", "content": prior_answer})
                messages.append({"role": "user", "content": repair_request})
                # B2 — dead-link repair is the `link_repair` role's job (cheap
                # Haiku). force_single_shot so a budget model in the multipass
                # set edits the existing files instead of regenerating the page.
                await _run_stream(
                    model_for_role("link_repair", override=force_model),
                    force_single_shot=True,
                )
                repaired_acc = str(state["accumulated"])
                try:
                    repaired_files, _ = _extract_files_and_edits(
                        repaired_acc, current_files
                    )
                except (UnsafePathError, ValueError):
                    repaired_files = {}
                if repaired_files and len(find_dead_links(repaired_files)) < len(dead):
                    files = repaired_files
                    accumulated = accumulated + repaired_acc
                    print(f"[PP] repair applied files={len(files)}", flush=True)
                else:
                    print("[PP] repair skipped (no improvement)", flush=True)

        # Kit files are Omnia-managed: drop any model attempt to write/delete them,
        # and re-inject the kit <link>/<script> into returned HTML if the model
        # dropped them (so animations/interactivity never silently break).
        # Imported projects: skip entirely — their HTML is foreign and must not
        # have Omnia's kit injected (it would break the layout and leak branding).
        if files and project_template not in CONTAINER_NEXT and not _is_code and not project_is_imported:
            files = {p: c for p, c in files.items() if p not in KIT_FILES}
            files = _ensure_kit_linked(files)

        # Image-resolver: if Haiku/Sonnet wrote <img data-omnia-gen="..."> tags,
        # call gateway → MinIO and rewrite the tags with real src URLs BEFORE
        # the commit so the snapshot / GitHub export / rollback all carry final
        # URLs (single source of truth in git). For fullstack the subsequent
        # hot_reload pushes the rewritten files into the dev container, so HMR
        # picks up the real images automatically. Per-project opt-out via
        # projects.image_gen_enabled (TopBar toggle).
        # Visual enricher — DORMANT by default (USE_VISUAL_ENRICHER, off).
        # Раньше лепил декор в каждый <section> голого HTML безусловно, но
        # циклил dot-grid / diagonal-lines / mesh по ВСЕМ секциям механически —
        # output читался как generative AI-slop («полоски/точки»). Owner-call
        # 2026-05-31: выкл совсем. Re-enable per-env via USE_VISUAL_ENRICHER=true.
        if files and get_settings().use_visual_enricher:
            try:
                files, enr_count, enr_total = enrich_visual_files(files)
                print(
                    f"[PP] visual_enricher enriched={enr_count} sections={enr_total}",
                    flush=True,
                )
            except Exception as enr_exc:
                print(f"[PP] visual_enricher failed: {enr_exc!r}", flush=True)

        # Signature-moment floor — guarantee ONE "expensive" scroll moment per
        # build (owner doctrine «всегда вау»). Surgical: injects a single
        # .omnia-draw line-art divider ONLY when the page carries no
        # .pin-stage/.compare/.omnia-draw/.scroll-clip-reveal. ON by default
        # (USE_SIGNATURE_FLOOR); fail-soft (.html only, never raises).
        if files and not surgical and get_settings().use_signature_floor:
            try:
                files, _sig_n = ensure_signature_floor(files)
                if _sig_n:
                    print(f"[PP] signature_floor injected={_sig_n}", flush=True)
            except Exception as _sig_exc:
                print(f"[PP] signature_floor failed: {_sig_exc!r}", flush=True)

        # Hero background visible — owner: «главный экран генерится с фоткой/
        # нарисованным фоном, в тему». The writer buries the hero's full-bleed
        # photo/graphic under a /70-/90 black wash → flat monotone screen. Lighten
        # that overlay + dim the shader so the on-theme image/graphic actually
        # shows. Build-only, deterministic, fail-soft.
        if files and not surgical and get_settings().use_hero_bg_visible:
            try:
                _idx_html = files.get("index.html")
                if _idx_html:
                    _unmasked, _hb_changed = image_edit.unmask_hero_bg(_idx_html)
                    if _hb_changed:
                        files["index.html"] = _unmasked
                        print("[PP] hero_bg_unmasked", flush=True)
            except Exception as _hb_exc:
                print(f"[PP] hero_bg_visible skipped: {_hb_exc!r}", flush=True)

        # Phase K (2026-05-27) — objective UI audit. ``ui_audit`` runs the
        # 10-point Malewicz Ch27 + Phase G rubric (typography/color/button/
        # accessibility/no-lorem/etc.) on the final HTML pool. We log the
        # score so we can baseline per-model design quality from prod logs
        # and emit it over WS so the workspace UI can surface a quality
        # indicator. NOT a re-generate trigger yet — measurement first,
        # feedback loop comes in Sprint 2 once we have a baseline score
        # distribution. Best-effort: any audit failure logs and continues.
        if files:
            try:
                html_pool = {
                    p: c for p, c in files.items()
                    if p.endswith(".html") or p.endswith(".htm")
                }
                if html_pool:
                    report = ui_audit(html_pool)
                    failed_ids = [f.check_id for f in report.failures]
                    print(
                        f"[PP] ui_audit score={report.score}/{report.max} "
                        f"failed={failed_ids}",
                        flush=True,
                    )
                    if pipeline_debug.enabled():
                        pipeline_debug.dump(
                            project_id,
                            assistant_message_id,
                            "06_ui_audit.md",
                            f"score={report.score}/{report.max}\n\n"
                            + "\n".join(
                                f"- [{f.severity}] {f.check_id}: "
                                f"{f.description} | {f.evidence}"
                                for f in report.failures
                            ),
                        )
                    await publish_event(
                        project_id,
                        "llm.audit",
                        {
                            "message_id": str(assistant_message_id),
                            "score": report.score,
                            "max": report.max,
                            "failures": [
                                {
                                    "id": f.check_id,
                                    "severity": f.severity,
                                    "description": f.description,
                                    "evidence": f.evidence,
                                }
                                for f in report.failures
                            ],
                        },
                    )
            except Exception as audit_exc:
                print(f"[PP] ui_audit failed: {audit_exc!r}", flush=True)

        # Phase L6 — audit-driven retry loop (1 retry max). Triggers only
        # when catalog/IR mode is active AND the first audit scored
        # below the threshold AND we haven't already retried. We append
        # the prior accumulated response + a concrete failure list as a
        # user turn and re-stream against the same model. The retry
        # response goes through the same IR parse → render pipeline; if
        # it parses, it replaces `files` and downstream (image_resolver
        # / repo commit) sees the corrected page.
        _RETRY_SCORE_THRESHOLD = 7  # max=10
        _retry_done = False
        try:
            _last_report = None  # type: ignore[var-annotated]
            try:
                _last_report = report
            except NameError:
                _last_report = None
            # B3 — deterministic rubric verdict, refined by the optional LLM
            # judge (role `audit`, Sonnet) ONLY in the borderline 6–7/10 band
            # where the rubric is least decisive. The judge can both *promote*
            # a 7 to a re-roll and *spare* a 6 that's actually fine, so it owns
            # the close calls; clear-cut scores never pay for it.
            _score = int(getattr(_last_report, "score", 10)) if _last_report else 10
            _wants_retry = _score < _RETRY_SCORE_THRESHOLD
            _retry_eligible = (
                # Catalog-only retry (re-rolls the IR). Freeform quality is
                # handled by the Phase 11 acceptance gate, not here.
                _gen_mode == "catalog"
                and _last_report is not None
                and not _retry_done
            )
            if _retry_eligible and _AUDIT_JUDGE_LOW <= _score <= _AUDIT_JUDGE_HIGH:
                _judge_html = "\n".join(
                    c for p, c in files.items()
                    if p.endswith(".html") or p.endswith(".htm")
                )
                _verdict = await _audit_judge_wants_retry(
                    html=_judge_html,
                    report=_last_report,
                    model=model_for_role("audit", override=force_model),
                    user_id=user_id,
                    project_id=project_id,
                    message_id=assistant_message_id,
                )
                if _verdict is not None:
                    print(
                        f"[PP] audit_judge verdict={'RETRY' if _verdict else 'PASS'} "
                        f"score={_score}",
                        flush=True,
                    )
                    _wants_retry = _verdict
            if _retry_eligible and _wants_retry:
                retry_msg = format_failures_for_retry(_last_report)
                if retry_msg:
                    print(
                        f"[PP] retry_triggered score={_last_report.score}/{_last_report.max} "
                        f"failures={len(_last_report.failures)}",
                        flush=True,
                    )
                    await publish_event(
                        project_id,
                        "llm.retry",
                        {
                            "message_id": str(assistant_message_id),
                            "reason": "audit_score_low",
                            "score": _last_report.score,
                            "max": _last_report.max,
                        },
                    )
                    # Append prior assistant response + retry feedback to
                    # the same message list so prompt caching (system) hits.
                    messages.append({"role": "assistant", "content": accumulated})
                    messages.append({"role": "user", "content": retry_msg})
                    # B3 — the re-roll is the `audit_retry` role's job (Opus,
                    # director-grade). force_all pins every pass onto it so the
                    # second attempt is uniformly strong, not the original mix.
                    _retry_model = model_for_role("audit_retry", override=force_model)
                    await _run_stream(_retry_model, force_all=_retry_model)
                    retry_accumulated = str(state["accumulated"])
                    retry_usage = state["usage"]
                    retry_error = state["error"]
                    if not retry_error and retry_accumulated:
                        # Replicate the L3 IR→HTML conversion on the retry
                        # output. Fail-soft: if it doesn't parse, keep the
                        # first-pass files unchanged.
                        import json as _json_r

                        from pydantic import ValidationError as _VE_r

                        from omnia_api.sections import PageIR as _PageIR_r
                        from omnia_api.sections.renderer import (
                            render_to_files as _render_to_files_r,
                        )
                        raw_r = retry_accumulated.strip()
                        if raw_r.startswith("```"):
                            raw_r = raw_r.split("\n", 1)[1] if "\n" in raw_r else raw_r[3:]
                            if raw_r.endswith("```"):
                                raw_r = raw_r[:-3]
                            raw_r = raw_r.strip()
                        try:
                            ir_r = _PageIR_r.model_validate(_json_r.loads(raw_r))
                            # Phase L8 — Smart Defaults on retry IR too.
                            from omnia_api.sections import apply_smart_defaults as _smart_r
                            ir_r = _smart_r(ir_r, preset_id=project_design_preset_id)
                            kit_css_r = current_files.get("src/assets/omnia-kit.css", "")
                            kit_js_r = current_files.get("src/assets/omnia-kit.js", "")
                            rendered_r = _render_to_files_r(
                                ir_r, kit_css=kit_css_r, kit_js=kit_js_r
                            )
                            # Replace files for downstream stages.
                            files = dict(rendered_r)
                            accumulated = "\n".join(
                                f'<file path="{p}">\n{c}\n</file>'
                                for p, c in rendered_r.items()
                            )
                            # Merge retry usage onto first-pass usage.
                            if usage_data is None:
                                usage_data = retry_usage
                            elif retry_usage:
                                usage_data = {
                                    "tokens_in": (
                                        int((usage_data or {}).get("tokens_in", 0))
                                        + int((retry_usage or {}).get("tokens_in", 0))
                                    ),
                                    "tokens_out": (
                                        int((usage_data or {}).get("tokens_out", 0))
                                        + int((retry_usage or {}).get("tokens_out", 0))
                                    ),
                                    "cost_rub": (
                                        float((usage_data or {}).get("cost_rub", 0.0))
                                        + float((retry_usage or {}).get("cost_rub", 0.0))
                                    ),
                                }
                            _retry_done = True
                            # Re-audit so logs show the post-retry score.
                            try:
                                html_pool_r = {
                                    p: c for p, c in files.items()
                                    if p.endswith(".html") or p.endswith(".htm")
                                }
                                if html_pool_r:
                                    report_r = ui_audit(html_pool_r)
                                    print(
                                        f"[PP] ui_audit_retry score={report_r.score}/{report_r.max}",
                                        flush=True,
                                    )
                                    await publish_event(
                                        project_id,
                                        "llm.audit",
                                        {
                                            "message_id": str(assistant_message_id),
                                            "score": report_r.score,
                                            "max": report_r.max,
                                            "stage": "retry",
                                        },
                                    )
                            except Exception:
                                pass
                        except (_json_r.JSONDecodeError, _VE_r, ValueError) as retry_ir_exc:
                            _log.warning(
                                "retry IR parse failed (keeping first-pass files): %r",
                                retry_ir_exc,
                            )
                            print(
                                f"[PP] retry_ir_fail err={retry_ir_exc!r}",
                                flush=True,
                            )
        except Exception as retry_exc:
            print(f"[PP] retry_branch_failed err={retry_exc!r}", flush=True)

        if files and project_image_gen_enabled:
            await _emit_stage("images", "start")
            try:
                # Hard cap: a broken image upstream (flux 501 / pexels timeout)
                # must NEVER hang the build — on timeout we ship the page as-is so
                # it still reaches commit (no lost work). 75s over the resolver's
                # own per-image deadline.
                # Live drop-in: emit a per-image event as each picture resolves so
                # the streaming preview swaps it into its frame in real time. Gated
                # by use_live_image_events; off → images still land on the snapshot.
                async def _emit_img(idx: int, url: str) -> None:
                    await publish_event(
                        project_id,
                        "image.resolved",
                        {
                            "message_id": str(assistant_message_id),
                            "idx": idx,
                            "url": url,
                        },
                    )

                _on_img = (
                    _emit_img if get_settings().use_live_image_events else None
                )
                files, resolved, total = await asyncio.wait_for(
                    resolve_images(files, str(project_id), on_image=_on_img),
                    timeout=75,
                )
                print(
                    f"[PP] image_resolver resolved={resolved} total={total}",
                    flush=True,
                )
                if total > 0 and resolved < total:
                    await publish_event(
                        project_id,
                        "llm.chunk",
                        {
                            "message_id": str(assistant_message_id),
                            "delta": (
                                f"\n\n*Сгенерировано картинок: {resolved} из {total}. "
                                f"Часть промптов не удалась — попробуй переключить toggle "
                                f"«🎨 Картинки» в шапке или перегенерировать промпт.*\n\n"
                            ),
                        },
                    )
            except Exception as img_exc:
                print(f"[PP] image_resolver failed: {img_exc!r}", flush=True)
            await _emit_stage("images", "end")

        # Belt-and-suspenders: if image-gen failed (budget exhausted / timeout),
        # the resolver leaves the data-omnia-gen tags in place → a BROKEN <img>
        # box ships (the dark "alt text" rectangle the owner saw). Strip any
        # leftover unresolved tags so the section's drawn/graphic background shows
        # instead. No-op when everything resolved. Runs for builds AND edits.
        if files:
            try:
                from omnia_api.services.image_resolver import strip_unresolved_tags

                files, _stripped = strip_unresolved_tags(files)
                if _stripped:
                    print(f"[PP] stripped_unresolved_img_tags={_stripped}", flush=True)
            except Exception as _strip_exc:
                print(f"[PP] strip_unresolved skipped: {_strip_exc!r}", flush=True)

        # ── Entity theme-token guard — deterministic, BEFORE the audit ────────
        # Cheap writer models leak raw neutral utilities (text-gray-800,
        # bg-gray-100, bg-white) instead of theme tokens, so the app freezes grey
        # and ignores the art-director's --primary/--foreground. Rewrite them to
        # tokens here (the .tsx analogue of palette_guard) so the shipped app
        # actually re-themes and the audit's hardcoded-colour class clears.
        # Semantic status colours (green/yellow/red) are left untouched.
        if files and not surgical and project_template in ("nextjs_entities", "fullstack"):
            try:
                from omnia_api.services.entity_theme import tokenize_neutrals

                files, _tok_n = tokenize_neutrals(files)
                if _tok_n:
                    print(f"[PP] entity_theme tokenized={_tok_n} neutral utils", flush=True)
            except Exception as _tok_exc:
                print(f"[PP] entity_theme skipped: {_tok_exc!r}", flush=True)

        # ── Missing-component shim guard — deterministic, BEFORE commit ────────
        # Writer models import standard shadcn components the template doesn't
        # ship (radio-group, switch, …) → Next.js "Module not found" → the WHOLE
        # app renders a build-error page (owner hit this). Inject a dependency-
        # free self-contained shim for any imported-but-missing @/components/ui/*
        # so the app always builds.
        if files and not surgical and project_template in ("nextjs_entities", "fullstack"):
            try:
                from omnia_api.services.ui_shims import ensure_ui_shims

                files, _shim_added, _shim_missing = ensure_ui_shims(files)
                if _shim_added:
                    print(f"[PP] ui_shims injected={_shim_added}", flush=True)
                if _shim_missing:
                    print(
                        f"[PP] ui_shims MISSING no-shim (build may fail)={_shim_missing}",
                        flush=True,
                    )
            except Exception as _shim_exc:
                print(f"[PP] ui_shims skipped: {_shim_exc!r}", flush=True)

        # ── Branded share-card (P2, pillar 4) — deterministic, BEFORE commit ──
        # The entity template's <head> is a static «Omnia project», so every
        # shared /p/<slug> link unfurls brand-less. Derive a {title, tagline,
        # accent} card from the project name + prompt + palette and inject it as
        # src/app/omnia-share.ts, which the template's generateMetadata +
        # opengraph-image route consume → a branded unfurl per niche, 0 model
        # cost. Fail-soft: any error leaves the template's neutral default card.
        if files and not surgical and project_template in ("nextjs_entities", "fullstack"):
            try:
                from omnia_api.services.design_tokens import tokens_for_project
                from omnia_api.services.share_meta import (
                    build_share_card,
                    inject_share_module,
                )

                _accent = tokens_for_project(
                    str(project_id), industry_hint=project_design_preset_id
                ).palette.primary
                async with factory() as _share_session:
                    _proj = await _share_session.get(Project, project_id)
                    _proj_name = _proj.name if _proj else None
                _card = build_share_card(_proj_name, prompt_text, _accent)
                files = inject_share_module(files, _card)
                print(f"[PP] share_meta card title={_card.title!r}", flush=True)
            except Exception as _share_exc:
                print(f"[PP] share_meta skipped: {_share_exc!r}", flush=True)

        # ── Baked brief → public surface (v2.21 #1A, pillar 3+4) — BEFORE commit ──
        # The freeform static /p/<slug> narrates its own birth (baked into
        # index.html further below). The ENTITY hot-path (≈80% of apps) did NOT:
        # its /p/<slug> 302-redirects to the LIVE app on another origin, whose
        # public/omnia-brief-narration.js only ever received the brief via the
        # workspace iframe's postMessage — so a stranger opening the shared (or
        # forked) app saw a finished UI, SILENT. Bake the art-director brief onto
        # window.__omniaBrief (src/app/omnia-brief.ts → layout.tsx, the .tsx
        # analogue of share_meta's omnia-share.ts) so the SAME reveal plays for a
        # stranger. Fail-soft: no/empty brief leaves the template's `null` default
        # and the reveal stays inert. Side-effect-free; idempotent on the file.
        _bm_brief = state.get("brief")
        if (
            files
            and not surgical
            and project_template in ("nextjs_entities", "fullstack")
            and isinstance(_bm_brief, dict)
        ):
            try:
                from omnia_api.services.brief_narration import inject_brief_module

                files = inject_brief_module(files, _bm_brief)
                print("[PP] brief_module baked into omnia-brief.ts", flush=True)
            except Exception as _bm_exc:
                print(f"[PP] brief_module skipped err={_bm_exc!r}", flush=True)

        # ── Structure audit (entity/app builds) — non-blocking smoke detector ─
        # Entity/fullstack apps skip the acceptance gate (container-backed), so we
        # at least LOG drift from the app-UI doctrine (hardcoded colours, fixed-px
        # widths, raw <table>/<aside>, missing app-shell). Never blocks the build.
        if files and not surgical and project_template in ("nextjs_entities", "fullstack"):
            try:
                from omnia_api.services.structure_audit import audit_entity_app

                _struct_warnings = audit_entity_app(files)
                if _struct_warnings:
                    print(
                        f"[PP] structure_audit ({len(_struct_warnings)}): "
                        + " | ".join(_struct_warnings[:12]),
                        flush=True,
                    )
                    pipeline_debug.dump(
                        project_id,
                        assistant_message_id,
                        "04_structure_audit.md",
                        "\n".join(f"- {w}" for w in _struct_warnings),
                    )
            except Exception as _audit_exc:
                print(f"[PP] structure_audit skipped: {_audit_exc!r}", flush=True)

        # ── Phase 11 — acceptance gate (freeform safety net) ──────────────
        # Render → check structure + responsiveness (+ optional vision). If it
        # fails, re-roll with concrete feedback up to ACCEPTANCE_MAX_RETRIES.
        # If freeform still fails, fall back to the catalog/IR path (guaranteed
        # valid page). Runs after image-resolve so screenshots carry real
        # images. All best-effort — any gate error ships the current files.
        _acc_settings = _get_settings()
        _acc_fingerprint: int | None = None
        if (
            files
            and not surgical
            and project_template
            not in ("fullstack", "nextjs_entities", "spa", "tgbot", "api", "code")
            and _acc_settings.use_acceptance_gate
            and _gen_mode in ("freeform", "catalog")
        ):
            await _emit_stage("judge", "start")
            from omnia_api.services import acceptance as _acceptance
            # Design judge (premium / on-button): force the Awwwards vision-critic
            # and allow EXACTLY ONE repair re-roll even in score-only mode (owner:
            # the judge must NOT loop many times — 1 iteration is the whole point).
            # Otherwise keep score-only (0 repairs) / max-retries as before.
            _design_judge = _acc_settings.use_design_judge
            # OWNER 2026-06-14: with auto_regenerate_enabled OFF the gate still
            # EVALUATES (advisory verdict published) but never re-rolls — 0 repairs.
            _max_acc = (
                0
                if not _acc_settings.auto_regenerate_enabled
                else 1
                if _design_judge
                else 0
                if _acc_settings.acceptance_score_only
                else max(0, int(_acc_settings.acceptance_max_retries))
            )
            # Taste barrier (область T, DARK): a generic→repair pass is decoupled
            # from auto_regenerate_enabled (owner: never auto-regenerate the whole
            # page) — it allows at most `acceptance_taste_repair_passes` extra
            # re-rolls, and ONLY when `acceptance_taste_repair_on_generic` is on.
            # Default (flag OFF / passes 0) leaves `_max_acc` untouched.
            if _acc_settings.acceptance_taste_repair_on_generic:
                _max_acc = max(
                    _max_acc, int(_acc_settings.acceptance_taste_repair_passes)
                )
            # Area D (anti-sameness, DARK): soften the catalog fallback. A gauntlet/
            # structural fail (verdict "broken" / struct-not-ok — both already
            # `_repair_worthy`) earns up to `acceptance_gate_repair_passes` freeform
            # re-rolls WITH the gate's failed-class feedback BEFORE dropping to the
            # single catalog template — so a diverse page fixes the specific issue
            # instead of being wholesale-replaced. Decoupled from
            # `auto_regenerate_enabled` (a targeted feedback re-roll, not a blind
            # full-page regen). Default 0 → straight to catalog (today's behaviour).
            if int(_acc_settings.acceptance_gate_repair_passes) > 0:
                _max_acc = max(
                    _max_acc, int(_acc_settings.acceptance_gate_repair_passes)
                )
            # Repair floor: only spend a repair re-roll on a genuinely deficient
            # page (see acceptance_repair_floor docstring) — not on every
            # borderline vision score.
            _repair_floor = max(0, int(_acc_settings.acceptance_repair_floor))
            _verdict = None
            # Best-so-far guard: the design-judge repair can REGRESS (re-add dead
            # links → struct fails, or strip wow-features). Track the best-ranked
            # attempt and ship THAT — never a repair worse than what we had.
            _best_files = files
            _best_rank: tuple[int, int, int] | None = None
            try:
                for _acc_attempt in range(_max_acc + 1):
                    # Hard cap: the design-judge (full-page screenshot + vision
                    # model) must NEVER hang the build. On timeout → TimeoutError
                    # bubbles to the gate's except → we ship the current page and
                    # still reach commit (no lost work).
                    _verdict = await asyncio.wait_for(
                        _acceptance.evaluate(
                            files,
                            project_id=str(project_id),
                            prompt_context=prompt_text,
                            user_id=str(user_id),
                            # Design judge forces the vision-critic ON (else follow the
                            # use_vision_audit setting). It now judges a FULL-PAGE,
                            # images-painted screenshot — no more "empty hero" misreads.
                            run_vision=(True if _design_judge else None),
                            # Originality: respect the setting (owner left it OFF — it
                            # fingerprinted unreliable shots and false-flagged minimal
                            # heroes as near-duplicates). Freeform-only when enabled.
                            run_originality=(
                                _acc_settings.use_originality and _gen_mode == "freeform"
                            ),
                            # V2.5.1 — feed the persisted onboarding answers to the
                            # chip-pixel fidelity leg so a request↔render mismatch
                            # (e.g. asked dark, rendered light) is a real finding
                            # instead of the empty-spec no-op it was before.
                            discovery_spec=project_discovery_spec,
                        ),
                        timeout=90,
                    )
                    _rank = (
                        1 if _verdict.structural_ok else 0,
                        1 if _verdict.responsive_ok else 0,
                        int(_verdict.score or 0),
                    )
                    if _best_rank is None or _rank > _best_rank:
                        _best_rank, _best_files = _rank, files
                    print(
                        f"[PP] acceptance attempt={_acc_attempt} passed={_verdict.passed} "
                        f"verdict={_verdict.verdict} score={_verdict.score} "
                        f"struct={_verdict.structural_ok} resp={_verdict.responsive_ok} "
                        f"vision={_verdict.vision_ran}",
                        flush=True,
                    )
                    pipeline_debug.dump(
                        project_id,
                        assistant_message_id,
                        f"04_vision_attempt{_acc_attempt}.md",
                        f"verdict={_verdict.verdict} score={_verdict.score} "
                        f"passed={_verdict.passed} struct={_verdict.structural_ok} "
                        f"resp={_verdict.responsive_ok} vision_ran={_verdict.vision_ran}\n\n"
                        f"ISSUES ({len(_verdict.issues)}):\n"
                        + "\n".join(f"- {_i}" for _i in _verdict.issues)
                        + "\n\nFEEDBACK:\n"
                        + (_verdict.feedback or "(none)"),
                    )
                    await publish_event(
                        project_id,
                        "llm.audit",
                        {
                            "message_id": str(assistant_message_id),
                            "stage": "acceptance",
                            "passed": _verdict.passed,
                            "verdict": _verdict.verdict,
                            "score": _verdict.score,
                        },
                    )
                    # Spend the repair re-roll ONLY on a genuinely deficient page:
                    # a hard structural/responsive defect, a "broken" vision
                    # verdict, or a vision score below the floor. A merely-not-
                    # perfect page (struct+resp OK, score in [floor, min_score))
                    # ships as attempt-0 — this is the reflexive-repair cut.
                    # Taste barrier (область T, DARK): when
                    # `acceptance_taste_repair_on_generic` is on, a GENERIC verdict
                    # (not only "broken") that vision really produced is also
                    # repair-worthy — "not ugly but generic" earns one re-roll with
                    # the vision issues as feedback. Default OFF → unchanged.
                    _taste_repair = (
                        _acc_settings.acceptance_taste_repair_on_generic
                        and _verdict.vision_ran
                        and _verdict.verdict == "generic"
                    )
                    _repair_worthy = (
                        not _verdict.structural_ok
                        or not _verdict.responsive_ok
                        or _verdict.verdict == "broken"
                        or (_verdict.vision_ran and int(_verdict.score) < _repair_floor)
                        or _taste_repair
                    )
                    if (
                        _verdict.passed
                        or not _verdict.feedback
                        or _acc_attempt >= _max_acc
                        or not _repair_worthy
                    ):
                        break
                    notice = (
                        f"\n\n*Приёмка {_acc_attempt + 1}/{_max_acc}: правлю вёрстку "
                        f"({_verdict.verdict})…*\n\n"
                    )
                    accumulated = accumulated + notice
                    await publish_event(
                        project_id,
                        "llm.chunk",
                        {"message_id": str(assistant_message_id), "delta": notice},
                    )
                    messages.append({"role": "assistant", "content": accumulated})
                    messages.append({"role": "user", "content": _verdict.feedback})
                    # Hard cap: a stuck repair re-roll must not hang the build —
                    # on timeout we ship the pre-repair page and reach commit.
                    await asyncio.wait_for(
                        _run_stream(
                            effective_model, force_all=force_model,
                            allow_art_director=False,
                        ),
                        timeout=120,
                    )
                    if state["error"] or not str(state["accumulated"]).strip():
                        print(f"[PP] acceptance_repair_empty err={state['error']!r}", flush=True)
                        break
                    _repair_acc = str(state["accumulated"])
                    try:
                        _repaired, _ = _extract_files_and_edits(_repair_acc, current_files)
                    except (UnsafePathError, ValueError):
                        _repaired = {}
                    if not _repaired:
                        print("[PP] acceptance_repair_no_files", flush=True)
                        break
                    _repaired = {p: c for p, c in _repaired.items() if p not in KIT_FILES}
                    _repaired = _ensure_kit_linked(_repaired)
                    if _acc_settings.use_visual_enricher:
                        try:
                            _repaired, _, _ = enrich_visual_files(_repaired)
                        except Exception:
                            pass
                    if _acc_settings.use_signature_floor:
                        try:
                            _repaired, _ = ensure_signature_floor(_repaired)
                        except Exception:
                            pass
                    if project_image_gen_enabled:
                        try:
                            _repaired, _, _ = await asyncio.wait_for(
                                resolve_images(_repaired, str(project_id)), timeout=75
                            )
                        except Exception:
                            pass
                    files = _repaired
                    accumulated = accumulated + _repair_acc

                # Best-so-far: if every repair ranked below an earlier attempt,
                # ship the best one — never regress (e.g. attempt0 struct-OK but
                # vision-flagged → attempt1 re-added dead links → struct broken).
                if _best_files is not files:
                    print(f"[PP] acceptance_best_so_far revert rank={_best_rank}", flush=True)
                    files = _best_files

                # Remember an accepted freeform page's fingerprint (Sprint 4)
                # so later generations can be nudged off near-duplicates.
                if _verdict is not None and _verdict.passed:
                    _acc_fingerprint = _verdict.fingerprint

                # Freeform exhausted its retries and still fails → regenerate
                # once via the catalog/IR path (guaranteed valid page).
                # Taste-barrier guard (область T, review HIGH-2): a vision-ONLY
                # block (struct/resp/gauntlet all OK, only the taste verdict flipped
                # passed) must NOT drop the rich freeform to the uglier catalog
                # fallback unless a taste re-roll is configured — otherwise enabling
                # acceptance_vision_block_enabled alone would catalog-bomb a large
                # share of structurally-fine freeform builds. With taste-repair on,
                # the re-roll already ran (Loop A); catalog stays the last resort.
                _vision_only_block = (
                    _verdict is not None
                    and getattr(_verdict, "vision_blocked", False)
                    and not _acc_settings.acceptance_taste_repair_on_generic
                )
                if (
                    _verdict is not None
                    and not _verdict.passed
                    and not _vision_only_block
                    and _gen_mode == "freeform"
                    and _acc_settings.use_section_catalog
                    and not _acc_settings.acceptance_score_only
                ):
                    print("[PP] acceptance->catalog fallback", flush=True)
                    _fb_files = await _catalog_fallback_generate(
                        history=history_serialized,
                        prompt_text=prompt_text,
                        selected_elements=selected_elements,
                        preset_id=project_design_preset_id,
                        project_id=project_id,
                        user_id=user_id,
                        assistant_message_id=assistant_message_id,
                        current_files=current_files,
                        discovery_spec=project_discovery_spec,
                    )
                    if _fb_files:
                        _fb_files = {p: c for p, c in _fb_files.items() if p not in KIT_FILES}
                        _fb_files = _ensure_kit_linked(_fb_files)
                        files = _fb_files
                        notice = (
                            "\n\n*Свободная вёрстка не прошла приёмку — собрал "
                            "через надёжный каталог.*\n\n"
                        )
                        accumulated = accumulated + notice
                        await publish_event(
                            project_id,
                            "llm.chunk",
                            {"message_id": str(assistant_message_id), "delta": notice},
                        )
            except Exception as _acc_exc:
                print(f"[PP] acceptance_gate_failed err={_acc_exc!r}", flush=True)
            await _emit_stage("judge", "end")

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
        if files and not surgical:
            if pipeline_debug.enabled():
                pipeline_debug.dump(
                    project_id, assistant_message_id,
                    "07_pre_palette_guard.html", files.get("index.html", ""),
                )
            try:
                from omnia_api.services.app_theme import apply_app_palette
                from omnia_api.services.design_tokens import tokens_for_project
                from omnia_api.services.palette_guard import enforce_palette

                _palette = tokens_for_project(
                    str(project_id), industry_hint=project_design_preset_id
                ).palette
                if pipeline_debug.enabled():
                    pipeline_debug.dump(
                        project_id, assistant_message_id,
                        "07b_forced_palette.md", repr(_palette),
                    )
                # Entity/.tsx apps theme via a brand :root override in
                # (app)/layout.tsx, NOT via index.html — enforce_palette below is
                # HTML-only and skips them. Snap the brand --primary to a colour
                # that's actually visible on the kit's light canvas (the writer
                # routinely emits a dark-palette near-white primary that vanishes).
                files = apply_app_palette(files, _palette)
                files = enforce_palette(files, _palette)
            except Exception as _pg_exc:
                print(f"[PP] palette_guard skipped err={_pg_exc!r}", flush=True)
            files = enforce_contrast(files)
            if pipeline_debug.enabled():
                pipeline_debug.dump(
                    project_id, assistant_message_id,
                    "08_post_palette_guard.html", files.get("index.html", ""),
                )

        # ── Brief-narration bake (v2.21 #1A, pillar 3+4) — BEFORE commit ──
        # The most-shared public surface (freeform static /p/<slug>) was born
        # SILENT: a colleague pasting the link saw a finished page, none of the
        # "AI is designing this" reveal that hooks the viral loop. Bake the
        # art-director brief + a self-contained reveal into index.html so the
        # shared link plays the SAME birth narration for a stranger (the brief
        # reached only the workspace/iframe before). Freeform only — container
        # apps (nextjs_entities/fullstack) 302-redirect to a live app on another
        # origin where the template's own omnia-brief-narration.js handles it.
        # Fail-soft + idempotent (services/brief_narration); 0-line brief = no-op.
        _bn_brief = state.get("brief")
        if (
            files
            and not surgical
            and _gen_mode == "freeform"
            and isinstance(_bn_brief, dict)
            and files.get("index.html")
        ):
            try:
                from omnia_api.services.brief_narration import inject_brief_narration

                files["index.html"] = inject_brief_narration(
                    files["index.html"], _bn_brief
                )
                print("[PP] brief_narration baked into index.html", flush=True)
            except Exception as _bn_exc:
                print(f"[PP] brief_narration skipped err={_bn_exc!r}", flush=True)

        new_snapshot_id: UUID | None = None
        # Entity-schema corruption (an unparseable entities/*.json) is detected in the
        # CONTAINER_NEXT guards below and surfaced as a «schema» card after commit, so a
        # DOA section is loud + fixable instead of shipping silently under a «готово».
        _bad_entities: list[str] = []
        if files:
            # A6a — restore the managed auth columns if the model dropped them
            # while rewriting the Drizzle schema, so signup/signin keep working.
            # Runs before commit + hot_reload so git and the container agree.
            if project_template in CONTAINER_NEXT:
                files = _preserve_auth_schema(files)
                # Filename↔Name guard: the registry resolves entities by
                # `entities/<Name>.json`, so a lowercase/plural filename the
                # writer copied from the brief (clients.json for name "Client")
                # would 404 every read/write and DOA the app. Realign here.
                files = _normalize_entity_filenames(files)
                # PROPOSAL P-ENTITYJSON (BS-31): a writer-fumbled, unparseable
                # entities/*.json resolves as 404 'unknown entity' at runtime — the
                # whole section is DOA while the build still reads as «готово». Detect
                # it here; the action half (a fixable «schema» card) fires after commit.
                # Does not mutate `files` — repair stays a user-driven «Починить».
                _bad_entities = _warn_unparseable_entity_json(files)
            # Carry the user's direct style edits (omnia-overrides block + font
            # links) across this regeneration so manual color/font tweaks aren't
            # lost when the model rewrites index.html. Fail-soft, like the guards.
            try:
                from omnia_api.services import overrides as _overrides

                _old_index = current_files.get("index.html")
                if _old_index and files.get("index.html"):
                    files["index.html"] = _overrides.carry_over_overrides(
                        _old_index, files["index.html"]
                    )
            except Exception as _co_exc:
                print(f"[PP] overrides carry-over skipped err={_co_exc!r}", flush=True)
            new_sha = await asyncio.to_thread(
                repo_svc.commit_files,
                project_id,
                files,
                f"AI: {prompt_text[:50]}",
                current_sha,
            )
            async with factory() as session:
                # Orchestrated runs record the "topmix-v1" label; a fired
                # fallback records the model that actually produced the output;
                # an admin-forced run records the forced model.
                snapshot_model_id = (
                    model_id
                    if model_id != routing_model
                    else (
                        force_model
                        or (ORCHESTRATION_LABEL if orchestrate else routing_model)
                    )
                )
                snapshot = Snapshot(
                    project_id=project_id,
                    commit_sha=new_sha,
                    prompt_text=prompt_text,
                    model_id=snapshot_model_id,
                    parent_id=current_snapshot_id,
                )
                session.add(snapshot)
                await session.flush()
                new_snapshot_id = snapshot.id

                project = await session.get(Project, project_id)
                if project is not None:
                    project.current_snapshot_id = snapshot.id

                msg = await session.get(Message, assistant_message_id)
                if msg is not None:
                    # Honest chat content: drop leaked raw code and any chip for a
                    # file that wasn't actually committed, so the chat reflects
                    # what shipped (not the model's raw attempt). Kill switch
                    # USE_CLEAN_CHAT_CONTENT falls back to the raw output.
                    msg.content = (
                        clean_chat_content(accumulated, files, surgical=surgical)
                        if get_settings().use_clean_chat_content
                        else accumulated
                    )
                    msg.snapshot_id = snapshot.id
                    if usage_data:
                        msg.tokens_in = int(usage_data.get("tokens_in") or 0)
                        msg.tokens_out = int(usage_data.get("tokens_out") or 0)

                # Burn one free generation — only on a successful free run (we're
                # inside `if files:`, so a snapshot was committed). Counted here,
                # not in the gateway, because the API owns the user row.
                if is_free:
                    user_row = await session.get(User, user_id)
                    if user_row is not None:
                        user_row.free_generations_used = (
                            user_row.free_generations_used or 0
                        ) + 1

                # Billing is the LLM Gateway's responsibility — it already
                # wrote `wallet_charges` + `usage` + decremented `wallets`
                # the moment the upstream stream finished (see
                # apps/llm-gateway/src/omnia_gateway/services/streaming.py:
                # billing.charge call). Re-charging here was double-billing
                # users (each prompt cost 2× the displayed price). The frontend
                # still picks up the new balance: usePromptStream invalidates
                # ["wallet"] on every `llm.done` event, which triggers a fresh
                # GET /api/wallet that reflects the gateway-side debit.
                await session.commit()

                await session.refresh(snapshot)

            await asyncio.to_thread(enqueue_preview, new_snapshot_id)
            await publish_event(
                project_id,
                "snapshot.created",
                {"snapshot": _snapshot_payload(snapshot)},
            )

            # BS-31 action half — a declared entity whose JSON didn't parse is DOA at
            # runtime (404 'unknown entity'), so the whole section is dead while the
            # chat still reads as «готово». Surface each as a fixable «schema» card so
            # the user sees the truth + «Починить» instead of a silently-broken screen.
            # Gated on the same kill switch as the other build/runtime cards.
            if _bad_entities and get_settings().use_error_cards:
                for _ent in _bad_entities:
                    await app_errors.publish(
                        factory,
                        project_id,
                        assistant_message_id,
                        category="schema",
                        title="Ошибка в схеме данных",
                        detail=(
                            f"Сущность «{_ent}» не читается из-за ошибки в её "
                            f"JSON-схеме, поэтому раздел «{_ent}» не будет работать. "
                            f"Нажмите «Починить», чтобы я пересобрал схему."
                        ),
                        file=f"entities/{_ent}.json",
                    )

            # Sprint 4 — fingerprint the shipped freeform page into the global
            # pool (cross-project dedup signal for next time). Fail-soft.
            if _gen_mode == "freeform" and _acc_fingerprint is not None:
                try:
                    from omnia_api.services import originality as _originality
                    await _originality.remember(str(project_id), _acc_fingerprint)
                except Exception as _fp_exc:
                    print(f"[PP] originality remember failed: {_fp_exc!r}", flush=True)

            # Fullstack projects: push the same files into the live dev
            # container so the user sees the new code immediately via HMR.
            # Failure here is logged + surfaced as a chat notice but does
            # NOT roll the snapshot back — the canonical state is still in
            # git/MinIO and the user can hit "Запустить" again to retry.
            if project is not None and project.template in CONTAINER_NEXT:
                # Error cards for drizzle / sync failures are a strict improvement
                # over the old italic notice, so they're always on. Only the new,
                # riskier compile probe (extra orchestrator call) is flag-gated.
                probe_compile = get_settings().use_error_cards
                try:
                    hot = await orchestrator_client.hot_reload(
                        project_id=project_id,
                        slug=project.slug,
                        files=files,
                    )
                    print(
                        f"[PP] hot_reload OK written={hot.get('written')} "
                        f"drizzle={hot.get('drizzle_exit_code', 'n/a')}",
                        flush=True,
                    )
                    drizzle_exit = hot.get("drizzle_exit_code")
                    if drizzle_exit and drizzle_exit not in ("0", "n/a"):
                        # Drizzle push failed — surface it, don't fail the prompt.
                        await app_errors.publish(
                            factory,
                            project_id,
                            assistant_message_id,
                            category="schema",
                            detail=(
                                hot.get("drizzle_stderr_tail")
                                or f"drizzle-kit push завершился с кодом {drizzle_exit}."
                            ),
                            file="src/lib/db/schema.ts",
                        )
                    elif int(get_settings().app_self_repair_passes) > 0:
                        # App self-repair loop (Claude-Code verify→fix, DARK). Probe
                        # the live container for a REAL compile/runtime error, ask the
                        # app_doctor model (DeepSeek) to fix it, hot-reload, re-probe —
                        # up to N passes — then commit the repaired files as a FOLLOW-UP
                        # snapshot so the fix survives a rebuild. Fail-soft: any trouble
                        # (or still broken) falls back to surfacing the card as before.
                        _sr_passes = int(get_settings().app_self_repair_passes)

                        async def _sr_notice(delta: str) -> None:
                            await publish_event(
                                project_id,
                                "llm.chunk",
                                {"message_id": str(assistant_message_id), "delta": delta},
                            )

                        try:
                            _repaired, _sr_err, _sr_cat = await _run_app_self_repair(
                                project_id, project.slug, files,
                                passes=_sr_passes, on_notice=_sr_notice,
                            )
                        except Exception as _sr_exc:
                            print(f"[PP] self_repair loop failed: {_sr_exc!r}", flush=True)
                            _repaired, _sr_err, _sr_cat = {}, None, ""
                        if _repaired and _sr_err is None:
                            # GREEN after repair — commit the fix as a follow-up snapshot
                            # (the first snapshot already shipped; this records the heal
                            # and keeps it rollback-able + in git for the next rebuild).
                            try:
                                files = {**files, **_repaired}
                                _rep_sha = await asyncio.to_thread(
                                    repo_svc.commit_files, project_id, files,
                                    "AI: авто-починка сборки", new_sha,
                                )
                                async with factory() as _s:
                                    _rep_snap = Snapshot(
                                        project_id=project_id, commit_sha=_rep_sha,
                                        prompt_text="авто-починка сборки",
                                        model_id=snapshot_model_id,
                                        parent_id=new_snapshot_id,
                                    )
                                    _s.add(_rep_snap)
                                    await _s.flush()
                                    _rep_proj = await _s.get(Project, project_id)
                                    if _rep_proj is not None:
                                        _rep_proj.current_snapshot_id = _rep_snap.id
                                    _rep_msg = await _s.get(Message, assistant_message_id)
                                    if _rep_msg is not None:
                                        _rep_msg.content = (_rep_msg.content or "") + (
                                            "\n\n*✓ Нашёл и починил ошибку сборки — "
                                            "приложение собирается.*"
                                        )
                                        _rep_msg.snapshot_id = _rep_snap.id
                                    await _s.commit()
                                    await _s.refresh(_rep_snap)
                                await asyncio.to_thread(enqueue_preview, _rep_snap.id)
                                await publish_event(
                                    project_id, "snapshot.created",
                                    {"snapshot": _snapshot_payload(_rep_snap)},
                                )
                                await _sr_notice(
                                    "\n\n*✓ Починил ошибку — приложение собирается.*\n\n"
                                )
                                print(f"[PP] self_repair committed sha={_rep_sha[:8]}", flush=True)
                            except Exception as _rc_exc:
                                print(f"[PP] self_repair commit failed: {_rc_exc!r}", flush=True)
                        elif _sr_err is not None:
                            # Still broken after the loop — surface the card (old path).
                            await app_errors.publish(
                                factory, project_id, assistant_message_id,
                                category=_sr_cat or "compile",
                                detail=str(
                                    _sr_err.get("error") or "Не удалось собрать приложение."
                                ),
                                file=_sr_err.get("file"),
                            )
                    elif probe_compile:
                        # Files synced cleanly — Turbopack now recompiles async.
                        # Probe for a compile error in the background so the card
                        # arrives without holding up llm.done.
                        _spawn_compile_probe(
                            factory, project_id, assistant_message_id, project.slug
                        )
                    # V1.6 16/5 — assert the awwwards COMPOSITION floor (taste +
                    # hierarchy) on the LIVE container. Entity apps skip
                    # acceptance.evaluate, so this worker job is the ONLY place the
                    # pillar-1 beauty floor gets teeth on the dominant entity class.
                    # Runs in the worker (the only process that can reach the dev
                    # container over the runtime network), not here. Independent of
                    # the compile probe — it surfaces its own quality card.
                    if (
                        not drizzle_exit or drizzle_exit in ("0", "n/a")
                    ) and project.template in ("nextjs_entities", "fullstack"):
                        if get_settings().acceptance_entity_composition_gate:
                            await asyncio.to_thread(
                                enqueue_entity_gate,
                                assistant_message_id,
                                project_id,
                                project.slug,
                            )
                except Exception as hot_exc:
                    print(f"[PP] hot_reload failed: {hot_exc!r}", flush=True)
                    await app_errors.publish(
                        factory,
                        project_id,
                        assistant_message_id,
                        category="runtime",
                        title="Синхронизация с контейнером не удалась",
                        detail=(
                            f"Снапшот сохранён, но файлы не доехали до dev-контейнера: "
                            f"{hot_exc}. Нажми «Запустить» в верхней панели, чтобы поднять "
                            f"среду выполнения."
                        ),
                        file=None,
                        fixable=False,
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
                assistant_message_id,
                _final_no_files,
                usage_data,
                snapshot_id=None,
            )
            await publish_event(
                project_id,
                "llm.error",
                {"message_id": str(assistant_message_id), "error": hint},
            )

        await publish_event(
            project_id,
            "llm.done",
            {
                "message_id": str(assistant_message_id),
                "tokens_in": int(usage_data["tokens_in"]) if usage_data else None,
                "tokens_out": int(usage_data["tokens_out"]) if usage_data else None,
                "cost_rub": float(usage_data["cost_rub"]) if usage_data else None,
            },
        )

    except Exception as e:
        import traceback as _tb
        print(f"[PP] FATAL project={project_id} asst={assistant_message_id} err={e!r}\n{_tb.format_exc()}", flush=True)
        # Mark the assistant row as failed so the UI input unblocks instead of
        # spinning forever; otherwise tokens_out stays NULL and ChatPanel
        # treats the message as still-streaming.
        try:
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
        # Стрим завершён (done / error / отмена / краш) — снимаем горячее
        # состояние, чтобы reconnect после конца не пытался досматривать
        # мёртвый поток. Best-effort: ошибка Redis тут не должна валить ответ.
        try:
            await clear_stream_state(project_id, assistant_message_id)
        except Exception:
            pass


async def _finalize_message(
    factory,
    message_id: UUID,
    content: str,
    usage_data: dict[str, float | int] | None,
    snapshot_id: UUID | None,
) -> None:
    async with factory() as session:
        msg = await session.get(Message, message_id)
        if msg is None:
            return
        msg.content = content
        if snapshot_id is not None:
            msg.snapshot_id = snapshot_id
        if usage_data:
            msg.tokens_in = int(usage_data.get("tokens_in") or 0)
            msg.tokens_out = int(usage_data.get("tokens_out") or 0)
        await session.commit()
