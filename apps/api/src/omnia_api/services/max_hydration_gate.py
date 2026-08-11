"""Deterministic proof that a MAX product survived client hydration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from omnia_api.services.auth_session import preview_resolver_args
from omnia_api.services.render_settle import goto_and_settle

log = logging.getLogger(__name__)

MIN_VISIBLE_TEXT_FRAGMENTS = 4

_AUDIT_JS = r"""
() => {
  const runtime = document.querySelector('.omnia-max-runtime');
  const product = runtime?.querySelector('[data-omnia-product-runtime="true"]') ?? null;
  const productRoots = product ? Array.from(product.children) : [];
  const visible = (element) => {
    let current = element;
    while (current) {
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden' ||
          style.contentVisibility === 'hidden' || Number(style.opacity || 1) <= 0) {
        return false;
      }
      current = current.parentElement;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const inViewport = (element) => {
    const rect = element.getBoundingClientRect();
    return rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
  };
  let textCount = 0;
  if (product) {
    const walker = document.createTreeWalker(product, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (parent && node.textContent?.trim() && visible(parent)) textCount += 1;
    }
  }
  return {
    runtime_present: !!runtime,
    product_present: !!product,
    product_visible: productRoots.some((root) => visible(root) && inViewport(root)),
    text_count: textCount,
  };
}
"""


@dataclass(frozen=True)
class MaxHydrationReport:
    passed: bool
    rendered: bool
    detail: str


def evaluate_observation(
    observation: dict[str, Any], *, rendered: bool = True
) -> MaxHydrationReport:
    """Score browser facts without judging design, palette or composition."""

    if not rendered:
        return MaxHydrationReport(False, False, "browser did not render the preview")
    if not bool(observation.get("runtime_present")):
        return MaxHydrationReport(False, True, "managed MAX runtime root is missing")
    if not bool(observation.get("product_present")):
        return MaxHydrationReport(False, True, "generated ProductApp did not mount after hydration")
    if not bool(observation.get("product_visible")):
        return MaxHydrationReport(False, True, "generated ProductApp mounted but is not visible")
    try:
        text_count = int(observation.get("text_count", 0))
    except (TypeError, ValueError):
        text_count = 0
    if text_count < MIN_VISIBLE_TEXT_FRAGMENTS:
        return MaxHydrationReport(
            False,
            True,
            f"ProductApp mounted but only {text_count} visible text fragment(s) rendered",
        )
    return MaxHydrationReport(
        True,
        True,
        f"ProductApp hydrated with {text_count} visible text fragment(s)",
    )


async def audit_url(url: str, *, timeout_ms: int = 20_000) -> MaxHydrationReport:
    """Render the mobile preview; any missing evidence is a failed proof."""

    try:
        from playwright.async_api import async_playwright

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
                try:
                    page = await context.new_page()
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    observation = await page.evaluate(_AUDIT_JS)
                    return evaluate_observation(observation)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("max_hydration_gate failed: %r", exc)
        return MaxHydrationReport(
            False,
            False,
            f"browser proof failed: {type(exc).__name__}",
        )
