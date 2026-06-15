"""Zero-signup "Remix this" CTA injection (V4.1b-UI).

The shipped fork backend (POST /api/projects/<id>/fork + anon-cookie seam) was
DEAD: /p/<slug> injected no fork affordance, so the viral loop was unreachable
from the UI. These pins lock the CTA injection that bridges it.
"""

from uuid import UUID

from omnia_api.routers.public import _inject_remix_cta

_PID = UUID("11111111-2222-3333-4444-555555555555")
_PAGE = b"<html><body><h1>hi</h1></body></html>"


def test_remix_cta_inserts_before_body_close() -> None:
    out = _inject_remix_cta(_PAGE, _PID)
    assert b'id="omnia-remix-cta"' in out
    assert out.index(b'id="omnia-remix-cta"') < out.index(b"</body>")
    assert b"<h1>hi</h1>" in out  # original content preserved


def test_remix_cta_targets_fork_endpoint_with_project_id() -> None:
    out = _inject_remix_cta(_PAGE, _PID)
    assert b"/api/projects/11111111-2222-3333-4444-555555555555/fork" in out
    # Lands the fresh fork straight in the workspace — the viral redirect edge.
    assert b'/projects/"+p.id' in out
    assert b'credentials:"include"' in out  # anon cookie must ride the POST


def test_remix_cta_idempotent() -> None:
    once = _inject_remix_cta(_PAGE, _PID)
    twice = _inject_remix_cta(once, _PID)
    assert once == twice
    assert twice.count(b'id="omnia-remix-cta"') == 1


def test_remix_cta_appends_when_no_body() -> None:
    out = _inject_remix_cta(b"<div>fragment</div>", _PID)
    assert out.startswith(b"<div>fragment</div>")
    assert b'id="omnia-remix-cta"' in out


def test_remix_cta_is_ascii_safe_bytes() -> None:
    # The template carries UTF-8 Cyrillic as raw bytes; the substituted id is
    # ASCII. The result must be valid UTF-8 (charset preserved, no mojibake).
    out = _inject_remix_cta(_PAGE, _PID)
    out.decode("utf-8")  # raises if the byte template is malformed
    assert "Сделать свою версию".encode() in out


def test_watermark_seed_rides_the_same_injection() -> None:
    """The viral watermark badge (#VIRAL-WATERMARK, pillar 4) is injected on the
    static share page alongside the remix pill: a "Сделано на Omnia.AI" credit
    that replays the design-birth reveal and offers a "make your own" CTA. One
    injection, so the same idempotency guard covers both affordances."""
    out = _inject_remix_cta(_PAGE, _PID)
    text = out.decode("utf-8")
    # Visible brand seed + the "make your own" CTA to the same-origin landing.
    assert 'id="omnia-wm"' in text
    assert "Сделано на " in text and "Omnia.AI" in text
    assert "Создать свой сайт" in text
    assert 'id="omnia-wm-make" href="/"' in text  # absolute → control-plane root
    # Wired to the narration replay hook (gracefully hidden when absent).
    assert "window.__omniaReplayBrief" in text
    # Inserted inside the document body, before the close tag, content preserved.
    assert text.index('id="omnia-wm"') < text.index("</body>")
