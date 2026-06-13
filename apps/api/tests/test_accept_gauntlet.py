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
from omnia_api.services.chip_pixel_gate import FidelityReport
from omnia_api.services.data_gate import DataFinding, DataReport
from omnia_api.services.hierarchy_gate import HierarchyReport
from omnia_api.services.perf_a11y_gate import PerfA11yFinding, PerfA11yReport
from omnia_api.services.taste_gate import TasteReport
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


def _stub_rendered(monkeypatch, *, wow, perf, chip, taste=None, hier=None, data=None):
    taste = taste if taste is not None else _taste()
    hier = hier if hier is not None else _hier()
    data = data if data is not None else _data()

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

    monkeypatch.setattr(accept_gauntlet.wow_dom_gate, "audit_files", _w)
    monkeypatch.setattr(accept_gauntlet.perf_a11y_gate, "audit_files", _p)
    monkeypatch.setattr(accept_gauntlet.chip_pixel_gate, "audit_files", _c)
    monkeypatch.setattr(accept_gauntlet.taste_gate, "audit_files", _t)
    monkeypatch.setattr(accept_gauntlet.hierarchy_gate, "audit_files", _h)
    monkeypatch.setattr(accept_gauntlet.data_gate, "audit_files", _d)


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
    )
    v = await accept_gauntlet.run(files={"index.html": _CLEAN_HTML})
    assert v.passed is False  # strict: abstain ≠ pass
    assert v.hard_failed == ()  # but not a hard finding
    assert {g.gate for g in v.abstained} == set(accept_gauntlet.RENDERED_GATES)


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
    )
    for mod in mods:
        assert mod in body, f"accept_gauntlet must import {mod}"


def test_acceptance_imports_the_gauntlet():
    # the ship-decision wiring: a NON-TEST importer of accept_gauntlet exists.
    body = (_SRC / "acceptance.py").read_text(encoding="utf-8")
    assert "accept_gauntlet" in body


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
