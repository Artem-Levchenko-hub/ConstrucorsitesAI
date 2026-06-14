"""V3.12 — the render defect-registry ratchet (pillar 3).

Every render class carries an adversary fixture that MUST fail its assert (like the
beauty gates 5/5 · 7/5, the viral registry V4.6 and the onboarding registry V2.7),
proving the gate has teeth and is not a vacuous green. A rich brief passes, a
no-brief / empty context is INERT, the narratable-payload verdict is single-sourced
on ``art_director_writer.parse_brief`` (R-04), and the registry is wired into the
acceptance gauntlet through the ``render=`` dial (it is not an orphan). Pure
Python — no browser, no LLM, no DB.
"""

from __future__ import annotations

from omnia_api.services import accept_gauntlet, render_registry
from omnia_api.services.render_registry import RenderContext, scan

# ── shared building blocks ──────────────────────────────────────────────────────

#: A regression-free art-director brief: labelled palette, fonts, a motion
#: signature and a section list — everything the live render narrates from.
_RICH_BRIEF = """\
# 2. ГЛОБАЛ
ФОН #0A0A0A · ТЕКСТ #FAFAFA · PRIMARY #1A1A1A · АКЦЕНТ #C8A04F
ШРИФТЫ: дисплей "Clash Display" · текст "Inter"
MOTION-СИГНАТУРА: .omnia-shader data-omnia-colors="#0A0A0A,#1A1A1A,#2A2A2A" (reduced-motion-safe)
# 3. СЕКЦИИ
[1] Герой | id="hero"
[2] Услуги | id="services"
"""


def _ctx(brief: str | None = _RICH_BRIEF) -> RenderContext:
    return RenderContext(brief=brief)


# ── the clean / inert baselines ─────────────────────────────────────────────────


def test_rich_brief_passes() -> None:
    rep = scan(_ctx())
    assert rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_none_context_is_inert() -> None:
    rep = scan(None)
    assert not rep.judged
    assert rep.passed  # INERT — nothing to narrate ≠ a failure
    assert rep.classes == ()


def test_empty_context_is_inert() -> None:
    rep = scan(RenderContext())
    assert not rep.judged
    assert rep.passed
    assert rep.classes == ()


def test_empty_brief_is_inert() -> None:
    rep = scan(_ctx(brief=""))
    assert not rep.judged
    assert rep.passed


def test_whitespace_brief_is_inert() -> None:
    rep = scan(_ctx(brief="   \n\t  "))
    assert not rep.judged
    assert rep.passed


# ── one adversary fixture PER class — each MUST fail its assert ──────────────────


def test_adversary_silent_render() -> None:
    # A brief that carries prose but no extractable design tokens → parse_brief
    # surfaces an empty husk → the omnia:brief event narrates nothing.
    rep = scan(_ctx(brief="просто описание без палитры, шрифтов и секций"))
    assert not rep.passed
    assert render_registry.SILENT_RENDER in rep.classes
    # the silent class subsumes the others — only it fires on a fully barren brief
    assert rep.classes == (render_registry.SILENT_RENDER,)


def test_adversary_swatchless_render() -> None:
    # Fonts + a section survive but NO palette HEX → the colour story drops, so
    # the V3.4 swatches render blank while the rest of the narration holds.
    brief = """\
ШРИФТ: "Inter"
MOTION-СИГНАТУРА: .line-rise (reduced-motion-safe)
[1] Герой | id="hero"
"""
    rep = scan(_ctx(brief=brief))
    assert not rep.passed
    assert render_registry.SWATCHLESS_RENDER in rep.classes
    assert render_registry.SILENT_RENDER not in rep.classes  # other signal exists


def test_adversary_motionless_render() -> None:
    # Palette + sections survive but NO MOTION-СИГНАТУРА → the hypnosis layer the
    # narration names while it paints is gone.
    brief = """\
ФОН #0A0A0A · ТЕКСТ #FAFAFA · PRIMARY #1A1A1A · АКЦЕНТ #C8A04F
[1] Герой | id="hero"
"""
    rep = scan(_ctx(brief=brief))
    assert not rep.passed
    assert render_registry.MOTIONLESS_RENDER in rep.classes
    assert render_registry.SILENT_RENDER not in rep.classes


# ── single-source contract: verdict tracks parse_brief (R-04) ───────────────────


def test_verdict_tracks_parse_brief() -> None:
    from omnia_api.services.art_director_writer import parse_brief

    # Whatever parse_brief surfaces a palette for must read swatchful, and whatever
    # it leaves palette-empty (but otherwise signalled) must read SWATCHLESS — the
    # rubric never drifts from the boundary the client actually receives.
    assert parse_brief(_RICH_BRIEF)["palette"]  # type: ignore[index]
    assert render_registry.SWATCHLESS_RENDER not in scan(_ctx()).classes

    palette_less = 'ШРИФТ: "Inter"\nMOTION-СИГНАТУРА: .line-rise\n[1] X | id="x"'
    assert not parse_brief(palette_less)["palette"]  # type: ignore[index]
    assert render_registry.SWATCHLESS_RENDER in scan(_ctx(brief=palette_less)).classes


# ── multiple classes + invariants ───────────────────────────────────────────────


def test_swatchless_and_motionless_compound() -> None:
    # Only sections survive → both swatchless AND motionless fire, in canonical
    # order, but NOT silent (there is still narratable structure).
    brief = '[1] Герой | id="hero"\n[2] Услуги | id="services"'
    rep = scan(_ctx(brief=brief))
    assert rep.classes == (
        render_registry.SWATCHLESS_RENDER,
        render_registry.MOTIONLESS_RENDER,
    )


def test_subscore_shape() -> None:
    rep = scan(_ctx(brief="просто текст без токенов"))
    sub = rep.subscore()
    assert sub["gate"] == "render"
    assert sub["passed"] is False
    assert sub["judged"] is True
    assert render_registry.SILENT_RENDER in sub["classes"]
    assert sub["count"] >= 1


def test_inert_subscore_shape() -> None:
    sub = scan(None).subscore()
    assert sub["gate"] == "render"
    assert sub["passed"] is True
    assert sub["judged"] is False
    assert sub["classes"] == []


# ── the registry is WIRED into the gauntlet (not an orphan) ──────────────────────


async def test_gauntlet_render_dial_off_by_default() -> None:
    verdict = await accept_gauntlet.run(render_context=_ctx())
    assert accept_gauntlet.RENDER not in [g.gate for g in verdict.gates]


async def test_gauntlet_render_dial_passes_rich_brief() -> None:
    verdict = await accept_gauntlet.run(render=True, render_context=_ctx())
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.RENDER)
    assert leg.passed
    assert not leg.abstained


async def test_gauntlet_render_dial_hard_fails_on_silent() -> None:
    ctx = _ctx(brief="голый текст без дизайн-токенов")
    verdict = await accept_gauntlet.run(render=True, render_context=ctx)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.RENDER)
    assert not leg.passed
    assert leg in verdict.hard_failed  # a real finding blocks ship, not an abstain
    assert f"{accept_gauntlet.RENDER}:{render_registry.SILENT_RENDER}" in (
        verdict.failed_classes
    )


async def test_gauntlet_render_dial_inert_on_empty_context() -> None:
    verdict = await accept_gauntlet.run(render=True)
    leg = next(g for g in verdict.gates if g.gate == accept_gauntlet.RENDER)
    assert leg.passed
    assert not leg.abstained
    assert leg not in verdict.hard_failed
