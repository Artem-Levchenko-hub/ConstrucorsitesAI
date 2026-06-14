"""Acceptance gauntlet — the aggregator that fans every landed gate (V1.6 0/5).

These prove the keystone contract: (1) a clean app passes / exits 0; (2) a
planted past defect makes the gauntlet FAIL with the exact class AND makes
`acceptance.evaluate` reject — i.e. the gate is wired, not orphaned; (3) abstain
(a render that produced no evidence) fails the STRICT verdict but is NOT a hard
failure for the hot path; (4) the rendered gate modules each have a non-test
importer (this module's subject, `accept_gauntlet`).
"""

from pathlib import Path

from omnia_api.services import accept_gauntlet
from omnia_api.services.catalog_coherence_gate import (
    CHECKS,
    CatalogFinding,
    CatalogReport,
)
from omnia_api.services.chip_pixel_gate import FidelityReport
from omnia_api.services.data_gate import DataFinding, DataReport
from omnia_api.services.hierarchy_gate import HierarchyReport
from omnia_api.services.perf_a11y_gate import PerfA11yFinding, PerfA11yReport
from omnia_api.services.reference_corpus import (
    RICHNESS_AXES,
    CorpusComparison,
    ReferenceReport,
)
from omnia_api.services.taste_gate import TYPE_SCALE, TasteFinding, TasteReport
from omnia_api.services.wow_dom_gate import WowDomFinding, WowDomReport

# A freeform page carrying a dead auth CTA — the dead-auth-link defect class the
# registry catches in `.html` as well as `.tsx` (mirror of the shipped fix).
_DEAD_AUTH_HTML = (
    "<!doctype html><html lang='ru'><head><title>T</title></head><body>"
    "<h1>Заголовок</h1>"
    "<a href='/'>Войти</a>"
    "</body></html>"
)
_CLEAN_HTML = (
    "<!doctype html><html lang='ru'><head><title>T</title></head><body>"
    "<h1>Заголовок</h1>"
    "<a href='/signin'>Войти</a>"
    "</body></html>"
)


def _wow(findings=(), *, rendered=True):
    return WowDomReport(tuple(findings), 390, 390, ("#7c3aed",), rendered=rendered)


def _perf(findings=(), *, rendered=True):
    return PerfA11yReport(tuple(findings), {}, 100, 0, rendered=rendered)


def _chip(findings=(), *, rendered=True, checked=()):
    return FidelityReport(tuple(findings), rendered=rendered, checked=tuple(checked))


def _taste(*, score=5, findings=(), rendered=True):
    return TasteReport(tuple(findings), score, 1440, ("inter", "playfair"), rendered=rendered)


def _hier(*, score=3, findings=(), rendered=True):
    return HierarchyReport(tuple(findings), score, 1440, rendered=rendered)


def _data(*, collections=1, findings=(), rendered=True):
    return DataReport(tuple(findings), collections, rendered=rendered)


def _ref(*, passed=True, rendered=True):
    """A reference-corpus report (V1.13b). ``passed`` → candidate meets-or-beats;
    not passed → it holds only one axis (below corpus); ``rendered=False`` →
    abstain (a page / the corpus did not render)."""
    full = {a: True for a in RICHNESS_AXES}
    cand = full if passed else {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES}
    met = tuple(a for a in RICHNESS_AXES if bool(cand[a]) >= bool(full[a]))
    comp = CorpusComparison(
        niche="saas",
        candidate=cand,
        reference=full,
        met=met,
        rendered=rendered,
        min_axes=4,
    )
    return ReferenceReport((comp,))


def _cat(*, score=5, findings=(), rendered=True, surface="catalog", rows=6):
    """A catalog-realism report (V1.17). ``score`` is the 0–5 realism axes clean;
    ``findings`` are the fired axes; ``rendered=False`` → abstain; ``surface='none'``
    → waived (no catalog grid on the page)."""
    return CatalogReport(
        tuple(findings), score, 1440, rows, rendered=rendered, surface=surface
    )


# ── 1. deterministic leg, no render ──────────────────────────────────────────


async def test_clean_registry_only_passes():
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML}, include_rendered=False
    )
    assert [g.gate for g in v.gates] == [accept_gauntlet.DEFECT_REGISTRY]
    assert v.passed is True
    assert v.hard_failed == ()
    assert v.failed_classes == ()


async def test_planted_dead_auth_link_fails_with_exact_class():
    v = await accept_gauntlet.run(
        files={"index.html": _DEAD_AUTH_HTML}, include_rendered=False
    )
    assert v.passed is False
    # exact class surfaces, gate-prefixed and raw
    assert "dead-auth-link" in v.gates[0].classes
    assert "defect-registry:dead-auth-link" in v.failed_classes
    # a deterministic registry hit is a HARD failure (blocks the hot path)
    assert any(g.gate == accept_gauntlet.DEFECT_REGISTRY for g in v.hard_failed)


async def test_empty_inputs_do_not_pass():
    # nothing ran → no evidence → not a ship
    v = await accept_gauntlet.run(files=None, include_rendered=False)
    assert v.gates == ()
    assert v.passed is False


# ── 2. rendered legs (stubbed — no real chromium) ─────────────────────────────


def _stub_rendered(monkeypatch, *, wow, perf, chip, taste=None, hier=None, data=None, ref=None):
    taste = taste if taste is not None else _taste()
    hier = hier if hier is not None else _hier()
    data = data if data is not None else _data()
    ref = ref if ref is not None else _ref()

    async def _w(files, **kw):
        return wow

    async def _p(files, **kw):
        return perf

    async def _c(files, spec, **kw):
        return chip

    async def _t(files, **kw):
        return taste

    async def _h(files, **kw):
        return hier

    async def _d(files, **kw):
        return data

    async def _r(files, **kw):
        return ref

    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _w)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _p)
    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _c)
    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _d)
    monkeypatch.setattr(accept_gauntlet.reference_corpus, "audit_files", _r)


async def test_all_gates_clean_passes(monkeypatch):
    _stub_rendered(monkeypatch, wow=_wow(), perf=_perf(), chip=_chip(checked=("palette-bg",)))
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert [g.gate for g in v.gates] == [
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.WOW_DOM,
        accept_gauntlet.PERF_A11Y,
        accept_gauntlet.CHIP_PIXEL,
        accept_gauntlet.TASTE,
        accept_gauntlet.HIERARCHY,
        accept_gauntlet.DATA,
        accept_gauntlet.REFERENCE,
    ]
    assert v.render_expected is True
    assert v.passed is True


async def test_rendered_finding_is_hard_failure(monkeypatch):
    _stub_rendered(
        monkeypatch,
        wow=_wow([WowDomFinding("h-scroll", "900px@390px")]),
        perf=_perf(),
        chip=_chip(),
    )
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert v.passed is False
    hard = {g.gate for g in v.hard_failed}
    assert accept_gauntlet.WOW_DOM in hard
    assert "wow-dom:h-scroll" in v.failed_classes


async def test_abstain_fails_strict_but_not_hard(monkeypatch):
    # a render that produced no evidence: strict verdict fails (CLI exit 1), but
    # it is NOT a hard failure — a flake must not sink the hot path.
    _stub_rendered(
        monkeypatch,
        wow=_wow(rendered=False),
        perf=_perf(rendered=False),
        chip=_chip(rendered=False),
        taste=_taste(rendered=False),
        hier=_hier(rendered=False),
        data=_data(rendered=False),
        ref=_ref(rendered=False),
    )
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert v.passed is False  # strict: abstain ≠ pass
    assert v.hard_failed == ()  # but not a hard finding
    # include_rendered fans the BLOCKING rendered legs only — the advisory catalog
    # leg (V1.17) stays off unless its own dial is set, so it is not in abstained.
    assert {g.gate for g in v.abstained} == (
        set(accept_gauntlet.RENDERED_GATES) - accept_gauntlet.ADVISORY_GATES
    )


async def test_perf_finding_surfaces(monkeypatch):
    _stub_rendered(
        monkeypatch,
        wow=_wow(),
        perf=_perf([PerfA11yFinding("a11y-violation", "image-alt")]),
        chip=_chip(),
    )
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert v.passed is False
    assert "perf-a11y:a11y-violation" in v.failed_classes


async def test_taste_below_floor_is_a_hard_failure(monkeypatch):
    from omnia_api.services.taste_gate import TasteFinding

    _stub_rendered(
        monkeypatch,
        wow=_wow(),
        perf=_perf(),
        chip=_chip(checked=("palette-bg",)),
        taste=_taste(score=2, findings=(TasteFinding("hero-imagery", "solid plate"),)),
    )
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert v.passed is False
    assert accept_gauntlet.TASTE in {g.gate for g in v.hard_failed}
    assert "taste:hero-imagery" in v.failed_classes


async def test_empty_catalog_is_a_hard_failure(monkeypatch):
    # the seeded-data assert (V1.6 5/5): a catalog that rendered 0 rows is a real
    # finding that blocks ship, not a mere abstain.
    _stub_rendered(
        monkeypatch,
        wow=_wow(),
        perf=_perf(),
        chip=_chip(checked=("palette-bg",)),
        data=_data(findings=(DataFinding("empty-collection", "0 rows"),)),
    )
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert v.passed is False
    assert accept_gauntlet.DATA in {g.gate for g in v.hard_failed}
    assert "data:empty-collection" in v.failed_classes


# ── 2b. composition-legs decoupled from the touch-leg (V1.6 14/5) ────────────
# `composition=True` runs the desktop-width COMPOSITION_LEGS (taste + hierarchy)
# as an ALWAYS-ON hard block, independently of `include_rendered` (which dials
# the touch/correctness legs — wow-dom @44px etc. — behind calibration 11/5).


def _stub_selective(monkeypatch, *, taste=None, hier=None):
    """Stub ONLY taste+hierarchy audit_files; make every other rendered leg
    raise so a test can prove the composition path runs nothing else."""
    taste = taste if taste is not None else _taste()
    hier = hier if hier is not None else _hier()

    async def _t(files, **kw):
        return taste

    async def _h(files, **kw):
        return hier

    async def _boom(*a, **kw):  # pragma: no cover — must never be awaited here
        raise AssertionError("composition path ran a non-composition leg")

    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _boom)


async def test_composition_runs_only_taste_and_hierarchy(monkeypatch):
    _stub_selective(monkeypatch)
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
    )
    assert [g.gate for g in v.gates] == [
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.TASTE,
        accept_gauntlet.HIERARCHY,
    ]
    assert v.render_expected is True
    assert v.passed is True
    # the touch/correctness legs are decoupled — they did NOT run
    assert accept_gauntlet.WOW_DOM not in {g.gate for g in v.gates}


async def test_composition_pale_app_hard_fails(monkeypatch):
    # a deliberately-pale app (bootstrap-baseline-equivalent) is REJECTED by the
    # composition floor — the falsifiable teeth of 14/5.
    from omnia_api.services.taste_gate import TasteFinding

    _stub_selective(
        monkeypatch,
        taste=_taste(score=2, findings=(TasteFinding("hero-imagery", "solid plate"),)),
    )
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
    )
    assert v.passed is False
    assert accept_gauntlet.TASTE in {g.gate for g in v.hard_failed}
    assert "taste:hero-imagery" in v.failed_classes


async def test_composition_abstain_is_not_a_hard_failure(monkeypatch):
    # a flaky composition render abstains — strict fails, hot path is spared.
    _stub_selective(
        monkeypatch,
        taste=_taste(rendered=False),
        hier=_hier(rendered=False),
    )
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
    )
    assert v.passed is False  # strict: abstain ≠ pass
    assert v.hard_failed == ()
    assert {g.gate for g in v.abstained} == {
        accept_gauntlet.TASTE,
        accept_gauntlet.HIERARCHY,
    }


async def test_composition_plus_full_render_has_no_duplicate_legs(monkeypatch):
    # composition union with the full rendered set must not double-run taste/hier.
    _stub_rendered(monkeypatch, wow=_wow(), perf=_perf(), chip=_chip(checked=("palette-bg",)))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=True,
        composition=True,
    )
    gates = [g.gate for g in v.gates]
    assert gates.count(accept_gauntlet.TASTE) == 1
    assert gates.count(accept_gauntlet.HIERARCHY) == 1
    # include_rendered fans the BLOCKING rendered legs; the advisory catalog leg
    # (V1.17) stays off without its own dial.
    assert set(gates) == {
        accept_gauntlet.DEFECT_REGISTRY,
        *(g for g in accept_gauntlet.RENDERED_GATES if g not in accept_gauntlet.ADVISORY_GATES),
    }


def test_composition_legs_are_desktop_width_taste_and_hierarchy():
    # the constant the decouple hangs on: composition = taste + hierarchy, and
    # neither is the 44px touch leg.
    assert accept_gauntlet.COMPOSITION_LEGS == (
        accept_gauntlet.TASTE,
        accept_gauntlet.HIERARCHY,
    )
    assert accept_gauntlet.WOW_DOM not in accept_gauntlet.COMPOSITION_LEGS


# ── 2c. fidelity leg (chip-pixel) decoupled from the touch leg (V2.5.2) ───────
# `fidelity=True` runs the chip-pixel request↔render check as an ALWAYS-ON hard
# block, independently of `include_rendered` (which dials the wow-dom touch leg
# behind calibration 11/5). chip-pixel has NO 44px false-positive and is inert
# without a spec, so it is the causality ship-block: the user's onboarding answers
# bite the render. Mirror of the composition decouple (14/5).


def _stub_fidelity(monkeypatch, *, chip):
    """Stub ONLY chip-pixel audit_files; make every other rendered leg raise so a
    test can prove the fidelity path runs nothing else."""

    async def _c(files, spec, **kw):
        return chip

    async def _boom(*a, **kw):  # pragma: no cover — must never be awaited here
        raise AssertionError("fidelity path ran a non-fidelity leg")

    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _c)
    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _boom)


async def test_fidelity_runs_only_chip_pixel(monkeypatch):
    _stub_fidelity(monkeypatch, chip=_chip(checked=("palette-bg",)))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=False,
        fidelity=True,
    )
    assert [g.gate for g in v.gates] == [
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.CHIP_PIXEL,
    ]
    assert v.render_expected is True
    assert v.passed is True
    # the touch/correctness/composition legs are decoupled — they did NOT run
    assert accept_gauntlet.WOW_DOM not in {g.gate for g in v.gates}
    assert accept_gauntlet.TASTE not in {g.gate for g in v.gates}


async def test_fidelity_mismatch_is_a_hard_failure(monkeypatch):
    # the falsifiable teeth of V2.5.2: a request↔render mismatch hard-fails ship.
    from omnia_api.services.chip_pixel_gate import FidelityFinding

    _stub_fidelity(
        monkeypatch,
        chip=_chip(
            findings=(FidelityFinding("palette-bg", "asked dark, rendered light"),),
            checked=("palette-bg",),
        ),
    )
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        fidelity=True,
    )
    assert v.passed is False
    assert accept_gauntlet.CHIP_PIXEL in {g.gate for g in v.hard_failed}
    assert "chip-pixel:palette-bg" in v.failed_classes


async def test_fidelity_compliant_render_passes(monkeypatch):
    # regression guard: a render that honours the spec still PASSES.
    _stub_fidelity(
        monkeypatch,
        chip=_chip(checked=("palette-bg", "primary-family")),
    )
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        fidelity=True,
    )
    assert v.passed is True
    assert v.hard_failed == ()


async def test_fidelity_abstain_is_not_a_hard_failure(monkeypatch):
    # a flaky chip-pixel render abstains — strict fails, the hot path is spared.
    _stub_fidelity(monkeypatch, chip=_chip(rendered=False))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        fidelity=True,
    )
    assert v.passed is False  # strict: abstain ≠ pass
    assert v.hard_failed == ()
    assert {g.gate for g in v.abstained} == {accept_gauntlet.CHIP_PIXEL}


async def test_fidelity_off_does_not_run_chip_pixel(monkeypatch):
    # default fidelity=False with composition on → chip-pixel stays off (the
    # _stub_selective boom-leg for chip would fire if it ran).
    _stub_selective(monkeypatch)
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
        fidelity=False,
    )
    assert accept_gauntlet.CHIP_PIXEL not in {g.gate for g in v.gates}


async def test_fidelity_plus_composition_has_no_duplicate_legs(monkeypatch):
    # union of the fidelity + composition dials runs chip + taste + hierarchy once
    # each, and nothing from the touch/correctness set (wow/perf/data).
    async def _c(files, spec, **kw):
        return _chip(checked=("palette-bg",))

    async def _t(files, **kw):
        return _taste()

    async def _h(files, **kw):
        return _hier()

    async def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("union ran a touch/correctness leg")

    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _c)
    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _boom)

    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
        fidelity=True,
    )
    gates = [g.gate for g in v.gates]
    assert gates.count(accept_gauntlet.CHIP_PIXEL) == 1
    assert set(gates) == {
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.CHIP_PIXEL,
        accept_gauntlet.TASTE,
        accept_gauntlet.HIERARCHY,
    }


def test_fidelity_legs_are_chip_pixel_only():
    # the constant the decouple hangs on: fidelity = chip-pixel, and it is neither
    # the 44px touch leg nor a composition leg.
    assert accept_gauntlet.FIDELITY_LEGS == (accept_gauntlet.CHIP_PIXEL,)
    assert accept_gauntlet.CHIP_PIXEL not in accept_gauntlet.TOUCH_LEGS
    assert accept_gauntlet.CHIP_PIXEL not in accept_gauntlet.COMPOSITION_LEGS


# ── 2d. reference CEILING leg decoupled via `reference=` (V1.13b) ────────────
# `reference=True` runs the pillar-1 CEILING leg as an ALWAYS-ON hard block,
# independently of `include_rendered`. It grades the candidate against a curated
# enterprise corpus; a below-corpus generation hard-fails ship, an empty corpus /
# render miss ABSTAINS (R-10). Mirror of the composition/fidelity decouple.


def _stub_reference(monkeypatch, *, ref):
    """Stub ONLY reference_corpus.audit_files; make every other rendered leg raise
    so a test can prove the reference path runs nothing else."""

    async def _r(files, **kw):
        return ref

    async def _boom(*a, **kw):  # pragma: no cover — must never be awaited here
        raise AssertionError("reference path ran a non-reference leg")

    monkeypatch.setattr(accept_gauntlet.reference_corpus, "audit_files", _r)
    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _boom)


async def test_reference_runs_only_reference(monkeypatch):
    _stub_reference(monkeypatch, ref=_ref())
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=False,
        fidelity=False,
        reference=True,
    )
    assert [g.gate for g in v.gates] == [
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.REFERENCE,
    ]
    assert v.render_expected is True
    assert v.passed is True


async def test_reference_below_corpus_is_a_hard_failure(monkeypatch):
    # the falsifiable teeth of V1.13b: a generation below the curated corpus
    # hard-fails ship with the exact class.
    _stub_reference(monkeypatch, ref=_ref(passed=False))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        reference=True,
    )
    assert v.passed is False
    assert accept_gauntlet.REFERENCE in {g.gate for g in v.hard_failed}
    assert "reference:reference-below" in v.failed_classes


async def test_reference_meets_or_beats_passes(monkeypatch):
    # regression guard: a generation that meets or beats the corpus PASSES.
    _stub_reference(monkeypatch, ref=_ref(passed=True))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        reference=True,
    )
    assert v.passed is True
    assert v.hard_failed == ()


async def test_reference_abstain_is_not_a_hard_failure(monkeypatch):
    # an empty corpus / render miss abstains — strict fails, the hot path is spared
    # (the carry-forward safety: wiring the leg never sinks ship on missing corpus).
    _stub_reference(monkeypatch, ref=_ref(rendered=False))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        reference=True,
    )
    assert v.passed is False  # strict: abstain ≠ pass
    assert v.hard_failed == ()
    assert {g.gate for g in v.abstained} == {accept_gauntlet.REFERENCE}


async def test_reference_off_does_not_run(monkeypatch):
    # default reference=False with composition on → the reference leg stays off
    # (the _boom leg for reference would fire if it ran).
    async def _t(files, **kw):
        return _taste()

    async def _h(files, **kw):
        return _hier()

    async def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("reference ran while reference=False")

    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.reference_corpus, "audit_files", _boom)
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
        reference=False,
    )
    assert accept_gauntlet.REFERENCE not in {g.gate for g in v.gates}


def test_reference_legs_are_reference_only():
    # the constant the decouple hangs on: reference is its own leg, in the order
    # tuple (so run() fans it, not orphaned) but neither a touch nor a composition
    # nor a fidelity leg.
    assert accept_gauntlet.REFERENCE_LEGS == (accept_gauntlet.REFERENCE,)
    assert accept_gauntlet.REFERENCE in accept_gauntlet.RENDERED_GATES
    assert accept_gauntlet.REFERENCE not in accept_gauntlet.TOUCH_LEGS
    assert accept_gauntlet.REFERENCE not in accept_gauntlet.COMPOSITION_LEGS
    assert accept_gauntlet.REFERENCE not in accept_gauntlet.FIDELITY_LEGS


# ── catalog-realism ratchet (V1.17) — ADVISORY, non-blocking quality-card ─────
# The eight RULE-10 demo-seeder fixes become a permanent floor: the gate scores
# the rendered catalog DOM, but as an ADVISORY card — it SURFACES (table/summary/
# subscore) yet NEVER blocks ship (out of hard_failed / strict passed /
# failed_classes). These prove both halves: it fans + surfaces, and it is inert
# on the ship decision.


def _stub_catalog(monkeypatch, *, cat):
    async def _cat_audit(files, **kw):
        return cat

    async def _boom(*a, **kw):  # pragma: no cover — must never be awaited here
        raise AssertionError("catalog path ran a non-catalog leg")

    monkeypatch.setattr(accept_gauntlet.catalog_coherence_gate, "audit_files", _cat_audit)
    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _boom)
    monkeypatch.setattr(accept_gauntlet.reference_corpus, "audit_files", _boom)


async def test_catalog_runs_only_catalog(monkeypatch):
    _stub_catalog(monkeypatch, cat=_cat())
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=False,
        fidelity=False,
        catalog=True,
    )
    assert [g.gate for g in v.gates] == [
        accept_gauntlet.DEFECT_REGISTRY,
        accept_gauntlet.CATALOG,
    ]
    assert v.render_expected is True
    assert v.passed is True  # clean registry + clean catalog


async def test_catalog_finding_is_advisory_not_a_hard_failure(monkeypatch):
    # the heart of V1.17: a real realism defect surfaces BUT never blocks ship.
    findings = (CatalogFinding(CHECKS[0], "197010₽ among ~1490₽ siblings"),)
    _stub_catalog(monkeypatch, cat=_cat(score=4, findings=findings))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        catalog=True,
    )
    # advisory: a failing catalog card does NOT sink the strict verdict, is NOT a
    # hard failure, and does NOT leak into the blocked-ship issue list.
    assert v.passed is True
    assert v.hard_failed == ()
    assert all(not c.startswith("catalog:") for c in v.failed_classes)
    # ...but the card SURFACES: its per-gate subscore carries the realism score.
    cat_sub = next(g for g in v.subscore()["gates"] if g["gate"] == "catalog")
    assert cat_sub["score"] == 4
    assert CHECKS[0] in cat_sub["findings"][0]["check"]


async def test_catalog_finding_does_not_sink_an_otherwise_clean_run(monkeypatch):
    # composition passes (taste+hierarchy) while catalog fires — ship still green.
    async def _t(files, **kw):
        return _taste()

    async def _h(files, **kw):
        return _hier()

    async def _cat_audit(files, **kw):
        return _cat(score=2, findings=(CatalogFinding(CHECKS[4], "акция до 2020"),))

    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.catalog_coherence_gate, "audit_files", _cat_audit)
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
        catalog=True,
    )
    assert accept_gauntlet.CATALOG in {g.gate for g in v.gates}
    assert v.passed is True
    assert v.hard_failed == ()


async def test_catalog_abstain_is_not_a_hard_failure(monkeypatch):
    # a render miss abstains — advisory, so it never sinks the hot path either.
    _stub_catalog(monkeypatch, cat=_cat(rendered=False))
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        catalog=True,
    )
    assert v.hard_failed == ()
    assert accept_gauntlet.CATALOG in {g.gate for g in v.abstained}


async def test_catalog_off_does_not_run(monkeypatch):
    # default catalog=False with composition on → the catalog leg stays off.
    async def _t(files, **kw):
        return _taste()

    async def _h(files, **kw):
        return _hier()

    async def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("catalog ran while catalog=False")

    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.catalog_coherence_gate, "audit_files", _boom)
    v = await accept_gauntlet.run(
        files={"index.html": _CLEAN_HTML},
        include_rendered=False,
        composition=True,
        catalog=False,
    )
    assert accept_gauntlet.CATALOG not in {g.gate for g in v.gates}


async def test_catalog_not_fanned_by_include_rendered(monkeypatch):
    # the decouple: even the broad include_rendered dial must NOT pull the advisory
    # leg in — only its own catalog= switch does.
    _stub_rendered(monkeypatch, wow=_wow(), perf=_perf(), chip=_chip(checked=("palette-bg",)))

    async def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("catalog ran via include_rendered without its dial")

    monkeypatch.setattr(accept_gauntlet.catalog_coherence_gate, "audit_files", _boom)
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})  # include_rendered=True
    assert accept_gauntlet.CATALOG not in {g.gate for g in v.gates}


def test_catalog_legs_are_catalog_only_and_advisory():
    assert accept_gauntlet.CATALOG_LEGS == (accept_gauntlet.CATALOG,)
    assert accept_gauntlet.CATALOG in accept_gauntlet.RENDERED_GATES
    assert accept_gauntlet.ADVISORY_GATES == frozenset({accept_gauntlet.CATALOG})
    assert accept_gauntlet.CATALOG not in accept_gauntlet.TOUCH_LEGS
    assert accept_gauntlet.CATALOG not in accept_gauntlet.COMPOSITION_LEGS
    assert accept_gauntlet.CATALOG not in accept_gauntlet.FIDELITY_LEGS
    assert accept_gauntlet.CATALOG not in accept_gauntlet.REFERENCE_LEGS


def test_advisory_exclusion_is_a_noop_without_an_advisory_gate():
    # back-compat guard: for every NON-advisory gate the ship semantics are
    # byte-identical — a hard-failing real gate still fails passed + hard_failed.
    gates = (
        accept_gauntlet.GateVerdict(
            gate=accept_gauntlet.TASTE,
            passed=False,
            abstained=False,
            classes=("hero-imagery",),
            summary="x",
            subscore={"gate": "taste"},
        ),
    )
    v = accept_gauntlet.GauntletVerdict(gates, render_expected=True)
    assert v.passed is False
    assert {g.gate for g in v.hard_failed} == {accept_gauntlet.TASTE}
    assert v.failed_classes == ("taste:hero-imagery",)


# ── 3. wiring: the rendered gates are no longer orphaned ─────────────────────

_SRC = Path(__file__).resolve().parents[1] / "src" / "omnia_api" / "services"


def test_aggregator_imports_every_rendered_gate():
    body = (_SRC / "accept_gauntlet.py").read_text(encoding="utf-8")
    mods = (
        "wow_dom_gate",
        "perf_a11y_gate",
        "chip_pixel_gate",
        "taste_gate",
        "hierarchy_gate",
        "data_gate",
        "defect_registry",
        "reference_corpus",
        "catalog_coherence_gate",
    )
    for mod in mods:
        assert mod in body, f"accept_gauntlet must import {mod}"


def test_acceptance_imports_the_gauntlet():
    # the ship-decision wiring: a NON-TEST importer of accept_gauntlet exists.
    body = (_SRC / "acceptance.py").read_text(encoding="utf-8")
    assert "accept_gauntlet" in body


# ── mobile composition dimension (V1.6 15/5) ─────────────────────────────────
# The composition legs (taste + hierarchy) used to render ONLY at desktop width,
# so niche_batch's dual-width loop silently scored the @390 pass at 1440 too — a
# page rich on desktop but collapsed to a monotone column on mobile went
# undetected. `composition_width` makes the composition legs honour the requested
# viewport, so the mobile pass is a real second render.


def _taste_at(w, *, score=5, findings=()):
    return TasteReport(tuple(findings), score, w, ("inter", "playfair"), rendered=True)


def _hier_at(w, *, score=3, findings=()):
    return HierarchyReport(tuple(findings), score, w, rendered=True)


def _stub_url_composition(monkeypatch, *, taste_by_width, hier_by_width):
    """Stub the composition gates' URL audits to a width-keyed report, recording
    the width each leg was actually asked to render at."""
    seen = {"taste": [], "hier": []}

    async def _t(url, **kw):
        seen["taste"].append(kw["width"])
        return taste_by_width(kw["width"])

    async def _h(url, **kw):
        seen["hier"].append(kw["width"])
        return hier_by_width(kw["width"])

    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_url", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_url", _h)
    return seen


async def test_composition_legs_default_to_desktop_width(monkeypatch):
    seen = _stub_url_composition(
        monkeypatch, taste_by_width=_taste_at, hier_by_width=_hier_at
    )
    await accept_gauntlet.run(url="http://x", include_rendered=False, composition=True)
    assert accept_gauntlet.COMPOSITION_WIDTH == 1440
    assert seen["taste"] == [accept_gauntlet.COMPOSITION_WIDTH]
    assert seen["hier"] == [accept_gauntlet.COMPOSITION_WIDTH]


async def test_composition_legs_honor_composition_width(monkeypatch):
    seen = _stub_url_composition(
        monkeypatch, taste_by_width=_taste_at, hier_by_width=_hier_at
    )
    await accept_gauntlet.run(
        url="http://x",
        include_rendered=False,
        composition=True,
        composition_width=390,
    )
    assert seen["taste"] == [390]
    assert seen["hier"] == [390]


async def test_mobile_monotone_collapse_fails_only_at_mobile_width(monkeypatch):
    # Rich at desktop (5/5) but collapses to a flat single column at mobile (2/5).
    def taste_by_width(w):
        if w >= 1000:
            return _taste_at(w, score=5)
        return _taste_at(w, score=2, findings=(TasteFinding(TYPE_SCALE, "flat type"),))

    _stub_url_composition(
        monkeypatch, taste_by_width=taste_by_width, hier_by_width=_hier_at
    )

    desktop = await accept_gauntlet.run(
        url="http://x", include_rendered=False, composition=True, composition_width=1440
    )
    assert desktop.passed is True

    mobile = await accept_gauntlet.run(
        url="http://x", include_rendered=False, composition=True, composition_width=390
    )
    assert mobile.passed is False
    assert accept_gauntlet.TASTE in {g.gate for g in mobile.hard_failed}
    assert "taste:type-scale" in mobile.failed_classes


def test_subscore_is_machine_readable():
    v_gates = (
        accept_gauntlet.GateVerdict(
            gate="defect-registry",
            passed=False,
            abstained=False,
            classes=("dead-auth-link",),
            summary="x",
            subscore={"gate": "defect-registry"},
        ),
    )
    v = accept_gauntlet.GauntletVerdict(v_gates, render_expected=False)
    sub = v.subscore()
    assert sub["passed"] is False
    assert sub["failed_classes"] == ["defect-registry:dead-auth-link"]
    assert sub["hard_failed"] == ["defect-registry"]
    assert "PASS" not in v.table()  # a failing run renders FAIL
