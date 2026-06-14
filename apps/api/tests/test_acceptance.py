"""Acceptance gate — structural checks + render-driven verdict (Phase 11)."""

from omnia_api.services import acceptance
from omnia_api.services.acceptance import _structural_issues

# A minimal but properly-COMPOSED clean page: one <h1>, a live anchor, a real
# type scale (h1 + h2) and section rhythm (header + section + footer). It clears
# the V3.3 compose floor (≥2 type sizes, ≥3 sections, a hero) — a page below that
# floor reads as flat, not "enterprise with one generation", and is meant to fail.
_GOOD = (
    "<!doctype html><html lang='ru'><head><title>T</title>"
    "<style>h1{font-size:3rem}h2{font-size:1.5rem}</style></head><body>"
    "<header><h1>Заголовок</h1>"
    "<a href='#contacts'>Связаться</a></header>"
    "<section id='features'><h2>Возможности</h2><p>Описание</p></section>"
    "<section id='contacts'>x</section>"
    "<footer><p>© 2026</p></footer>"
    "</body></html>"
)


def _capture_stub(overflow_widths=()):
    """Build a fake `capture()` coroutine returning given overflow widths."""
    from omnia_api.workers.preview import CaptureResult

    async def _fake(files, widths=(375, 768, 1440), **kw):
        return {
            int(w): CaptureResult(
                png=b"png",
                viewport_width=int(w),
                scroll_width=int(w) + (40 if w in overflow_widths else 0),
                has_overflow=w in overflow_widths,
            )
            for w in widths
        }

    return _fake


def test_structural_clean():
    assert _structural_issues({"index.html": _GOOD}) == []


def test_structural_dead_link():
    bad = {"index.html": _GOOD.replace("#contacts", "#")}
    assert any("ссылка" in i for i in _structural_issues(bad))


def test_structural_missing_h1():
    bad = {"index.html": _GOOD.replace("<h1>Заголовок</h1>", "<p>nope</p>")}
    assert any("нет ни одного <h1>" in i for i in _structural_issues(bad))


def test_structural_multiple_h1():
    bad = {"index.html": _GOOD.replace("<h1>Заголовок</h1>", "<h1>A</h1><h1>B</h1>")}
    assert any("h1" in i.lower() for i in _structural_issues(bad))


async def test_evaluate_passes_clean(monkeypatch):
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    res = await acceptance.evaluate(
        {"index.html": _GOOD}, project_id="p", run_vision=False
    )
    assert res.passed
    assert res.structural_ok
    assert res.responsive_ok
    assert res.feedback == ""


async def test_evaluate_fails_on_overflow(monkeypatch):
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub(overflow_widths={360}))
    res = await acceptance.evaluate(
        {"index.html": _GOOD}, project_id="p", run_vision=False
    )
    assert not res.passed
    assert not res.responsive_ok
    assert "360px" in res.feedback


async def test_evaluate_fails_on_dead_link(monkeypatch):
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    bad = {"index.html": _GOOD.replace("#contacts", "#")}
    res = await acceptance.evaluate(bad, project_id="p", run_vision=False)
    assert not res.passed
    assert not res.structural_ok
    assert "ссылк" in res.feedback.lower()


async def test_evaluate_render_failure_is_soft(monkeypatch):
    from omnia_api.workers import preview

    async def _boom(files, widths=(375, 768, 1440), **kw):
        raise RuntimeError("no chromium here")

    monkeypatch.setattr(preview, "capture", _boom)
    # Render blew up → responsive layer is skipped, not fatal; a clean page
    # still passes on structure alone.
    res = await acceptance.evaluate(
        {"index.html": _GOOD}, project_id="p", run_vision=False
    )
    assert res.passed
    assert res.responsive_ok  # skipped == treated as ok


async def test_evaluate_no_index_html():
    res = await acceptance.evaluate(
        {"style.css": "body{}"}, project_id="p", run_vision=False
    )
    assert not res.passed
    assert res.verdict == "broken"


async def test_evaluate_rejects_planted_gauntlet_defect(monkeypatch):
    """V1.6 keystone: a known defect class (dead-auth-link) is caught by the
    gauntlet's deterministic leg and BLOCKS ship — proving the gauntlet is wired
    into the acceptance ship decision, not orphaned. The page is otherwise
    structurally clean (real <a> href, exactly one <h1>)."""
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    # Auth CTA pointing at a dead self-link — structurally a live `/`, so the
    # structural dead-link check passes; only the gauntlet registry catches it.
    planted = (
        "<!doctype html><html lang='ru'><head><title>T</title></head><body>"
        "<h1>Заголовок</h1><a href='/'>Войти</a></body></html>"
    )
    res = await acceptance.evaluate(
        {"index.html": planted}, project_id="p", run_vision=False
    )
    assert not res.passed
    assert any("dead-auth-link" in i for i in res.issues)
    assert "гейт" in res.feedback.lower()


async def test_evaluate_gauntlet_clean_does_not_block(monkeypatch):
    """A page with no known defect class is not blocked by the gauntlet leg."""
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    res = await acceptance.evaluate(
        {"index.html": _GOOD}, project_id="p", run_vision=False
    )
    assert res.passed
    assert not any("dead-auth-link" in i for i in res.issues)


def _spy_gauntlet(monkeypatch, captured):
    """Patch accept_gauntlet.run to record its kwargs and return a clean verdict."""
    from omnia_api.services import accept_gauntlet
    from omnia_api.services.accept_gauntlet import GauntletVerdict

    async def _run(**kwargs):
        captured["spec"] = kwargs.get("spec")
        captured["fidelity"] = kwargs.get("fidelity")
        return GauntletVerdict((), render_expected=False)

    monkeypatch.setattr(accept_gauntlet, "run", _run)


async def test_evaluate_wires_discovery_spec_into_gauntlet(monkeypatch):
    """V2.5.1 — a persisted discovery_spec reifies into a non-empty FidelitySpec
    handed to the gauntlet (so the chip-pixel leg can assert request↔render)."""
    from omnia_api.services.chip_pixel_gate import FidelitySpec
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    captured: dict[str, object] = {}
    _spy_gauntlet(monkeypatch, captured)

    await acceptance.evaluate(
        {"index.html": _GOOD},
        project_id="p",
        run_vision=False,
        discovery_spec={
            "dark_mode": True,
            "primary_family": "violet",
            "sections": ["catalog"],
            "tone": "premium",
        },
    )
    spec = captured["spec"]
    assert isinstance(spec, FidelitySpec)
    assert spec.dark_mode is True
    assert spec.primary_family == "violet"
    assert spec.sections == ("catalog",)
    assert spec.tone == "premium"


async def test_evaluate_no_discovery_spec_keeps_none(monkeypatch):
    """Back-compat: no discovery_spec → spec=None (the empty-spec no-op the
    gauntlet already defaults to). Behaviour byte-identical to pre-V2.5."""
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    captured: dict[str, object] = {}
    _spy_gauntlet(monkeypatch, captured)

    await acceptance.evaluate({"index.html": _GOOD}, project_id="p", run_vision=False)
    assert captured["spec"] is None


async def test_evaluate_enables_fidelity_for_nonempty_spec(monkeypatch):
    """V2.5.2 — a non-empty discovery_spec turns the chip-pixel leg ON as an
    always-on hard block (fidelity=True), independent of the render-gates flag."""
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    captured: dict[str, object] = {}
    _spy_gauntlet(monkeypatch, captured)

    await acceptance.evaluate(
        {"index.html": _GOOD},
        project_id="p",
        run_vision=False,
        discovery_spec={"dark_mode": True, "primary_family": "violet"},
    )
    assert captured["fidelity"] is True


async def test_evaluate_disables_fidelity_without_spec(monkeypatch):
    """Back-compat: no discovery_spec → fidelity OFF → no extra chip-pixel render
    (byte-identical to pre-V2.5.2)."""
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    captured: dict[str, object] = {}
    _spy_gauntlet(monkeypatch, captured)

    await acceptance.evaluate({"index.html": _GOOD}, project_id="p", run_vision=False)
    assert captured["fidelity"] is False


async def test_evaluate_disables_fidelity_for_empty_spec(monkeypatch):
    """An all-null discovery_spec row reifies to an empty spec → asserts nothing →
    fidelity OFF (no point paying for the render)."""
    from omnia_api.workers import preview

    monkeypatch.setattr(preview, "capture", _capture_stub())
    captured: dict[str, object] = {}
    _spy_gauntlet(monkeypatch, captured)

    await acceptance.evaluate(
        {"index.html": _GOOD},
        project_id="p",
        run_vision=False,
        discovery_spec={
            "dark_mode": None,
            "primary_family": None,
            "sections": [],
            "tone": None,
        },
    )
    assert captured["fidelity"] is False
