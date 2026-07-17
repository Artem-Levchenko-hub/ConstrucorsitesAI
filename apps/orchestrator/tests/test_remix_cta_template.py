"""Contract + drift guard for the viral "Remix this" CTA shipped in the two
flagship Next.js container templates (V4.1b-UI-fullstack render leg).

Why these pins matter: the CTA is the ONLY surface that closes pillar 4 (viral
shareability) for the 59 container-backed apps — they 302-redirect /p/<slug> to a
different-origin live container, so the static same-origin CTA can't reach them.
A regression that drops the top-level gate, breaks the slug/origin derivation, or
unwires the layout silently kills the loop for the most valuable apps. These are
deterministic file-content asserts (same house style as the inspector drift
guard) — money-free, no container needed.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
_ENTITIES = _TEMPLATES / "nextjs-entities"
_DRIZZLE = _TEMPLATES / "nextjs-postgres-drizzle"
# nextjs-realtime (messengers) originally shipped without any omnia public
# scripts → select-mode / manual editor / remix CTA were dead. Pinned here too.
_REALTIME = _TEMPLATES / "nextjs-realtime"
_CTA_REL = "public/omnia-remix-cta.js"


def test_remix_cta_copies_stay_in_sync() -> None:
    """Every Next.js container template ships a byte-identical CTA (R-04 DRY)."""
    canonical = (_ENTITIES / _CTA_REL).read_bytes()
    for tpl in (_DRIZZLE, _REALTIME):
        assert (tpl / _CTA_REL).read_bytes() == canonical, (
            f"omnia-remix-cta.js drifted between nextjs-entities and {tpl.name} — "
            "keep the copies byte-identical (copy the nextjs-entities one over it)."
        )


def test_remix_cta_contract() -> None:
    """The CTA enforces the four load-bearing invariants of the render leg."""
    src = (_ENTITIES / _CTA_REL).read_text(encoding="utf-8")
    # 1) Top-level-only: hidden inside the owner-workspace iframe.
    assert "window.self === window.top" in src
    # 2) Cross-origin entry is the GET /p/<slug>/remix endpoint (NOT the
    #    same-origin POST fork the static page uses — that can't carry the cookie
    #    cross-origin).
    assert '"/p/" + encodeURIComponent(slug) + "/remix"' in src
    # Plain top-level <a href> navigation (NOT a same-origin fetch/POST, which
    # can't carry the session cookie cross-origin): the entry is an anchor whose
    # href is the derived remix URL.
    assert "createElement(\"a\")" in src
    assert "a.href = href" in src
    # 3) Slug derived from the container host, dev-preview "-dev" stripped.
    assert "location.hostname" in src
    assert "/-dev$/" in src
    # 4) API origin derived from a real preview host or an explicit override;
    #    never a broken link on localhost/sslip dev.
    assert "__omniaApiOrigin" in src
    assert 'indexOf("preview")' in src
    # Self-contained brand pill, no CDN, reduced-motion-safe, idempotent.
    assert 'id="omnia-remix-cta"' in src or 'wrap.id = "omnia-remix-cta"' in src
    assert "prefers-reduced-motion:reduce" in src
    assert "Сделать свою версию" in src
    # No CDN: the only network destination is the derived control-plane origin.
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis"):
        assert cdn not in src


def test_remix_cta_wired_into_both_layouts() -> None:
    """Each flagship layout loads the CTA script (else the file ships dead)."""
    for tpl in (_ENTITIES, _DRIZZLE, _REALTIME):
        layout = (tpl / "src/app/layout.tsx").read_text(encoding="utf-8")
        assert 'src="/omnia-remix-cta.js"' in layout, (
            f"{tpl.name}/src/app/layout.tsx must <Script src> the remix CTA."
        )
        assert 'src="/omnia-inspector.js"' in layout, (
            f"{tpl.name}/src/app/layout.tsx must <Script src> the select-mode "
            "inspector (powers «Править с ИИ» + the manual style editor)."
        )


def test_watermark_seed_contract() -> None:
    """The viral watermark badge (#VIRAL-WATERMARK, pillar 4) rides the same
    container surface as the remix pill — a "Сделано на Omnia.AI" credit that, on
    click, replays the design-birth reveal and offers a "make your own" CTA. It
    must keep the same top-level + origin gates (no broken link on dev hosts),
    its brand copy, and its wiring to the narration replay hook."""
    src = (_ENTITIES / _CTA_REL).read_text(encoding="utf-8")
    # Mounted from the same module, after the pill, so one load-bearing script
    # owns every viral corner affordance.
    assert "function mountWatermark()" in src
    assert "mountWatermark();" in src
    # Visible brand seed copy + the "make your own" CTA.
    assert "Сделано на " in src and "Omnia.AI" in src
    assert "Создать свой сайт" in src
    # Same gates as the pill: top-level visitor + derivable control-plane origin
    # (the "make your own" link is the landing on that origin).
    assert "isTopLevel()" in src
    assert "var homeHref = origin" in src
    # On-demand replay of the live "design being born" reveal, only when the
    # narration module exposed its hook (graceful when absent).
    assert "window.__omniaReplayBrief" in src
    # Self-contained, reduced-motion-safe, no CDN.
    assert 'wrap.id = "omnia-wm"' in src or 'id="omnia-wm"' in src
    assert "prefers-reduced-motion:reduce" in src


def test_narration_exposes_replay_hook() -> None:
    """Both container narration copies expose the replay hook the watermark calls
    (keeps the badge's "посмотреть, как он родился" button alive)."""
    rel = "public/omnia-brief-narration.js"
    for tpl in (_ENTITIES, _DRIZZLE, _REALTIME):
        src = (tpl / rel).read_text(encoding="utf-8")
        assert "window.__omniaReplayBrief = function" in src, (
            f"{tpl.name}/{rel} must expose window.__omniaReplayBrief."
        )
