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

The browser session around that navigation is single-source too: :func:`render_url`
and :func:`render_files` own "launch chromium → context → page → settle → audit →
close". A gate keeps only what is its own — the page audit, the ABSTAIN report
and its log line.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page, StorageState

# Canonical settle budget — every render leg waits the same way.
LOAD_TIMEOUT_MS = 4_000
NETWORKIDLE_TIMEOUT_MS = 8_000
FONT_TIMEOUT_MS = 4_000
PAINT_BEAT_MS = 900

# Stranger first-paint budget (V4.0b). NORTH STAR pillar 4: "коллега открыл → за
# секунды". The share-link first-paint gate asserts a cold incognito visitor sees
# a contentful paint within this wall — anything slower silently kills k-factor.
# Single-source R-04 constant: it lives here beside the other paint-timing budgets
# (not "e.g. 3s" inline) so a gate cannot be satisfied by an arbitrary number and
# an adversarial fixture (paint > budget) is forced to fail against a fixed bar.
FIRST_PAINT_BUDGET_MS = 3_000


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
        await asyncio.wait_for(
            page.evaluate("() => document.fonts.ready"),
            timeout=FONT_TIMEOUT_MS / 1_000,
        )
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


async def render_url[T](
    url: str,
    audit: Callable[[Page], Awaitable[T]],
    *,
    width: int,
    height: int,
    timeout_ms: int,
    storage_state: StorageState | None = None,
    launch_args: list[str] | None = None,
    init_script: str | None = None,
) -> T:
    """One render session: settle ``url`` in a fresh context, return ``audit(page)``.

    ``storage_state=None`` is an anonymous context; ``init_script`` is installed
    before navigation. Nothing is swallowed here — the calling gate owns the
    fail-soft ABSTAIN, whose report shape and log line differ per gate. Context
    and browser are closed on every path.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=launch_args)
        try:
            context = await browser.new_context(
                viewport={"width": int(width), "height": height},
                reduced_motion="reduce",
                storage_state=storage_state,
            )
            try:
                page = await context.new_page()
                if init_script is not None:
                    await page.add_init_script(init_script)
                await goto_and_settle(page, url, timeout_ms=timeout_ms)
                return await audit(page)
            finally:
                await context.close()
        finally:
            await browser.close()


async def render_files[T](
    files: dict[str, str],
    audit: Callable[[Page], Awaitable[T]],
    *,
    prefix: str,
    width: int,
    height: int,
    timeout_ms: int,
    launch_args: list[str] | None = None,
    init_script: str | None = None,
) -> T:
    """:func:`render_url` over a static ``{path: html}`` page set's ``index.html``.

    The set lives in a temporary directory (``prefix`` names the gate, so a
    directory leaked by a killed worker is attributable) only for the session.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        workdir = Path(tmp)
        for path, content in files.items():
            full = workdir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        return await render_url(
            (workdir / "index.html").as_uri(),
            audit,
            width=width,
            height=height,
            timeout_ms=timeout_ms,
            launch_args=launch_args,
            init_script=init_script,
        )


def run_gate_cli(
    argv: list[str],
    gate: str,
    audit_url: Callable[[str], Coroutine[Any, Any, Any]],
    audit_files: Callable[[dict[str, str]], Coroutine[Any, Any, Any]],
) -> int:
    """``python -m yleum_api.services.<gate> <url|index.html-dir>`` for a render leg."""
    if len(argv) < 2:
        print(f"usage: python -m yleum_api.services.{gate} <url|index.html-dir>")
        return 2
    target = argv[1]
    if target.startswith(("http://", "https://")):
        report = asyncio.run(audit_url(target))
    else:
        root = Path(target)
        files = {
            str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in root.rglob("*.html")
        }
        report = asyncio.run(audit_files(files))
    print(report.summary())
    print(json.dumps(report.subscore(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1
