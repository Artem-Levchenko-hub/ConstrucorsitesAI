from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

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
from omnia_api.core.redis import get_redis, publish_event
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.message import MessagePublic, PromptRequest, PromptResponse
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.director_polish import director_polish_generate
from omnia_api.services.file_extractor import (
    UnsafePathError,
    apply_edits,
    extract_edits,
    extract_files,
)
from omnia_api.services.image_resolver import resolve_images
from omnia_api.services.intent_triage import ORCHESTRATE, decide_intent
from omnia_api.services.link_validator import find_dead_links, repair_dead_links_inline
from omnia_api.services.llm_client import set_free_generation, stream_chat_completion
from omnia_api.services.multipass_generator import multipass_generate
from omnia_api.services.preset_classifier import classify_preset
from omnia_api.services.prompt_builder import KIT_FILES, build_messages
from omnia_api.services.queue import enqueue_preview
from omnia_api.services.ui_audit import audit as ui_audit
from omnia_api.services.ui_audit import format_failures_for_retry
from omnia_api.services.vendor_profiles import vendor_directive
from omnia_api.services.visual_enricher import enrich_files as enrich_visual_files

RESERVED_BALANCE = Decimal("5.0000")  # минимум перед стартом генерации

# Snapshot/Message.model_id label for the orchestrated (role-mix) path. The real
# per-pass models (Opus director, DeepSeek polish, …) are logged per-call in the
# gateway's `usage` table; this label just marks the snapshot as orchestrated
# rather than a single user-picked model.
ORCHESTRATION_LABEL = "topmix-v1"

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


async def _ensure_owner(session: SessionDep, project_id: UUID, user_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


@router.post(
    "/{project_id}/prompt",
    response_model=PromptResponse,
    status_code=status.HTTP_202_ACCEPTED,
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
    is_free = (current_user.free_generations_used or 0) < FREE_GENERATION_LIMIT
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
    intent = decide_intent(
        payload.prompt,
        is_first_prompt=project.current_snapshot_id is None,
        selected_count=len(selected_dump or []),
    )
    orchestrate = intent == ORCHESTRATE

    # Model choice is server-side — the user never picks. `force_model` is the
    # hidden admin override (env FORCE_MODEL). Otherwise the triage decides:
    # orchestrate → director (Opus) drives prompt-routing + Director→Polish;
    # cheap → a single reliable Haiku shot (role `edit`).
    force_model = get_settings().force_model or None
    routing_model = force_model or model_for_role("director" if orchestrate else "edit")

    user_msg = Message(
        project_id=project_id,
        role="user",
        content=payload.prompt,
        model_id=None,  # user turns have no model
        selected_elements=selected_dump,
    )
    assistant_msg = Message(
        project_id=project_id,
        role="assistant",
        content="",
        # Orchestrated runs carry the mix label; cheap runs log the real model.
        model_id=force_model or (ORCHESTRATION_LABEL if orchestrate else routing_model),
    )
    session.add_all([user_msg, assistant_msg])
    await session.commit()
    await session.refresh(user_msg)
    await session.refresh(assistant_msg)

    _spawn_process_prompt(
        project_id=project_id,
        user_id=current_user.id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant_msg.id,
        current_snapshot_id=project.current_snapshot_id,
        prompt_text=payload.prompt,
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

    return PromptResponse(message_id=assistant_msg.id, snapshot_id=None)


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


_KIT_LINK = '<link rel="stylesheet" href="assets/omnia-kit.css">'
_KIT_SCRIPT = '<script src="assets/omnia-kit.js" defer></script>'


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
        has_js = "assets/omnia-kit.js" in content
        if has_css and has_js:
            continue
        inject = ""
        if not has_css:
            inject += "  " + _KIT_LINK + "\n"
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
    print(f"[PP] start project={project_id} asst_msg={assistant_message_id} model={model_id} free={is_free} force={force_model}", flush=True)

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    accumulated = ""
    usage_data: dict[str, float | int] | None = None
    current_sha: str | None = None
    current_files: dict[str, str] = {}
    history_serialized: list[dict[str, str]] = []
    project_template = "blank"
    project_name = ""
    project_design_preset_id: str | None = None
    project_image_gen_enabled: bool = True
    project_image_gen_enabled: bool = True

    try:
        async with factory() as session:
            if current_snapshot_id:
                snap = await session.get(Snapshot, current_snapshot_id)
                if snap is not None:
                    current_sha = snap.commit_sha
            proj = await session.get(Project, project_id)
            if proj is not None:
                project_template = proj.template
                project_name = proj.name or ""
                project_design_preset_id = proj.design_preset_id
                project_image_gen_enabled = proj.image_gen_enabled
            res = await session.execute(
                select(Message)
                .where(Message.project_id == project_id)
                .where(Message.id != assistant_message_id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            rows = list(reversed(list(res.scalars().all())))
            history_serialized = [
                {"role": m.role, "content": m.content} for m in rows if m.content
            ]
        print(f"[PP] ctx_loaded sha={current_sha} history={len(history_serialized)}", flush=True)

        if current_sha:
            current_files = await asyncio.to_thread(
                repo_svc.read_files, project_id, current_sha
            )
        print(f"[PP] files_loaded count={len(current_files)}", flush=True)

        # Kit files are Omnia-managed infra — keep them out of the model's context
        # (saves tokens and stops the model rewriting them from what it "saw").
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
        )
        print(f"[PP] messages_built count={len(messages)}", flush=True)

        # Phase 11 — resolve the generation mode ONCE from the routing model so
        # the prompt we built (freeform vs catalog) and the way we parse the
        # answer below never disagree. The empty-response fallback may switch
        # the model later, but the mode is fixed by the first pass's prompt.
        from omnia_api.core.config import generation_mode as _generation_mode
        _gen_mode = _generation_mode(model_id, str(project_id))
        print(f"[PP] gen_mode={_gen_mode}", flush=True)

        # ──────────────────────────────────────────────────────────────
        # Inner stream loop, extracted so we can retry the whole thing
        # against a fallback model when the primary returns junk.
        # `accumulated` and `usage_data` are mutated through closure refs
        # via the dict trick — Python doesn't let us rebind outer names
        # cleanly from a nested coroutine.
        # ──────────────────────────────────────────────────────────────
        state: dict[str, object] = {"accumulated": "", "usage": None, "error": None}

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
            )
            if _dp_active:
                source = director_polish_generate(
                    base_messages=messages,
                    user_prompt=prompt_text,
                    director_model=force_all,
                    polish_model=force_all,
                    user_id=user_id,
                    project_id=project_id,
                    message_id=assistant_message_id,
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
                else:
                    _ss_model = force_all or use_model
                # Per-vendor directive on the freeform/single-shot path. IR JSON
                # is expected ONLY on premium + catalog mode; otherwise the model
                # emits freeform HTML, so json_strict must stay False (a "JSON
                # only" nudge would corrupt an HTML response).
                _expects_ir = (
                    _settings.use_section_catalog
                    and tier_for_model(_ss_model) == "premium"
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
                    await publish_event(
                        project_id,
                        "llm.chunk",
                        {
                            "message_id": str(assistant_message_id),
                            "delta": event["delta"],
                        },
                    )
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

        # --- Pass 1: primary model (role-orchestrated unless admin-forced) ---
        await _run_stream(model_id, force_all=force_model)
        accumulated = str(state["accumulated"])
        usage_data = state["usage"]  # type: ignore[assignment]
        stream_error = state["error"]

        if stream_error:
            print(f"[PP] stream_error err={stream_error!r}", flush=True)
            await _finalize_message(
                factory, assistant_message_id, accumulated, usage_data, snapshot_id=None
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
        if _gen_mode == "catalog":
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
            await _finalize_message(
                factory, assistant_message_id, accumulated, usage_data, snapshot_id=None
            )
            await publish_event(
                project_id,
                "llm.error",
                {"message_id": str(assistant_message_id), "error": str(e)},
            )
            return

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
                await _run_stream(model_id, force_multipass=True, force_all=force_model)
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
        if files and project_template != "fullstack":
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
            if len(dead) > _DEAD_LINK_LLM_THRESHOLD:
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
        if files and project_template != "fullstack":
            files = {p: c for p, c in files.items() if p not in KIT_FILES}
            files = _ensure_kit_linked(files)

        # Image-resolver: if Haiku/Sonnet wrote <img data-omnia-gen="..."> tags,
        # call gateway → MinIO and rewrite the tags with real src URLs BEFORE
        # the commit so the snapshot / GitHub export / rollback all carry final
        # URLs (single source of truth in git). For fullstack the subsequent
        # hot_reload pushes the rewritten files into the dev container, so HMR
        # picks up the real images automatically. Per-project opt-out via
        # projects.image_gen_enabled (TopBar toggle).
        # Visual enricher — гарантируем декор в каждом <section> голого HTML
        # ДО image_resolver, чтобы добавленные нами SVG-теги не считались
        # «уже есть SVG» при последующих pass'ах. Запускаем безусловно (декор
        # нужен независимо от toggle картинок).
        if files:
            try:
                files, enr_count, enr_total = enrich_visual_files(files)
                print(
                    f"[PP] visual_enricher enriched={enr_count} sections={enr_total}",
                    flush=True,
                )
            except Exception as enr_exc:
                print(f"[PP] visual_enricher failed: {enr_exc!r}", flush=True)

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
            try:
                files, resolved, total = await resolve_images(files, str(project_id))
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
            and project_template not in ("fullstack", "tgbot", "api")
            and _acc_settings.use_acceptance_gate
            and _gen_mode in ("freeform", "catalog")
        ):
            from omnia_api.services import acceptance as _acceptance
            _max_acc = max(0, int(_acc_settings.acceptance_max_retries))
            _verdict = None
            try:
                for _acc_attempt in range(_max_acc + 1):
                    _verdict = await _acceptance.evaluate(
                        files,
                        project_id=str(project_id),
                        prompt_context=prompt_text,
                        user_id=str(user_id),
                        # Originality (anti-generic) only for freeform — catalog
                        # pages are template-based and intentionally alike.
                        run_originality=(_gen_mode == "freeform"),
                    )
                    print(
                        f"[PP] acceptance attempt={_acc_attempt} passed={_verdict.passed} "
                        f"verdict={_verdict.verdict} score={_verdict.score} "
                        f"struct={_verdict.structural_ok} resp={_verdict.responsive_ok} "
                        f"vision={_verdict.vision_ran}",
                        flush=True,
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
                    if _verdict.passed or not _verdict.feedback or _acc_attempt >= _max_acc:
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
                    await _run_stream(effective_model, force_all=force_model)
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
                    try:
                        _repaired, _, _ = enrich_visual_files(_repaired)
                    except Exception:
                        pass
                    if project_image_gen_enabled:
                        try:
                            _repaired, _, _ = await resolve_images(_repaired, str(project_id))
                        except Exception:
                            pass
                    files = _repaired
                    accumulated = accumulated + _repair_acc

                # Remember an accepted freeform page's fingerprint (Sprint 4)
                # so later generations can be nudged off near-duplicates.
                if _verdict is not None and _verdict.passed:
                    _acc_fingerprint = _verdict.fingerprint

                # Freeform exhausted its retries and still fails → regenerate
                # once via the catalog/IR path (guaranteed valid page).
                if (
                    _verdict is not None
                    and not _verdict.passed
                    and _gen_mode == "freeform"
                    and _acc_settings.use_section_catalog
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

        new_snapshot_id: UUID | None = None
        if files:
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
                    else (force_model or ORCHESTRATION_LABEL)
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
                    msg.content = accumulated
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
            if project is not None and project.template == "fullstack":
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
                        # Drizzle push failed — tell the user, don't fail the prompt.
                        await publish_event(
                            project_id,
                            "llm.chunk",
                            {
                                "message_id": str(assistant_message_id),
                                "delta": (
                                    f"\n\n*Файлы записаны в dev-контейнер, но `drizzle-kit push` "
                                    f"завершился с кодом {drizzle_exit}. Проверь src/lib/db/schema.ts и "
                                    f"подключение к Postgres.*\n\n"
                                ),
                            },
                        )
                except Exception as hot_exc:
                    print(f"[PP] hot_reload failed: {hot_exc!r}", flush=True)
                    await publish_event(
                        project_id,
                        "llm.chunk",
                        {
                            "message_id": str(assistant_message_id),
                            "delta": (
                                f"\n\n*Снапшот сохранён в git, но синхронизация с dev-контейнером "
                                f"не удалась: {hot_exc}. Нажми «Запустить» в верхней панели чтобы "
                                f"проверить runtime.*\n\n"
                            ),
                        },
                    )
        else:
            # Модель ответила, но ни одного <file path="...">...</file> в выводе.
            # Раньше тут была тишина — UI получал только llm.done и думал, что
            # всё ок, хотя preview не обновлялся. Теперь шлём явный llm.error,
            # чтобы юзер видел, что произошло, и сообщение в чате становится
            # видимым (а не висит «пустым» из-за пустого snapshot_id).
            await _finalize_message(
                factory, assistant_message_id, accumulated, usage_data, snapshot_id=None
            )
            hint = (
                "Модель не вернула ни одного файла в формате "
                '<file path="...">...</file>. Похоже, выбранная модель плохо '
                "следует структурному формату для генерации сайтов — попробуй "
                "Claude Haiku 4.5 (быстро) или Claude Sonnet 4.6 (качественно)."
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
