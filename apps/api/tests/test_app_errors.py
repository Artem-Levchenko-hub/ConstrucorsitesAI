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


# ── client-side JS error cards (signature + dedup) ───────────────────────────


def test_client_card_signature_basic() -> None:
    title, file = app_errors.client_card_signature(
        "TypeError: x is not a function",
        "https://app.dev/_next/static/chunks/page.js",
        42,
    )
    assert title == "TypeError: x is not a function"
    # Only the basename is kept (full chunk URL is noise), with the line.
    assert file == "page.js:42"


def test_client_card_signature_first_line_only_and_no_source() -> None:
    title, file = app_errors.client_card_signature(
        "Boom\n  at foo\n  at bar", "", 0
    )
    assert title == "Boom"
    assert file is None  # no source → no locator


def test_client_card_signature_strips_query_and_caps_title() -> None:
    title, file = app_errors.client_card_signature(
        "E" * 300, "/a/b/main.js?v=123", 7
    )
    assert file == "main.js:7"
    assert len(title) <= 140


def test_render_block_supports_client_category() -> None:
    block = app_errors.render_block(
        category="client",
        title=None,
        detail="ReferenceError",
        file="page.js:1",
        fixable=True,
    )
    assert 'category="client"' in block
    assert 'title="Ошибка в браузере"' in block  # default title for client


def test_has_client_card_dedups_same_error() -> None:
    title, file = app_errors.client_card_signature("Boom", "page.js", 3)
    block = app_errors.render_block(
        category="client", title=title, detail="Boom", file=file, fixable=True
    )
    content = "ответ ассистента" + block
    assert app_errors.has_client_card(content, title, file) is True
    # A different error (other line) is NOT considered a duplicate.
    t2, f2 = app_errors.client_card_signature("Boom", "page.js", 99)
    assert app_errors.has_client_card(content, t2, f2) is False


def test_has_client_card_ignores_other_categories() -> None:
    compile_block = app_errors.render_block(
        category="compile", title="Boom", detail="Boom", file="page.js:3", fixable=True
    )
    # Same title/file but a compile card — must not suppress a real client card.
    assert app_errors.has_client_card(compile_block, "Boom", "page.js:3") is False


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


def test_client_card_detail_plain_message_only() -> None:
    # No stack, no context → just the message (back-compat with old reports).
    assert app_errors.client_card_detail("boom", "", "", []) == "boom"


def test_client_card_detail_includes_stack_route_and_steps() -> None:
    detail = app_errors.client_card_detail(
        "Cannot read 'x' of undefined",
        "at f (page.tsx:10)",
        "/dashboard",
        ["клик: button «Удалить»", "ввод: input «Email»"],
    )
    assert "Cannot read 'x' of undefined" in detail
    assert "at f (page.tsx:10)" in detail
    assert "Страница: /dashboard" in detail
    assert "Шаги до ошибки:" in detail
    assert "• клик: button «Удалить»" in detail
    assert "• ввод: input «Email»" in detail


def test_client_card_detail_clamps_and_skips_blank_crumbs() -> None:
    detail = app_errors.client_card_detail(
        "err", "", "/x", ["  ", "a" * 500]
    )
    assert "Страница: /x" in detail
    # Blank crumb dropped; the long one clamped to 120 chars.
    lines = [ln for ln in detail.splitlines() if ln.startswith("• ")]
    assert len(lines) == 1
    assert len(lines[0]) - len("• ") == 120
