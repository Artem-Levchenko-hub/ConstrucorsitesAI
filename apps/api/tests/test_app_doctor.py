"""Tests for `services.app_doctor` — the web/container self-heal core.

`parse_fix` is the deterministic <edit>/<file> apply (browser-free, no I/O);
`propose_fix` adds the single model call. The probe + hot-reload loop lives in
`routers/messages.py` and is exercised separately.
"""

from __future__ import annotations

from omnia_api.services import app_doctor


def test_parse_fix_applies_edit() -> None:
    files = {"src/app/page.tsx": "const x = 1\nconst y = 2\n"}
    answer = (
        '<edit path="src/app/page.tsx">\n'
        "<<<<<<< SEARCH\nconst x = 1\n=======\nconst x = 42\n>>>>>>> REPLACE\n"
        "</edit>"
    )
    changed = app_doctor.parse_fix(answer, files)
    assert changed == {"src/app/page.tsx": "const x = 42\nconst y = 2\n"}


def test_parse_fix_full_file_block() -> None:
    files = {"src/app/page.tsx": "old"}
    answer = '<file path="src/app/page.tsx">export default function P(){return null}</file>'
    changed = app_doctor.parse_fix(answer, files)
    assert changed == {"src/app/page.tsx": "export default function P(){return null}"}


def test_parse_fix_noop_when_unchanged() -> None:
    # a <file> whose body equals the current content is not a change.
    files = {"a.tsx": "same"}
    answer = '<file path="a.tsx">same</file>'
    assert app_doctor.parse_fix(answer, files) == {}


def test_parse_fix_ignores_unmatched_search() -> None:
    # a SEARCH that doesn't match the file is dropped (no change), never an error.
    files = {"a.tsx": "const x = 1\n"}
    answer = (
        '<edit path="a.tsx">\n'
        "<<<<<<< SEARCH\nconst nope = 9\n=======\nconst nope = 0\n>>>>>>> REPLACE\n"
        "</edit>"
    )
    assert app_doctor.parse_fix(answer, files) == {}


def test_parse_fix_garbage_returns_empty() -> None:
    assert app_doctor.parse_fix("sorry, I can't help", {"a.tsx": "x"}) == {}


def _stub_model(monkeypatch, reply: str) -> None:
    async def _fake(messages, **kw):
        return reply

    monkeypatch.setattr(app_doctor, "complete_chat", _fake)


async def test_propose_fix_returns_changed(monkeypatch) -> None:
    files = {"src/app/page.tsx": "import { Bad } from 'x'\nconst a = 1\n"}
    _stub_model(
        monkeypatch,
        '<edit path="src/app/page.tsx">\n'
        "<<<<<<< SEARCH\nconst a = 1\n=======\nconst a = 2\n>>>>>>> REPLACE\n</edit>",
    )
    out = await app_doctor.propose_fix(
        category="compile",
        detail="Type error on line 2",
        file_path="src/app/page.tsx",
        files=files,
        model="test-model",
    )
    assert out == {"src/app/page.tsx": "import { Bad } from 'x'\nconst a = 2\n"}


async def test_propose_fix_none_on_empty_answer(monkeypatch) -> None:
    _stub_model(monkeypatch, "")
    out = await app_doctor.propose_fix(
        category="runtime", detail="boom", file_path="a.tsx",
        files={"a.tsx": "x"}, model="m",
    )
    assert out is None


async def test_propose_fix_none_on_noop(monkeypatch) -> None:
    # model echoes the file unchanged → no change → None (caller's loop stops).
    _stub_model(monkeypatch, '<file path="a.tsx">x</file>')
    out = await app_doctor.propose_fix(
        category="compile", detail="err", file_path="a.tsx",
        files={"a.tsx": "x"}, model="m",
    )
    assert out is None
