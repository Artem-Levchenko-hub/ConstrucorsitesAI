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

The shared browser SESSION (``render_url`` / ``render_files``) left with the site
builder together with its only two callers, the page-audit gates; every gate that
stayed opens its own session (it needs its own viewport, auth state and resolver
rules) and borrows only the navigation.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import yleum_api.services.render_settle as rs

SERVICES_DIR = Path(rs.__file__).parent
GATE_FILES = sorted(p for p in SERVICES_DIR.glob("*_gate.py"))


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
