"""Resolve one admitted request for text, images and fonts, never source restore."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation.agent_messages import _is_retry_request
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.generation.request_binding import bound_message_request
from yleum_api.services.generation.source_checkpoint import checkpoint_reference_message
from yleum_api.services.generation_runs import load_generation_dispatch


@dataclass(frozen=True)
class RequestSource:
    message_id: UUID
    prompt: str


def _has_assets(message: Message) -> bool:
    return bool(message.reference_images or message.font_selections or message.resolved_fonts)


def _admitted(run: GenerationRun, baseline: SourceBaseline) -> bool:
    try:
        dispatch = load_generation_dispatch(run)
    except ValueError:
        return False
    return (
        dispatch.user_message_id == run.user_message_id
        and dispatch.assistant_message_id == run.assistant_message_id
        and dispatch.current_snapshot_id == baseline.snapshot_id
    )


async def _same_baseline(
    session: AsyncSession,
    ids: GenerationIds,
    baseline: SourceBaseline,
) -> bool:
    project = await session.get(Project, ids.project_id, populate_existing=True)
    if (
        project is None
        or project.owner_id != ids.user_id
        or project.current_snapshot_id != baseline.snapshot_id
    ):
        return False
    if baseline.snapshot_id is None:
        return baseline.sha is None
    snapshot = await session.get(Snapshot, baseline.snapshot_id, populate_existing=True)
    return bool(
        snapshot is not None
        and snapshot.project_id == ids.project_id
        and isinstance(baseline.sha, str)
        and re.fullmatch(r"[a-f0-9]{40}", baseline.sha)
        and snapshot.commit_sha == baseline.sha
    )


async def _ambiguous(session: AsyncSession, run: GenerationRun) -> bool:
    return (
        await session.scalar(
            select(1)
            .select_from(GenerationRun)
            .where(
                GenerationRun.project_id == run.project_id,
                GenerationRun.created_at == run.created_at,
                GenerationRun.id != run.id,
            )
            .limit(1)
        )
        is not None
    )


async def _checkpoint_request(
    session: AsyncSession,
    run: GenerationRun,
    ids: GenerationIds,
    baseline: SourceBaseline,
) -> RequestSource:
    if run.user_message_id is None or run.assistant_message_id is None:
        raise ValueError("reference continuation admission is missing")
    message_id = await checkpoint_reference_message(
        session,
        ids=GenerationIds(
            run.id,
            ids.project_id,
            ids.user_id,
            run.user_message_id,
            run.assistant_message_id,
        ),
        baseline=baseline,
    )
    message = await session.get(Message, message_id)
    if message is None:
        raise ValueError("reference continuation message disappeared")
    return RequestSource(message.id, message.content)


async def resolve_request_source(
    session: AsyncSession,
    *,
    ids: GenerationIds,
    baseline: SourceBaseline,
) -> RequestSource:
    """Re-prove adjacent manual retries on every load, including cold resumes.

    Explicit checkpoint provenance keeps its existing validator. The fallback
    below grants only the original request's immutable attachment bindings; it
    neither restores draft source nor changes any run/message/dispatch identity.
    """
    current = await session.get(GenerationRun, ids.run_id)
    if (
        current is None
        or current.project_id != ids.project_id
        or current.user_id != ids.user_id
        or current.user_message_id != ids.user_message_id
        or current.assistant_message_id != ids.assistant_message_id
    ):
        raise ValueError("retry request owner conflicts")
    message = await session.get(Message, current.user_message_id)
    bound_message_request(current, message, project_id=ids.project_id, run_id=current.id)
    assert message is not None
    original = RequestSource(message.id, message.content)
    state = current.agent_state if isinstance(current.agent_state, dict) else {}
    if "source_checkpoint_resume" in state:
        return await _checkpoint_request(session, current, ids, baseline)
    if (
        not _is_retry_request(message.content)
        or _has_assets(message)
        or current.response_mode not in {"build", "edit"}
        or not _admitted(current, baseline)
        or not await _same_baseline(session, ids, baseline)
    ):
        return original

    for _ in range(20):
        if await _ambiguous(session, current):
            return original
        # Owner filtering here would skip a foreign predecessor instead of
        # treating it as a barrier. Project history supplies the actual adjacency.
        previous = await session.scalar(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == ids.project_id,
                GenerationRun.id != current.id,
                GenerationRun.created_at <= current.created_at,
            )
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
            .limit(1)
        )
        if (
            previous is None
            or previous.created_at >= current.created_at
            or previous.user_id != ids.user_id
            or previous.status not in {"failed", "cancelled"}
            or previous.response_mode not in {"build", "edit"}
            or previous.user_message_id is None
            or not _admitted(previous, baseline)
            or await _ambiguous(session, previous)
        ):
            return original
        prior_message = await session.get(Message, previous.user_message_id)
        try:
            bound_message_request(
                previous,
                prior_message,
                project_id=ids.project_id,
                run_id=previous.id,
            )
        except ValueError:
            return original
        assert prior_message is not None
        if not prior_message.content.strip():
            return original
        state = previous.agent_state if isinstance(previous.agent_state, dict) else {}
        if "source_checkpoint_resume" in state:
            return await _checkpoint_request(session, previous, ids, baseline)
        if not _is_retry_request(prior_message.content) or _has_assets(prior_message):
            return RequestSource(prior_message.id, prior_message.content)
        current = previous
    return original
