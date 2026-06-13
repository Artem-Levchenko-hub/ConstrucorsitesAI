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
