"""Signed browser acceptance gate for generated MAX Mini Apps.

The ordinary realtime gate proves a known API protocol. MAX products are
free-form mobile interfaces behind a managed, signed runtime, so their stable
contract is expressed through inert ``data-omnia-*`` hooks. This gate opens the
same signed preview as a real Studio reviewer, exercises main navigation and the
primary action, proves managed persistence across reload when requested, and
checks mobile/a11y/browser invariants. Missing evidence is red, never skipped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize

_TIMEOUT_MS = 20_000
_MAX_NAV_CONTROLS = 8


def evaluate_static_observation(observation: Mapping[str, Any]) -> list[Check]:
    """Convert browser facts into deterministic mobile/a11y checks."""

    try:
        nav_count = int(observation.get("nav_count", 0))
        heading_count = int(observation.get("heading_count", 0))
        unlabeled = int(observation.get("unlabeled_controls", 0))
        fake_controls = int(observation.get("fake_controls", 0))
        small_targets = int(observation.get("small_targets", 0))
        overflow = int(observation.get("horizontal_overflow", 0))
        primary_count = int(observation.get("primary_count", 0))
    except (TypeError, ValueError):
        return [Check("max_dom_contract", False, "browser returned malformed DOM facts")]
    return [
        Check(
            "max_main_navigation",
            nav_count >= 2,
            f"{nav_count} semantic data-omnia-screen-nav control(s)",
        ),
        Check(
            "max_primary_action",
            primary_count >= 1,
            f"{primary_count} visible data-omnia-primary-action control(s)",
        ),
        Check(
            "max_mobile_layout",
            overflow == 0,
            "no horizontal overflow" if overflow == 0 else f"overflow by {overflow}px",
        ),
        Check(
            "max_accessibility",
            heading_count >= 1 and unlabeled == 0 and fake_controls == 0 and small_targets == 0,
            (
                f"headings={heading_count}, unlabeled={unlabeled}, "
                f"non-semantic={fake_controls}, undersized_targets={small_targets}"
            ),
        ),
    ]


async def run_max_functional_gate(
    bootstrap_url: str,
    *,
    require_persistence: bool,
    timeout_ms: int = _TIMEOUT_MS,
) -> FunctionalVerdict:
    """Exercise one signed MAX preview and return a fail-closed verdict."""

    parsed = urlsplit(bootstrap_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return summarize([Check("max_signed_session", False, "invalid bootstrap URL")])
    target_url = f"{parsed.scheme}://{parsed.netloc}/"
    checks: list[Check] = []
    try:
        from playwright.async_api import async_playwright

        from omnia_api.services.auth_session import preview_resolver_args
        from omnia_api.services.render_settle import goto_and_settle, settle

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=preview_resolver_args(),
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    reduced_motion="reduce",
                )
                page = await context.new_page()
                browser_errors: list[str] = []
                failed_requests: list[str] = []

                def on_console(message: object) -> None:
                    if getattr(message, "type", "") == "error":
                        browser_errors.append(str(getattr(message, "text", ""))[:240])

                def on_page_error(error: object) -> None:
                    browser_errors.append(str(error)[:240])

                def on_response(response: object) -> None:
                    try:
                        status = int(getattr(response, "status", 0))
                        url = str(getattr(response, "url", ""))
                        if status >= 400 and "/api/omnia/" in url:
                            failed_requests.append(f"HTTP {status} {url}"[:240])
                    except Exception:
                        pass

                page.on("console", on_console)
                page.on("pageerror", on_page_error)
                page.on("response", on_response)
                await goto_and_settle(page, bootstrap_url, timeout_ms=timeout_ms)
                await goto_and_settle(page, target_url, timeout_ms=timeout_ms)

                observation = await page.evaluate(
                    """() => {
                      const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' &&
                          s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
                      };
                      const controls = [...document.querySelectorAll(
                        'button,a,[role="button"],[role="tab"]'
                      )]
                        .filter(visible);
                      const nav = [...document.querySelectorAll('[data-omnia-screen-nav]')]
                        .filter(visible);
                      const primary = [...document.querySelectorAll('[data-omnia-primary-action]')]
                        .filter(visible);
                      const unlabeled = controls.filter((el) => {
                        const text = (el.textContent || '').trim();
                        return !text && !el.getAttribute('aria-label') && !el.getAttribute('title');
                      });
                      const fake = controls.filter((el) =>
                        !['BUTTON','A'].includes(el.tagName) &&
                        (el.getAttribute('role') === 'button' || el.hasAttribute('onclick'))
                      );
                      const small = controls.filter((el) => {
                        const r = el.getBoundingClientRect();
                        return r.width < 36 || r.height < 36;
                      });
                      return {
                        nav_count: nav.length,
                        primary_count: primary.length,
                        heading_count: document.querySelectorAll(
                          'main h1,main h2,' +
                          '[data-omnia-product-runtime] h1,' +
                          '[data-omnia-product-runtime] h2'
                        ).length,
                        unlabeled_controls: unlabeled.length,
                        fake_controls: fake.length,
                        small_targets: small.length,
                        horizontal_overflow: Math.max(
                          0, document.documentElement.scrollWidth - innerWidth
                        ),
                      };
                    }"""
                )
                checks.extend(evaluate_static_observation(observation))

                nav = page.locator("[data-omnia-screen-nav]:visible")
                nav_count = min(await nav.count(), _MAX_NAV_CONTROLS)
                changed = 0
                for index in range(nav_count):
                    control = nav.nth(index)
                    before = await page.locator("[data-omnia-screen]:visible").all_inner_texts()
                    try:
                        await control.click(timeout=4_000)
                        await settle(page)
                    except Exception:
                        continue
                    after = await page.locator("[data-omnia-screen]:visible").all_inner_texts()
                    selected = await control.get_attribute("aria-selected")
                    current = await control.get_attribute("aria-current")
                    if before != after or selected == "true" or bool(current):
                        changed += 1
                checks.append(
                    Check(
                        "max_navigation_interaction",
                        nav_count >= 2 and changed >= min(2, nav_count),
                        f"{changed}/{nav_count} marked view switches changed active UI",
                    )
                )

                action_selector = (
                    "[data-omnia-persisted-action]:visible"
                    if require_persistence
                    else "[data-omnia-primary-action]:visible"
                )
                actions = page.locator(action_selector)
                action_count = await actions.count()
                before_text = ""
                action_changed = False
                writes: list[int] = []
                reads: list[int] = []

                def track_managed_response(response: object) -> None:
                    try:
                        request = response.request  # type: ignore[attr-defined]
                        url = str(getattr(response, "url", ""))
                        status = int(getattr(response, "status", 0))
                        method = str(getattr(request, "method", "")).upper()
                        if "/api/omnia/actions" not in url:
                            return
                        if method == "POST":
                            writes.append(status)
                        elif method == "GET":
                            reads.append(status)
                    except Exception:
                        pass

                page.on("response", track_managed_response)
                if action_count:
                    before_text = await page.locator("[data-omnia-product-runtime]").inner_text()
                    try:
                        await actions.first.click(timeout=4_000)
                        await settle(page)
                        after_text = await page.locator("[data-omnia-product-runtime]").inner_text()
                        action_changed = before_text != after_text or any(
                            200 <= x < 300 for x in writes
                        )
                    except Exception:
                        action_changed = False
                checks.append(
                    Check(
                        "max_primary_action_interaction",
                        action_count > 0 and action_changed,
                        "marked action changed UI or completed a managed write"
                        if action_changed
                        else "marked action was missing or produced no observable result",
                    )
                )

                if require_persistence:
                    write_ok = any(200 <= status < 300 for status in writes)
                    if write_ok:
                        await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                        await settle(page)
                    read_ok = any(200 <= status < 300 for status in reads)
                    checks.append(
                        Check(
                            "max_reload_persistence",
                            write_ok and read_ok,
                            (
                                f"managed write statuses={writes or 'none'}, "
                                f"reload reads={reads or 'none'}"
                            ),
                        )
                    )

                checks.append(
                    Check(
                        "max_browser_errors",
                        not browser_errors and not failed_requests,
                        (
                            "no console/page/managed-request errors"
                            if not browser_errors and not failed_requests
                            else "; ".join((browser_errors + failed_requests)[:6])
                        ),
                    )
                )
            finally:
                await browser.close()
    except Exception as exc:
        checks.append(
            Check("max_signed_session", False, f"browser proof failed: {type(exc).__name__}")
        )
    return summarize(checks)


__all__ = ["evaluate_static_observation", "run_max_functional_gate"]
