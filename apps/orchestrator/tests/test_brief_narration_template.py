"""Contract + drift guard for the brief-narration script shipped in the two
flagship Next.js container templates (ONE BRIEF, EVERY SURFACE).

Why these pins matter: the art-director brief is the single source of design
truth, but it used to reach ONLY the workspace chat — entity dashboards and the
public «/» were born silent. This script turns the brief (forwarded by the
workspace over `postMessage({type:'omnia:brief'})`, or baked onto
`window.__omniaBrief`) into a short "AI is designing" reveal on the surface
itself. A regression that drops the message listener, breaks the brief→lines
logic, or unwires the layout silently re-mutes every generated app. These are
deterministic file-content asserts (same house style as the inspector / remix
drift guards) — money-free, no container needed.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
_ENTITIES = _TEMPLATES / "nextjs-entities"
_DRIZZLE = _TEMPLATES / "nextjs-postgres-drizzle"
_SCRIPT_REL = "public/omnia-brief-narration.js"


def test_brief_narration_copies_stay_in_sync() -> None:
    """Both Next.js container templates ship a byte-identical script (R-04 DRY)."""
    canonical = (_ENTITIES / _SCRIPT_REL).read_bytes()
    copy = (_DRIZZLE / _SCRIPT_REL).read_bytes()
    assert copy == canonical, (
        "omnia-brief-narration.js drifted between nextjs-entities and "
        "nextjs-postgres-drizzle — keep the copies byte-identical "
        "(copy the nextjs-entities one over the drizzle one)."
    )


def test_brief_narration_contract() -> None:
    """The script enforces the load-bearing invariants of the lever."""
    src = (_ENTITIES / _SCRIPT_REL).read_text(encoding="utf-8")
    # 1) Consumes the brief from BOTH transports: postMessage + baked global.
    assert 'd.type !== "omnia:brief"' in src
    assert "window.__omniaBrief" in src
    # 2) Lines literally carry brief values (mirror of apps/web brief-narration.ts)
    #    — palette / font / sections / motion. If these were hard-coded the
    #    narration would not change with the brief (the falsifiable property).
    assert "Подбираю палитру" in src
    assert "Беру шрифт" in src
    assert "Компоную секции" in src
    assert "Оживляю движением" in src
    # 3) Same role order as the canonical narration (accent → primary → bg).
    assert "ACCENT" in src and "PRIMARY" in src and "BACKGROUND" in src
    # 4) Fail-soft: zero lines → render nothing (no empty overlay on a blank brief).
    assert "if (!lines.length) return" in src
    # Self-contained brand overlay, reduced-motion-safe, idempotent by signature.
    assert 'OVERLAY_ID = "omnia-brief-narration"' in src
    assert "prefers-reduced-motion:reduce" in src
    assert "data-omnia-sig" in src
    # No CDN: the script makes no network calls at all.
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis"):
        assert cdn not in src


def test_brief_narration_wired_into_both_layouts() -> None:
    """Each flagship layout loads the script (else the file ships dead)."""
    for tpl in (_ENTITIES, _DRIZZLE):
        layout = (tpl / "src/app/layout.tsx").read_text(encoding="utf-8")
        assert 'src="/omnia-brief-narration.js"' in layout, (
            f"{tpl.name}/src/app/layout.tsx must <Script src> the brief narration."
        )
