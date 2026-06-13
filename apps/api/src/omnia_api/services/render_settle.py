"""Single-source render-settle helper for every gauntlet render leg (V1.6 13/5).

The ``domcontentloaded`` defect-class surfaced THREE separate times (preview 5.1,
hierarchy 9/5, latent taste 7/5) and was patched by hand each time — exactly the
recurrence the quality ratchet is supposed to make impossible. A generated app's
public ``/p/<slug>`` page is a Next.js client render: its hero, sections, accent
CTAs and colour land *after* ``load``, so reading at ``domcontentloaded`` sees an
empty shell — a false-FAIL on the strict streak and a hollow false-PASS on the
hot path.

Consolidating every leg onto one helper closes the class structurally (canon
R-04, single source of truth): the knowledge of "how to navigate to and settle a
client render" lives in exactly one place. No ``*_gate.py`` calls ``page.goto`` /
passes ``wait_until`` directly — the gate-suite AST assert
(``tests/test_render_settle.py``) fails the moment one tries. Helper is
unit-tested here once; each leg is asserted to route through it.

Every settle step is best-effort and never blocks the read (canon R-10): a flaky
network or font load degrades to a slightly earlier read, never a raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

# Canonical settle budget — every render leg waits the same way.
LOAD_TIMEOUT_MS = 4_000
NETWORKIDLE_TIMEOUT_MS = 8_000
PAINT_BEAT_MS = 900


async def settle(page: Page) -> None:
    """Let a client-rendered app actually paint before a gate reads it.

    ``load`` fires → network quiesces → fonts load → one Tailwind-JIT/paint beat.
    Each step is best-effort and swallows its own failure so a render leg never
    hard-fails on a flaky settle (R-10).

    The ``load`` wait is best-effort *on purpose* (V1.6 16/5): a live Next **dev**
    container (HMR socket + dev overlay keep connections open) NEVER fires the
    ``load`` event, so gating navigation on it abstained every entity app. A
    static / published page fires ``load`` near-instantly and this wait returns
    immediately — so the read still happens after ``load`` exactly as 13/5
    intended, while a dev container falls through to networkidle + paint beat
    (empirically reads the full client render, ~100 text nodes, not an empty
    shell)."""
    try:
        await page.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
    except Exception:
        pass
    try:
        await page.evaluate("() => document.fonts.ready")
    except Exception:
        pass
    try:
        await page.wait_for_timeout(PAINT_BEAT_MS)
    except Exception:
        pass


async def goto_and_settle(page: Page, url: str, *, timeout_ms: int) -> None:
    """The ONLY sanctioned navigation path for a render leg.

    Navigates with ``wait_until='domcontentloaded'`` — the only readiness signal a
    live Next dev container reliably emits (its ``load`` never fires; see
    :func:`settle`). ``domcontentloaded`` still raises on a real navigation failure
    (DNS / connection refused / bad URL), so the caller's fail-soft handler records
    an ABSTAIN (``rendered=False``); the ``goto`` is *not* wrapped in try/except.
    The canonical :func:`settle` then waits for ``load`` (best-effort) + network
    quiescence + fonts + a paint beat, so the client render is fully painted before
    a gate reads it — closing the original ``domcontentloaded``-empty-shell class
    (13/5) without abstaining on dev containers (16/5)."""
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await settle(page)
