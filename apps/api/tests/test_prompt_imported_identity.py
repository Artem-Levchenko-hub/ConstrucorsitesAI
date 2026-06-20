"""B4 — pure unit tests for the generic edit identity used with imported repos.

These are 100 % offline (no DB, no network):
  * _build_edit_messages(is_imported=True) → generic identity block appears,
    no Omnia-specific text (index.html, omnia-kit, DOCTYPE).
  * _build_edit_messages(is_imported=False) → original identity unchanged.
  * build_messages(edit_mode=True, is_imported=True) → same as above (the
    dispatcher in build_messages routes to _build_edit_messages).
"""

from __future__ import annotations

import pytest

from omnia_api.services.prompt_builder import (
    _EDIT_IDENTITY_GENERIC,
    _build_edit_messages,
    build_messages,
)

_SAMPLE_FILES = {
    "src/main.py": "def hello():\n    print('hello')\n",
    "README.md": "# My project\n",
}

_HISTORY: list[dict[str, str]] = []


# ---------------------------------------------------------------------------
# _build_edit_messages with is_imported=True
# ---------------------------------------------------------------------------


def test_imported_edit_uses_generic_identity() -> None:
    msgs = _build_edit_messages(
        current_files=_SAMPLE_FILES,
        history=_HISTORY,
        user_prompt="Rename hello() to greet()",
        selected_elements=None,
        template="code",
        is_imported=True,
    )
    system = next(m["content"] for m in msgs if m["role"] == "system")
    # Generic identity text must appear (case-insensitive check)
    assert "произвольного стека" in system.lower()
    # Must NOT contain Omnia-BRAND assumptions (things from non-generic paths
    # that assume an Omnia-authored template). The generic identity itself
    # says "НЕ предполагай index.html / omnia-kit" as an instruction, so
    # we check for the omnia-kit CSS filename that only appears in kit-injection
    # blocks, and check that the Next.js-specific identity is absent.
    assert "omnia-kit.css" not in system
    assert "<!DOCTYPE" not in system
    # The generic identity must NOT contain the Omnia-specific NEXT identity
    assert "src/app/**/page.tsx" not in system
    # The Omnia static-HTML identity block must not appear
    assert "Ты — Omnia.AI в режиме ТОЧЕЧНОЙ ПРАВКИ. Пользователь уже собрал" not in system


def test_imported_edit_generic_identity_constant_present() -> None:
    """The _EDIT_IDENTITY_GENERIC string itself must contain the key phrase."""
    assert "произвольного стека" in _EDIT_IDENTITY_GENERIC.lower()


def test_imported_edit_contains_search_replace_format() -> None:
    """Even generic imports should use SEARCH/REPLACE format."""
    msgs = _build_edit_messages(
        current_files=_SAMPLE_FILES,
        history=_HISTORY,
        user_prompt="Change something",
        selected_elements=None,
        template="code",
        is_imported=True,
    )
    system = next(m["content"] for m in msgs if m["role"] == "system")
    assert "SEARCH" in system
    assert "REPLACE" in system


def test_imported_edit_includes_current_files_in_context() -> None:
    """The model must see the actual files so it can write byte-exact SEARCH."""
    msgs = _build_edit_messages(
        current_files=_SAMPLE_FILES,
        history=_HISTORY,
        user_prompt="Add a docstring",
        selected_elements=None,
        template="code",
        is_imported=True,
    )
    combined = " ".join(m["content"] for m in msgs)
    assert "src/main.py" in combined
    assert "def hello" in combined


# ---------------------------------------------------------------------------
# _build_edit_messages with is_imported=False (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_non_imported_edit_uses_omnia_identity() -> None:
    msgs = _build_edit_messages(
        current_files={"index.html": "<!DOCTYPE html><html></html>"},
        history=_HISTORY,
        user_prompt="Change the heading",
        selected_elements=None,
        template="blank",
        is_imported=False,
    )
    system = next(m["content"] for m in msgs if m["role"] == "system")
    # Should use the static Omnia identity (not generic)
    assert "ТОЧЕЧНОЙ ПРАВКИ" in system
    # MUST NOT contain the generic identity's distinctive opening phrase
    assert "внешнего репозитория" not in system


def test_non_imported_nextjs_edit_uses_next_identity() -> None:
    msgs = _build_edit_messages(
        current_files={"src/app/page.tsx": "export default function Page() { return <h1>hi</h1> }"},
        history=_HISTORY,
        user_prompt="Change h1 text",
        selected_elements=None,
        template="nextjs_entities",
        is_imported=False,
    )
    system = next(m["content"] for m in msgs if m["role"] == "system")
    assert "Next.js" in system or "next.js" in system.lower()
    # Generic identity phrase must not appear
    assert "внешнего репозитория" not in system


# ---------------------------------------------------------------------------
# build_messages dispatcher (edit_mode=True routes to _build_edit_messages)
# ---------------------------------------------------------------------------


def test_build_messages_imported_edit_mode() -> None:
    msgs = build_messages(
        current_files=_SAMPLE_FILES,
        history=_HISTORY,
        user_prompt="Fix a bug",
        template="code",
        edit_mode=True,
        is_imported=True,
    )
    system = next(m["content"] for m in msgs if m["role"] == "system")
    # Generic identity flows through build_messages (case-insensitive check)
    assert "произвольного стека" in system.lower()
    # Omnia-specific assumptions absent
    assert "omnia-kit.css" not in system
    assert "src/app/**/page.tsx" not in system
    assert "Ты — Omnia.AI в режиме ТОЧЕЧНОЙ ПРАВКИ. Пользователь уже собрал" not in system


def test_build_messages_non_imported_edit_mode_unchanged() -> None:
    msgs = build_messages(
        current_files={"index.html": "<!DOCTYPE html><html></html>"},
        history=_HISTORY,
        user_prompt="Change heading",
        template="blank",
        edit_mode=True,
        is_imported=False,
    )
    system = next(m["content"] for m in msgs if m["role"] == "system")
    # Original static identity unchanged
    assert "ТОЧЕЧНОЙ ПРАВКИ" in system
    # Generic identity phrase must not appear
    assert "внешнего репозитория" not in system
