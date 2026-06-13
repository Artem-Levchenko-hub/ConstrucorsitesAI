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
NETWORKIDLE_TIMEOUT_MS = 8_000
PAINT_BEAT_MS = 900


async def settle(page: Page) -> None:
    """Let a client-rendered app actually paint before a gate reads it.

    Network quiesces → fonts load → one Tailwind-JIT/paint beat. Each step is
    best-effort and swallows its own failure so a render leg never hard-fails on
    a flaky settle (R-10)."""
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

    Navigates with ``wait_until='load'`` (never ``domcontentloaded`` — that is the
    recurring defect class) then runs the canonical :func:`settle`. The ``goto``
    itself is *not* wrapped in a try/except: a navigation failure must propagate so
    the caller's fail-soft handler can record an ABSTAIN (``rendered=False``)."""
    await page.goto(url, wait_until="load", timeout=timeout_ms)
    await settle(page)
