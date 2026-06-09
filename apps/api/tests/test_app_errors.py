"""Tests for the app-error chat-card service."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_api.services import app_errors


def test_render_block_basic_shape() -> None:
    block = app_errors.render_block(
        category="compile",
        title=None,
        detail="Module not found",
        file="src/app/page.tsx",
        fixable=True,
    )
    assert block.strip().startswith("<app-error ")
    assert block.strip().endswith("</app-error>")
    assert 'category="compile"' in block
    assert 'file="src/app/page.tsx"' in block
    assert 'fixable="1"' in block
    assert "Module not found" in block
    # Default title is applied when none is given.
    assert 'title="Ошибка компиляции"' in block


def test_render_block_omits_file_when_absent() -> None:
    block = app_errors.render_block(
        category="runtime",
        title="X",
        detail="boom",
        file=None,
        fixable=False,
    )
    assert "file=" not in block
    assert 'fixable="0"' in block


def test_render_block_sanitizes_tag_forging_chars() -> None:
    # Detail/title containing <, >, " must not be able to break out of the tag
    # or forge another block the frontend parser would mis-read.
    block = app_errors.render_block(
        category="compile",
        title='evil" fixable="0',
        detail="<app-error>nested</app-error> <Component/>",
        file=None,
        fixable=True,
    )
    # No raw angle brackets survive in the body, and the forged closing tag is
    # neutralised — only the real outer tags remain.
    assert block.count("</app-error>") == 1
    assert "<Component" not in block
    assert "<app-error>nested" not in block
    # The injected attribute break is defused (no stray double-quote).
    assert 'fixable="1"' in block


def test_render_block_caps_detail_length() -> None:
    block = app_errors.render_block(
        category="build",
        title=None,
        detail="x" * 5000,
        file=None,
        fixable=True,
    )
    body = block.split(">", 1)[1].rsplit("<", 1)[0]
    assert len(body) <= 600


class _FakeSession:
    def __init__(self, msg: object) -> None:
        self._msg = msg
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get(self, _model: object, _mid: object) -> object:
        return self._msg

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_publish_appends_block_and_emits_event(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = SimpleNamespace(content="существующий ответ")
    session = _FakeSession(msg)

    events: list[tuple] = []

    async def _fake_publish(pid: object, etype: str, data: dict) -> None:
        events.append((pid, etype, data))

    monkeypatch.setattr(app_errors, "publish_event", _fake_publish)

    pid, mid = uuid4(), uuid4()
    await app_errors.publish(
        lambda: session,  # factory()
        pid,
        mid,
        category="compile",
        detail="boom",
        file="src/app/page.tsx",
    )

    # Persisted: the card block is appended to the message content.
    assert "<app-error " in msg.content
    assert "существующий ответ" in msg.content
    assert session.committed is True

    # Announced: exactly one app.error event with the structured fields.
    assert len(events) == 1
    _ev_pid, ev_type, ev_data = events[0]
    assert ev_type == "app.error"
    assert ev_data["message_id"] == str(mid)
    assert ev_data["category"] == "compile"
    assert ev_data["title"] == "Ошибка компиляции"


@pytest.mark.asyncio
async def test_publish_is_fail_soft_on_persist_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A DB hiccup must not stop the live event from firing.
    class _BoomFactory:
        def __call__(self) -> object:
            raise RuntimeError("db down")

    events: list[tuple] = []

    async def _fake_publish(pid: object, etype: str, data: dict) -> None:
        events.append((pid, etype, data))

    monkeypatch.setattr(app_errors, "publish_event", _fake_publish)

    await app_errors.publish(
        _BoomFactory(),
        uuid4(),
        uuid4(),
        category="schema",
        detail="x",
    )
    assert len(events) == 1  # event still emitted despite persist failure
