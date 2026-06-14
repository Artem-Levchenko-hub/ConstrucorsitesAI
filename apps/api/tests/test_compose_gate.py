"""Compose gate — the money-free composition floor (V3.3).

These pin the contract of the cheap, deterministic source-scan that runs BEFORE
any render or vision pass: a freeform ``index.html`` that is catastrophically
flat (one type size, no section rhythm, no hero) fails; a richly-composed page
passes; and a file set with no standalone HTML page (an entity/fullstack stack,
whose composition lives in the rendered DOM, not the source) is INERT — judged
nothing, passes. The floors are catastrophe-only by design: a real enterprise
generation can never trip them, so the gate adds teeth without a false-positive
on the live hot path.
"""

from omnia_api.services import accept_gauntlet, compose_gate

# A richly-composed freeform landing: a real type scale (h1/h2 + CSS sizes), four
# section landmarks (header + 2× section + footer) and an unmistakable hero.
_RICH_HTML = (
    "<!doctype html><html lang='ru'><head><style>"
    "h1{font-size:3rem} h2{font-size:1.5rem} .lead{font-size:1.25rem} p{font-size:1rem}"
    "</style></head><body>"
    "<header class='hero'><h1>Студия дизайна</h1>"
    "<p class='lead'>Создаём бренды, которые помнят</p>"
    "<a href='/signin'>Войти</a></header>"
    "<section class='features'><h2>Услуги</h2><p>Брендинг, веб, упаковка</p></section>"
    "<section class='pricing'><h2>Тарифы</h2><p>От 50 000 ₽</p></section>"
    "<footer><p>© 2026 Студия</p></footer>"
    "</body></html>"
)

# Catastrophic flatness: one uniform font, no headings, no landmarks, no hero.
_FLAT_HTML = (
    "<!doctype html><html lang='ru'><body><div>"
    "<p style='font-size:16px'>Просто текст без всякой структуры.</p>"
    "<p style='font-size:16px'>Ещё один абзац того же размера.</p>"
    "</div></body></html>"
)


def test_rich_freeform_page_passes_the_floor():
    rep = compose_gate.scan({"index.html": _RICH_HTML})
    assert rep.judged is True
    assert rep.passed is True
    assert rep.classes == ()
    assert rep.font_sizes >= 2
    assert rep.sections >= 3
    assert rep.hero is True


def test_flat_page_fails_all_three_classes():
    rep = compose_gate.scan({"index.html": _FLAT_HTML})
    assert rep.judged is True
    assert rep.passed is False
    assert set(rep.classes) == {
        compose_gate.FLAT_TYPE,
        compose_gate.TOO_FEW_SECTIONS,
        compose_gate.NO_HERO,
    }


def test_single_font_only_fires_flat_type():
    # Hero + 3 sections via landmarks/markers, but exactly one type signal (h1).
    html = (
        "<body>"
        "<header class='hero'><h1>Заголовок</h1></header>"
        "<section class='features'><p>текст</p></section>"
        "<section class='pricing'><p>текст</p></section>"
        "<footer><p>низ</p></footer>"
        "</body>"
    )
    rep = compose_gate.scan({"index.html": html})
    assert rep.classes == (compose_gate.FLAT_TYPE,)
    assert rep.passed is False


def test_too_few_sections_only_fires_its_class():
    # Hero + a real type scale, but a single block → no section rhythm.
    html = (
        "<body><div class='hero'>"
        "<h1 style='font-size:3rem'>Заголовок</h1>"
        "<p style='font-size:1rem'>текст</p>"
        "</div></body>"
    )
    rep = compose_gate.scan({"index.html": html})
    assert rep.classes == (compose_gate.TOO_FEW_SECTIONS,)
    assert rep.passed is False


def test_no_hero_only_fires_its_class():
    # A real type scale and ≥3 section landmarks, but no h1 / header / hero marker.
    html = (
        "<body>"
        "<section><h2 style='font-size:2rem'>Раздел 1</h2>"
        "<p style='font-size:1rem'>текст</p></section>"
        "<section><h2>Раздел 2</h2></section>"
        "<section><h2>Раздел 3</h2></section>"
        "</body>"
    )
    rep = compose_gate.scan({"index.html": html})
    assert rep.classes == (compose_gate.NO_HERO,)
    assert rep.passed is False


def test_no_standalone_html_is_inert():
    # An entity/fullstack stack has no top-level index.html — its composition is
    # judged by the rendered taste/hierarchy legs, so the source-scan abstains.
    rep = compose_gate.scan(
        {"app/page.tsx": "export default function Page(){return <main>hi</main>}"}
    )
    assert rep.judged is False
    assert rep.passed is True
    assert rep.classes == ()


def test_index_html_found_case_insensitively_in_subdir():
    rep = compose_gate.scan({"public/Index.HTML": _RICH_HTML})
    assert rep.judged is True
    assert rep.passed is True


def test_unparseable_body_is_fail_soft():
    # A pathological non-string never raises through the scan (R-10).
    rep = compose_gate.scan({"index.html": None})  # type: ignore[dict-item]
    assert rep.judged is False
    assert rep.passed is True


def test_summary_and_subscore_are_stable():
    rep = compose_gate.scan({"index.html": _FLAT_HTML})
    sub = rep.subscore()
    assert sub["gate"] == "compose"
    assert sub["passed"] is False
    assert sub["judged"] is True
    assert set(sub["classes"]) == set(rep.classes)
    assert "compose" in rep.summary().lower()


# ── wiring: the gauntlet fans compose as a dial (never auto-on) ───────────────


async def test_run_with_compose_hard_fails_on_flat_page():
    v = await accept_gauntlet.run(
        files={"index.html": _FLAT_HTML}, compose=True, include_rendered=False
    )
    hard = {g.gate for g in v.hard_failed}
    assert accept_gauntlet.COMPOSE in hard
    assert "compose:flat-type" in v.failed_classes


async def test_run_with_compose_passes_rich_page():
    v = await accept_gauntlet.run(
        files={"index.html": _RICH_HTML}, compose=True, include_rendered=False
    )
    gates = {g.gate for g in v.gates}
    assert accept_gauntlet.COMPOSE in gates
    assert v.hard_failed == ()
    assert v.passed is True


async def test_compose_off_by_default_does_not_append_leg():
    # The dial defaults OFF in run() so existing gate unit tests (which never pass
    # compose=) are byte-identical — the hot path turns it on explicitly.
    v = await accept_gauntlet.run(
        files={"index.html": _FLAT_HTML}, include_rendered=False
    )
    assert accept_gauntlet.COMPOSE not in {g.gate for g in v.gates}
