"""Acceptance-lock: a mid-build LLM stream error must leave a FINALISED,
human-readable assistant chat row — not a blank, forever-"streaming" one.

Blind spot (dogfood run #25, BS-28): when the brief/writer pass fails on a
handled upstream error (budget / gateway timeout / rate-limit /
model_unavailable), the `stream_error` branch in `_process_prompt`
(routers/messages.py) used to finalise the assistant message with the empty
`accumulated` string and a falsy `usage_data`, so the persisted row had
`content=""` AND `tokens_out=NULL`. The error reached the user ONLY via the
ephemeral `llm.error` WebSocket event — a user who reloaded or dropped the
socket during the multi-minute build saw a blank bubble with a stuck spinner
and no clue WHY it stopped (live-reproduced on prod: project
dogfood-crm-klienty-e28e8d, writer budget error → assistant row
content='' / tokens_out=NULL / snapshot_id=NULL).

The fix mirrors the crash-path recovery `_emergency_error`: persist a readable
error body (when nothing streamed) + zero tokens so the row finalises.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.routers.messages import _failed_build_body, _finalize_message

pytestmark = pytest.mark.asyncio


# ── Pure-logic lock (no DB) ──────────────────────────────────────────────────


def test_failed_build_body_writes_error_when_nothing_streamed() -> None:
    """Empty / whitespace-only stream → a readable error body, never ''."""
    body = _failed_build_body("", "writer pass failed: insufficient balance")
    assert body != ""
    assert "Ошибка" in body
    assert "insufficient balance" in body

    # whitespace-only is treated as empty too
    assert _failed_build_body("   \n\t ", "boom").startswith("[Ошибка")


def test_failed_build_body_preserves_partial_content() -> None:
    """If the model streamed real partial output, keep it (don't clobber)."""
    partial = "<file path=\"src/app/page.tsx\">half a file"
    assert _failed_build_body(partial, "stream dropped") == partial


# ── DB lock: the finalised row must not look "streaming" ─────────────────────


@pytest_asyncio.fixture
async def _assistant_msg(db_session: AsyncSession) -> Message:
    user = User(email=f"fail-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="dogfood-fail",
        slug=f"df-{uuid.uuid4().hex[:6]}",
        template="nextjs_entities",
    )
    db_session.add(project)
    await db_session.flush()
    msg = Message(
        project_id=project.id,
        role="assistant",
        content="",
        tokens_in=None,
        tokens_out=None,
    )
    db_session.add(msg)
    await db_session.commit()
    return msg


async def test_stream_error_finalize_persists_error_and_clears_streaming(
    _assistant_msg: Message, test_engine
) -> None:
    """Replays exactly what the fixed `stream_error` branch does and asserts the
    persisted row is non-empty AND has tokens_out set (i.e. not "streaming")."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    err = "writer pass failed: Недостаточно средств на балансе LLM-провайдера"

    body = _failed_build_body("", err)  # nothing streamed
    await _finalize_message(
        factory,
        _assistant_msg.id,
        body,
        {"tokens_in": 0, "tokens_out": 0},
        snapshot_id=None,
    )

    async with factory() as s:
        row = await s.get(Message, _assistant_msg.id)
        assert row is not None
        assert row.content != "", "blank assistant row leaves the user with no explanation"
        assert "Ошибка" in row.content
        assert row.tokens_out == 0, "tokens_out=NULL makes the chat row look forever-streaming"


async def test_old_path_left_row_blank_and_streaming_evidence(
    _assistant_msg: Message, test_engine
) -> None:
    """Evidence (green): the PRE-fix call shape (empty content + falsy usage)
    left the row blank with tokens_out NULL — the exact broken state observed
    live. Documents the regression the fix above prevents."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    await _finalize_message(factory, _assistant_msg.id, "", None, snapshot_id=None)
    async with factory() as s:
        row = await s.get(Message, _assistant_msg.id)
        assert row is not None
        assert row.content == ""
        assert row.tokens_out is None
