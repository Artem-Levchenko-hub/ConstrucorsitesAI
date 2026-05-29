"""Acceptance gate — structural checks + render-driven verdict (Phase 11)."""

from omnia_api.services import acceptance
from omnia_api.services.acceptance import _structural_issues

_GOOD = (
    "<!doctype html><html lang='ru'><head><title>T</title></head><body>"
    "<h1>Заголовок</h1>"
    "<a href='#contacts'>Связаться</a>"
    "<section id='contacts'>x</section>"
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

    monkeypatch.setattr(preview, "capture", _capture_stub(overflow_widths={375}))
    res = await acceptance.evaluate(
        {"index.html": _GOOD}, project_id="p", run_vision=False
    )
    assert not res.passed
    assert not res.responsive_ok
    assert "375px" in res.feedback


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
