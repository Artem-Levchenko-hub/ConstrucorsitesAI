from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import get_redis, publish_event
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.message import MessagePublic, PromptRequest, PromptResponse
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.file_extractor import (
    UnsafePathError,
    apply_edits,
    extract_edits,
    extract_files,
)
from omnia_api.services.image_resolver import resolve_images
from omnia_api.services.link_validator import find_dead_links
from omnia_api.services.llm_client import stream_chat_completion
from omnia_api.services.preset_classifier import classify_preset
from omnia_api.services.prompt_builder import KIT_FILES, build_messages
from omnia_api.services.queue import enqueue_preview
from omnia_api.services.visual_enricher import enrich_files as enrich_visual_files

RESERVED_BALANCE = Decimal("5.0000")  # минимум перед стартом генерации

router = APIRouter(prefix="/api/projects", tags=["messages"])

# Strong references to fire-and-forget background tasks. Without this set,
# `asyncio.create_task(...)` returns a Task whose only reference is the
# anonymous expression — the GC can collect it mid-flight, silently aborting
# the prompt-processing coroutine and leaving the assistant message empty
# in the DB. https://docs.python.org/3/library/asyncio-task.html#creating-tasks
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _spawn_process_prompt(**kwargs: object) -> None:
    """Fire-and-forget _process_prompt with a guaranteed strong reference.

    Any exception that escapes the coroutine is logged via the structlog
    fallback below, so it surfaces in `docker logs` instead of being eaten
    by `_process_prompt`'s broad except → publish_event(llm.error).
    """
    import logging

    log = logging.getLogger(__name__)
    task = asyncio.create_task(_process_prompt(**kwargs))  # type: ignore[arg-type]
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task[None]) -> None:
        _BACKGROUND_TASKS.discard(t)
        if t.cancelled():
            log.warning("_process_prompt task cancelled")
            return
        exc = t.exception()
        if exc is not None:
            log.error("_process_prompt failed", exc_info=exc)

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

    wallet = await session.get(Wallet, current_user.id)
    if wallet is None or wallet.balance_rub < RESERVED_BALANCE:
        raise ApiError("wallet_empty", "insufficient balance", 402)

    # Select-mode: элементы, выделенные в превью, с комментариями. Сериализуем в
    # list[dict] для JSONB-колонки и для передачи в фоновую _process_prompt
    # (сервис prompt_builder не знает про Pydantic-схему — R-07).
    selected_dump = (
        [el.model_dump() for el in payload.selected_elements]
        if payload.selected_elements
        else None
    )

    user_msg = Message(
        project_id=project_id,
        role="user",
        content=payload.prompt,
        model_id=payload.model_id,
        selected_elements=selected_dump,
    )
    assistant_msg = Message(
        project_id=project_id,
        role="assistant",
        content="",
        model_id=payload.model_id,
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
        model_id=payload.model_id,
        selected_elements=selected_dump,
    )

    # Reset the orchestrator's hibernate timer — a user submitting a new prompt
    # is the strongest possible "this project is active" signal. The hibernate
    # loop subscribes to `activity:*` on Redis and resets its in-memory
    # last_activity[project_id] when this lands. Fire-and-forget — a Redis
    # hiccup must not kill a live prompt.
    try:
        await get_redis().publish(f"activity:{project_id}", "")
    except Exception:  # noqa: BLE001 — best-effort signal, not load-bearing
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


async def _process_prompt(
    project_id: UUID,
    user_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
    current_snapshot_id: UUID | None,
    prompt_text: str,
    model_id: str,
    selected_elements: list[dict[str, Any]] | None = None,
) -> None:
    import logging as _log_mod
    _log = _log_mod.getLogger(__name__)
    print(f"[PP] start project={project_id} asst_msg={assistant_message_id} model={model_id}", flush=True)

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
        )
        print(f"[PP] messages_built count={len(messages)}", flush=True)

        # ──────────────────────────────────────────────────────────────
        # Inner stream loop, extracted so we can retry the whole thing
        # against a fallback model when the primary returns junk.
        # `accumulated` and `usage_data` are mutated through closure refs
        # via the dict trick — Python doesn't let us rebind outer names
        # cleanly from a nested coroutine.
        # ──────────────────────────────────────────────────────────────
        state: dict[str, object] = {"accumulated": "", "usage": None, "error": None}

        async def _run_stream(use_model: str) -> None:
            """Drain one stream from the gateway into `state`."""
            state["accumulated"] = ""
            state["usage"] = None
            state["error"] = None
            async for event in stream_chat_completion(
                messages,
                use_model,
                str(user_id),
                str(project_id),
                str(assistant_message_id),
            ):
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

        # --- Pass 1: primary model ----------------------------------
        await _run_stream(model_id)
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
            for fb_model in _EMPTY_RESPONSE_FALLBACKS.get(model_id, []):
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
                await _run_stream(fb_model)
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

        # --- Pass N+1: one-shot dead-link repair (static only) ----------
        # The prompt forbids dead links, but weaker models still slip in
        # href="#" / broken anchors. If we find any, re-prompt ONCE with the
        # exact issues and keep the result only if it's strictly better.
        # Fail-safe: a single pass, never blocks the commit (R-10 fail fast).
        if files and project_template != "fullstack":
            dead = find_dead_links(files)
            if dead:
                print(f"[PP] dead_links found={len(dead)} -> repair pass", flush=True)
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
                await _run_stream(effective_model)
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
                snapshot = Snapshot(
                    project_id=project_id,
                    commit_sha=new_sha,
                    prompt_text=prompt_text,
                    model_id=model_id,
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
