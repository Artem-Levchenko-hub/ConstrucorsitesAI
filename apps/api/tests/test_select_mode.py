"""Select-mode (preview element picker) — prompt injection, schema clamps,
static-serve injection, and the two-copy inspector drift guard."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from omnia_api.routers.public import _INSPECTOR_TAG, _inject_inspector
from omnia_api.schemas.message import PromptRequest, SelectedElement
from omnia_api.services.prompt_builder import build_messages


def _sel(**kw: object) -> dict:
    base = {
        "selector": ".btn",
        "label": "button.btn",
        "html": "<button>X</button>",
        "text": "X",
        "comment": "сделай красной",
    }
    base.update(kw)
    return base


# ── prompt injection ────────────────────────────────────────────────────────


def test_build_messages_injects_selection_block() -> None:
    msgs = build_messages(
        current_files={},
        history=[],
        user_prompt="поменяй кнопку",
        template="landing",
        selected_elements=[_sel()],
    )
    last = msgs[-1]
    assert last["role"] == "user"
    body = last["content"]
    assert "выделил элементы" in body  # ubiquitous-language framing
    assert ".btn" in body
    assert "<button>X</button>" in body
    assert "сделай красной" in body
    # the user's own text stays at the end, after the context block
    assert body.rstrip().endswith("поменяй кнопку")


def test_build_messages_without_selection_is_unchanged() -> None:
    assert build_messages({}, [], "просто промпт", "landing")[-1]["content"] == "просто промпт"
    # explicit empty list behaves the same (backward-compatible)
    assert build_messages({}, [], "просто промпт", "landing", [])[-1]["content"] == "просто промпт"


def test_build_messages_numbers_multiple_selections() -> None:
    body = build_messages(
        {},
        [],
        "правки",
        "landing",
        [_sel(selector=".a", comment="один"), _sel(selector=".b", comment="два")],
    )[-1]["content"]
    assert "1." in body and "2." in body
    assert ".a" in body and ".b" in body
    assert "один" in body and "два" in body


# ── schema clamps (R-10 fail-fast at the boundary) ───────────────────────────


def test_selected_element_clamps_lengths() -> None:
    with pytest.raises(ValidationError):
        SelectedElement(selector="x" * 601)
    with pytest.raises(ValidationError):
        SelectedElement(selector=".ok", html="h" * 2001)
    with pytest.raises(ValidationError):
        SelectedElement(selector=".ok", comment="c" * 1001)
    with pytest.raises(ValidationError):
        SelectedElement(selector="")  # selector required


def test_prompt_request_caps_selection_count() -> None:
    twelve = [SelectedElement(selector=f".s{i}") for i in range(12)]
    req = PromptRequest(prompt="p", model_id="m", selected_elements=twelve)
    assert len(req.selected_elements or []) == 12
    with pytest.raises(ValidationError):
        PromptRequest(
            prompt="p",
            model_id="m",
            selected_elements=[SelectedElement(selector=f".s{i}") for i in range(13)],
        )


def test_prompt_request_backward_compatible_without_field() -> None:
    assert PromptRequest(prompt="p", model_id="m").selected_elements is None


# ── static-serve injection ───────────────────────────────────────────────────


def test_inject_inspector_inserts_before_body_close() -> None:
    out = _inject_inspector(b"<html><body><h1>hi</h1></body></html>")
    assert _INSPECTOR_TAG in out
    assert out.index(_INSPECTOR_TAG) < out.index(b"</body>")
    assert b"<h1>hi</h1>" in out  # original content preserved


def test_inject_inspector_appends_when_no_body() -> None:
    out = _inject_inspector(b"<div>fragment</div>")
    assert out.startswith(b"<div>fragment</div>")
    assert _INSPECTOR_TAG in out


# ── two-copy drift guard (R-04 DRY of knowledge) ─────────────────────────────


def test_inspector_copies_stay_in_sync() -> None:
    repo = Path(__file__).resolve().parents[3]  # apps/api/tests/<file> -> repo root
    canonical = repo / "apps/api/src/omnia_api/static/omnia-inspector.js"
    copies = [
        repo / "apps/orchestrator/templates/nextjs-postgres-drizzle/public/omnia-inspector.js",
        repo / "apps/orchestrator/templates/nextjs-entities/public/omnia-inspector.js",
        # nextjs-realtime (messengers) shipped WITHOUT the inspector — select-mode
        # + the manual style editor were dead on every messenger until it was added.
        # Pin it here so a new template can't silently drop it again.
        repo / "apps/orchestrator/templates/nextjs-realtime/public/omnia-inspector.js",
    ]
    want = canonical.read_bytes()
    for copy in copies:
        assert copy.read_bytes() == want, (
            f"omnia-inspector.js drifted between apps/api and {copy.name}'s template "
            f"({copy.parent.parent.name}) — keep all copies byte-identical "
            "(copy apps/api/.../static/omnia-inspector.js over the template ones)."
        )


def test_inspector_reports_runtime_errors() -> None:
    """The canonical inspector must carry the always-on error reporter so a broken
    preview surfaces a chat card (the two template copies inherit it via the drift
    guard above)."""
    src = (
        Path(__file__).resolve().parents[3]
        / "apps/api/src/omnia_api/static/omnia-inspector.js"
    ).read_text(encoding="utf-8")
    assert 'post({ type: "omnia:preview:error"' in src
    assert 'addEventListener(\n    "error"' in src or 'addEventListener("error"' in src
    assert '"unhandledrejection"' in src
    # Gating contract: silent without a workspace parent (public /p/ stays clean).
    assert "window.parent === window" in src
