"""V1.6 13/5 — single-source ``_settle`` render-helper, structurally enforced.

The ``domcontentloaded`` defect-class recurred three times because each render
leg owned its own navigation + settle. These tests make recurrence impossible:

1. ``goto_and_settle`` is unit-tested once. It navigates at ``domcontentloaded``
   — the only readiness signal a live Next **dev** container emits (its ``load``
   never fires; V1.6 16/5) — then settles on ``load`` (best-effort) + network
   quiescence + fonts + a paint beat. The empty-shell class is closed by the
   SETTLE (it waits for ``load`` + networkidle + paint before any read), not by
   the navigation ``wait_until``.
2. A falsifiable AST assert fails the moment ANY ``*_gate.py`` calls ``page.goto``
   or passes a ``wait_until`` kwarg directly — all navigation must route through
   ``render_settle.goto_and_settle``.
3. Every url/files render leg is asserted to run the shared session
   (``render_url`` / ``render_files``) and to open no browser, context or page of
   its own; the session itself is unit-tested here once.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

import yleum_api.services.render_settle as rs

SERVICES_DIR = Path(rs.__file__).parent
GATE_FILES = sorted(p for p in SERVICES_DIR.glob("*_gate.py"))

# The render legs that navigate to a live/static page and must settle the client
# render before reading it. (Every ``*_gate.py`` today is such a leg; if a future
# non-rendering ``*_gate.py`` is added it simply won't contain ``page.goto`` and
# stays green on the structural assert without needing the helper.)
RENDER_LEGS = (
    "wow_dom_gate.py",
    "chip_pixel_gate.py",
)


# ── fake page double (no chromium needed — pure call-order assertions) ────────


class _RecordingPage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def goto(self, url: str, **kw) -> None:
        self.calls.append(("goto", url, kw))

    async def wait_for_load_state(self, state: str, **kw) -> None:
        self.calls.append(("load_state", state))

    async def evaluate(self, expr: str):
        self.calls.append(("evaluate", expr))

    async def wait_for_timeout(self, ms: int) -> None:
        self.calls.append(("timeout", ms))


class _BoomPage:
    """Every settle step raises — the helper must swallow all of them (R-10)."""

    async def wait_for_load_state(self, *a, **k):
        raise RuntimeError("networkidle never fired")

    async def evaluate(self, *a, **k):
        raise RuntimeError("fonts hung")

    async def wait_for_timeout(self, *a, **k):
        raise RuntimeError("clock skew")


class _NeverFontsPage:
    async def wait_for_load_state(self, *a, **k):
        return None

    async def evaluate(self, *a, **k):
        await asyncio.Event().wait()

    async def wait_for_timeout(self, *a, **k):
        return None


# ── 1. helper behaviour ──────────────────────────────────────────────────────


def test_goto_and_settle_navigates_at_domcontentloaded_then_settles():
    page = _RecordingPage()
    asyncio.run(rs.goto_and_settle(page, "http://app.local/p/x", timeout_ms=12_345))

    # Navigation gates on domcontentloaded — the only signal a live Next dev
    # container reliably emits. The empty-shell class is closed by the SETTLE,
    # not this wait_until (V1.6 16/5).
    assert page.calls[0] == (
        "goto",
        "http://app.local/p/x",
        {"wait_until": "domcontentloaded", "timeout": 12_345},
    ), "navigation must use wait_until='domcontentloaded' (dev containers never fire load)"
    # settle order: load (best-effort) → networkidle → fonts.ready → paint beat
    assert ("load_state", "load") in page.calls
    assert ("load_state", "networkidle") in page.calls
    assert any(c[0] == "evaluate" and "fonts.ready" in c[1] for c in page.calls)
    assert ("timeout", rs.PAINT_BEAT_MS) in page.calls
    # load is waited BEFORE networkidle, both after navigation
    assert page.calls.index(("load_state", "load")) > 0
    assert page.calls.index(("load_state", "load")) < page.calls.index(
        ("load_state", "networkidle")
    )


def test_settle_is_best_effort_and_never_raises():
    # Must complete cleanly even when every underlying step blows up.
    asyncio.run(rs.settle(_BoomPage()))


def test_settle_bounds_a_never_resolving_font_promise(monkeypatch):
    monkeypatch.setattr(rs, "FONT_TIMEOUT_MS", 10, raising=False)
    asyncio.run(asyncio.wait_for(rs.settle(_NeverFontsPage()), timeout=0.2))


def test_goto_and_settle_does_not_swallow_navigation_errors():
    class _NavBoom:
        async def goto(self, *a, **k):
            raise RuntimeError("dns")

    # A navigation failure must propagate so the caller records an ABSTAIN.
    try:
        asyncio.run(rs.goto_and_settle(_NavBoom(), "http://x", timeout_ms=1))
    except RuntimeError:
        return
    raise AssertionError("navigation error must propagate, not be swallowed")


# ── 2. falsifiable structural ratchet ────────────────────────────────────────


def test_no_gate_navigates_directly():
    """No ``*_gate.py`` may call ``page.goto`` or pass ``wait_until`` itself — the
    knowledge of how to navigate + settle a client render lives only in
    ``render_settle``. AST-based so prose mentioning ``domcontentloaded`` in a
    docstring is fine; only real code is flagged. The signed-session bootstrap
    must inspect the HTTP response before any render or authenticated probe;
    permit only that exact navigation, covered by rejection tests separately.
    """
    offenders: list[str] = []
    for path in GATE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        signed_bootstrap = set()
        if path.name == "coverage_gate.py":
            expected_guard = ast.dump(ast.parse("cell_preview is None", mode="eval").body)
            expected_navigation = ast.dump(ast.parse(
                'page.goto(cell_preview.bootstrap_url, wait_until="domcontentloaded")',
                mode="eval",
            ).body)
            for function in tree.body:
                if not isinstance(function, ast.AsyncFunctionDef):
                    continue
                if function.name != "run_coverage_gate":
                    continue
                for branch in ast.walk(function):
                    if not isinstance(branch, ast.If) or not branch.orelse:
                        continue
                    if ast.dump(branch.test) != expected_guard:
                        continue
                    assignment = branch.orelse[0]
                    if (
                        isinstance(assignment, ast.Assign)
                        and len(assignment.targets) == 1
                        and isinstance(assignment.targets[0], ast.Name)
                        and assignment.targets[0].id == "bootstrap_response"
                        and isinstance(assignment.value, ast.Await)
                        and ast.dump(assignment.value.value) == expected_navigation
                    ):
                        signed_bootstrap.add(assignment.value.value)
            assert len(signed_bootstrap) == 1, (
                "signed bootstrap exception must stay narrowly scoped"
            )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if node in signed_bootstrap:
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "goto":
                offenders.append(f"{path.name}:{node.lineno} calls .goto() directly")
            for kw in node.keywords:
                if kw.arg == "wait_until":
                    offenders.append(
                        f"{path.name}:{node.lineno} passes wait_until= directly"
                    )
    assert not offenders, (
        "render legs must navigate via render_settle.goto_and_settle, not "
        "page.goto/wait_until:\n" + "\n".join(offenders)
    )


def test_every_render_leg_routes_through_shared_helper():
    for name in RENDER_LEGS:
        src = (SERVICES_DIR / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
            and node.module == "render_settle"
            for alias in node.names
        }
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "render_url" in imported and "render_url" in called, (
            f"{name} must render through the shared render_settle session"
        )
        own_session = [
            f"{name}:{node.lineno} .{node.func.attr}()"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"launch", "new_context", "new_page"}
        ]
        assert not own_session, (
            "render legs must not open a browser session of their own:\n"
            + "\n".join(own_session)
        )
        assert "async def _settle" not in src, (
            f"{name} still defines its own _settle — R-04 single-source violation"
        )


def test_the_shared_session_is_the_only_place_that_navigates_or_opens_a_browser():
    """The ban above exempts ``render_settle`` itself, and that is now where every
    url/files leg navigates — so the owner gets its own ratchet: ``goto`` /
    ``wait_until`` live only in ``goto_and_settle``, and a browser, context or page
    is opened only in ``render_url`` (``render_files`` must delegate to it, never
    grow a second, unsettled session)."""
    tree = ast.parse(Path(rs.__file__).read_text(encoding="utf-8"))
    allowed = {"goto": "goto_and_settle", "wait_until": "goto_and_settle",
               "launch": "render_url", "new_context": "render_url", "new_page": "render_url"}
    offenders: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            used = [kw.arg for kw in node.keywords if kw.arg == "wait_until"]
            if isinstance(node.func, ast.Attribute) and node.func.attr in allowed:
                used.append(node.func.attr)
            offenders += [
                f"{function.name}:{node.lineno} uses {name}"
                for name in used
                if allowed[name] != function.name
            ]
    assert not offenders, "\n".join(offenders)

    calls = {
        function.name: {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for function in tree.body
        if isinstance(function, ast.AsyncFunctionDef)
    }
    assert "goto_and_settle" in calls["render_url"]
    assert "render_url" in calls["render_files"]


class _Session:
    """Minimal ``async_playwright()`` double around one ``_RecordingPage``."""

    def __init__(self, page: _RecordingPage) -> None:
        self.page = page
        self.chromium = self
        self.closed: list[str] = []

    def __call__(self) -> _Session:
        return self

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def launch(self, **kw) -> _Session:
        self.page.calls.append(("launch", kw))
        return self

    async def new_context(self, **kw) -> _Session:
        self.page.calls.append(("context", kw))
        return self

    async def new_page(self) -> _RecordingPage:
        return self.page

    async def close(self) -> None:
        self.closed.append("closed")


def test_render_url_settles_before_the_audit_and_swallows_nothing(monkeypatch):
    page = _RecordingPage()
    session = _Session(page)
    monkeypatch.setattr("playwright.async_api.async_playwright", session)

    async def audit(seen):
        page.calls.append(("audit",))
        raise RuntimeError("audit blew up")

    with pytest.raises(RuntimeError, match="audit blew up"):
        asyncio.run(rs.render_url("http://app.local/", audit, width=390, height=844, timeout_ms=7))

    kinds = [call[0] for call in page.calls]
    assert kinds[:3] == ["launch", "context", "goto"]
    assert kinds[-1] == "audit" and "timeout" in kinds, "the audit must read a settled page"
    assert page.calls[2] == (
        "goto", "http://app.local/", {"wait_until": "domcontentloaded", "timeout": 7}
    )
    assert session.closed == ["closed", "closed"], "context and browser close on failure too"


def test_render_legs_enumerated_match_disk():
    """If a new ``*_gate.py`` render leg is added, force a conscious decision about
    whether it must route through the helper (don't let it silently skip)."""
    on_disk = {p.name for p in GATE_FILES}
    assert set(RENDER_LEGS) <= on_disk, (
        f"RENDER_LEGS lists gates not on disk: {set(RENDER_LEGS) - on_disk}"
    )


# ── CLI wrapper shared by the render legs ─────────────────────────────────────


class _Report:
    def __init__(self, passed: bool) -> None:
        self.passed = passed

    def summary(self) -> str:
        return "сводка"

    def subscore(self) -> dict[str, object]:
        return {"оценка": 1}


def _cli(passed: bool):
    seen: list[object] = []

    async def audit_url(url: str) -> _Report:
        seen.append(("url", url))
        return _Report(passed)

    async def audit_files(files: dict[str, str]) -> _Report:
        seen.append(("files", files))
        return _Report(passed)

    return seen, audit_url, audit_files


def test_gate_cli_without_a_target_prints_usage(capsys):
    seen, audit_url, audit_files = _cli(True)
    assert rs.run_gate_cli(["prog"], "taste_gate", audit_url, audit_files) == 2
    assert capsys.readouterr().out == (
        "usage: python -m yleum_api.services.taste_gate <url|index.html-dir>\n"
    )
    assert seen == []


@pytest.mark.parametrize(("passed", "code"), [(True, 0), (False, 1)])
def test_gate_cli_audits_a_url(capsys, passed, code):
    seen, audit_url, audit_files = _cli(passed)
    assert rs.run_gate_cli(["prog", "https://app.test/"], "x", audit_url, audit_files) == code
    assert seen == [("url", "https://app.test/")]
    assert capsys.readouterr().out == 'сводка\n{\n  "оценка": 1\n}\n'


def test_gate_cli_audits_every_html_file_of_a_directory(tmp_path, capsys):
    (tmp_path / "pages").mkdir()
    (tmp_path / "index.html").write_text("<h1>Главная</h1>", encoding="utf-8")
    (tmp_path / "pages" / "about.html").write_text("<p>О нас</p>", encoding="utf-8")
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    seen, audit_url, audit_files = _cli(True)

    assert rs.run_gate_cli(["prog", str(tmp_path)], "x", audit_url, audit_files) == 0

    assert seen == [
        ("files", {"index.html": "<h1>Главная</h1>", "pages/about.html": "<p>О нас</p>"})
    ]
