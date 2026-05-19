from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.rate_limit import limiter
from omnia_api.core.redis import publish_event
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.message import MessagePublic, PromptRequest, PromptResponse
from omnia_api.services import repo as repo_svc
from omnia_api.services.billing import charge_for_message
from omnia_api.services.file_extractor import UnsafePathError, extract_files
from omnia_api.services.llm_client import stream_chat_completion
from omnia_api.services.prompt_builder import build_messages
from omnia_api.services.queue import enqueue_preview

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
@limiter.limit("10/minute")
@limiter.limit("100/hour")
async def post_prompt(
    request: Request,
    project_id: UUID,
    payload: PromptRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> PromptResponse:
    project = await _ensure_owner(session, project_id, current_user.id)

    wallet = await session.get(Wallet, current_user.id)
    if wallet is None or wallet.balance_rub < RESERVED_BALANCE:
        raise ApiError("wallet_empty", "insufficient balance", 402)

    user_msg = Message(
        project_id=project_id,
        role="user",
        content=payload.prompt,
        model_id=payload.model_id,
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
    )

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


async def _process_prompt(
    project_id: UUID,
    user_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
    current_snapshot_id: UUID | None,
    prompt_text: str,
    model_id: str,
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

    try:
        async with factory() as session:
            if current_snapshot_id:
                snap = await session.get(Snapshot, current_snapshot_id)
                if snap is not None:
                    current_sha = snap.commit_sha
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

        messages = build_messages(current_files, history_serialized, prompt_text)
        print(f"[PP] messages_built count={len(messages)}", flush=True)

        async for event in stream_chat_completion(
            messages,
            model_id,
            str(user_id),
            str(project_id),
            str(assistant_message_id),
        ):
            if "delta" in event:
                accumulated += event["delta"]
                await publish_event(
                    project_id,
                    "llm.chunk",
                    {
                        "message_id": str(assistant_message_id),
                        "delta": event["delta"],
                    },
                )
            elif "usage" in event:
                usage_data = event["usage"]  # type: ignore[assignment]
            elif "error" in event:
                print(f"[PP] stream_error err={event['error']!r}", flush=True)
                await _finalize_message(
                    factory, assistant_message_id, accumulated, usage_data, snapshot_id=None
                )
                await publish_event(
                    project_id,
                    "llm.error",
                    {"message_id": str(assistant_message_id), "error": event["error"]},
                )
                return

        print(f"[PP] stream_complete acc_len={len(accumulated)} usage={usage_data}", flush=True)

        try:
            files = extract_files(accumulated)
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

                if usage_data and float(usage_data.get("cost_rub") or 0) > 0:
                    new_balance = await charge_for_message(
                        session,
                        user_id,
                        assistant_message_id,
                        project_id,
                        model_id,
                        int(usage_data.get("tokens_in") or 0),
                        int(usage_data.get("tokens_out") or 0),
                        Decimal(str(usage_data["cost_rub"])),
                        f"Generation: {prompt_text[:80]}",
                    )
                    await session.commit()
                    await publish_event(
                        project_id,
                        "wallet.updated",
                        {"balance_rub": float(new_balance)},
                    )
                else:
                    await session.commit()

                await session.refresh(snapshot)

            await asyncio.to_thread(enqueue_preview, new_snapshot_id)
            await publish_event(
                project_id,
                "snapshot.created",
                {"snapshot": _snapshot_payload(snapshot)},
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

    except Exception as e:  # noqa: BLE001 — широкий лов, чтобы dispatch error в WS
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
