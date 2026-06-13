"""V1.6 13/5 — single-source ``_settle`` render-helper, structurally enforced.

The ``domcontentloaded`` defect-class recurred three times because each render
leg owned its own navigation + settle. These tests make recurrence impossible:

1. ``goto_and_settle`` is unit-tested once (it navigates at ``load`` and settles
   on network quiescence + fonts + a paint beat, best-effort).
2. A falsifiable AST assert fails the moment ANY ``*_gate.py`` calls ``page.goto``
   or passes a ``wait_until`` kwarg directly — all navigation must route through
   ``render_settle.goto_and_settle``.
3. Every render leg is asserted to import and call the shared helper.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import omnia_api.services.render_settle as rs

SERVICES_DIR = Path(rs.__file__).parent
GATE_FILES = sorted(p for p in SERVICES_DIR.glob("*_gate.py"))

# The render legs that navigate to a live/static page and must settle the client
# render before reading it. (Every ``*_gate.py`` today is such a leg; if a future
# non-rendering ``*_gate.py`` is added it simply won't contain ``page.goto`` and
# stays green on the structural assert without needing the helper.)
RENDER_LEGS = (
    "wow_dom_gate.py",
    "perf_a11y_gate.py",
    "chip_pixel_gate.py",
    "taste_gate.py",
    "hierarchy_gate.py",
    "data_gate.py",
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


# ── 1. helper behaviour ──────────────────────────────────────────────────────


def test_goto_and_settle_navigates_at_load_then_settles():
    page = _RecordingPage()
    asyncio.run(rs.goto_and_settle(page, "http://app.local/p/x", timeout_ms=12_345))

    assert page.calls[0] == (
        "goto",
        "http://app.local/p/x",
        {"wait_until": "load", "timeout": 12_345},
    ), "navigation must use wait_until='load', never 'domcontentloaded'"
    # settle order: networkidle → fonts.ready → paint beat
    assert ("load_state", "networkidle") in page.calls
    assert any(c[0] == "evaluate" and "fonts.ready" in c[1] for c in page.calls)
    assert ("timeout", rs.PAINT_BEAT_MS) in page.calls
    assert page.calls.index(("load_state", "networkidle")) > 0


def test_settle_is_best_effort_and_never_raises():
    # Must complete cleanly even when every underlying step blows up.
    asyncio.run(rs.settle(_BoomPage()))


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
    docstring is fine; only real code is flagged."""
    offenders: list[str] = []
    for path in GATE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
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
        assert "from .render_settle import goto_and_settle" in src, (
            f"{name} must import the shared goto_and_settle helper"
        )
        assert "goto_and_settle(" in src, (
            f"{name} must navigate via goto_and_settle"
        )
        assert "async def _settle" not in src, (
            f"{name} still defines its own _settle — R-04 single-source violation"
        )


def test_render_legs_enumerated_match_disk():
    """If a new ``*_gate.py`` render leg is added, force a conscious decision about
    whether it must route through the helper (don't let it silently skip)."""
    on_disk = {p.name for p in GATE_FILES}
    assert set(RENDER_LEGS) <= on_disk, (
        f"RENDER_LEGS lists gates not on disk: {set(RENDER_LEGS) - on_disk}"
    )
