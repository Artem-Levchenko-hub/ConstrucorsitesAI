"""Agent VISION tool — the engine behind the builder loop's `see` action.

Gives the agent real EYES: screenshot the live dev-container page it is building,
hand it to the product-appropriate vision rubric (`vision_audit`), and return
concrete fix-deltas as the agent's observation. So
the agent stops being a blind author — it LOOKS at what it drew and fixes
"ugly"/"broken", not just "compiles".

Composes three existing pieces, while selecting the correct product rubric:
  dev_container.resolve_live_url  → where the running app lives
  preview.capture_live_url        → screenshot it (web: 1440 + 360; MAX: 390 + 360)
  vision_audit.audit_screenshots  → vision-model verdict + concrete issues

Fail-soft everywhere (R-10): no running preview, a render timeout, or a skipped
vision verdict all degrade to a harmless observation dict, never an exception
that could kill the loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

# A MAX Mini App is a mobile product inside the messenger, not a desktop landing
# page. Two common phone widths catch compact and regular layouts without paying
# for an irrelevant 1440px render.
_WEB_SEE_WIDTHS = (1440, 360)
_MAX_SEE_WIDTHS = (390, 360)
# Reuse the captured pixels for one cheap recovery attempt. The visual opinion is
# advisory for MAX, so a longer retry ladder would only delay functional proof.
_MAX_VISION_RETRY_DELAYS_SECONDS = (0, 3)

log = logging.getLogger(__name__)


async def see_page(
    project_id: UUID | str,
    *,
    path: str = "/",
    prompt_context: str = "",
    bootstrap_url: str | None = None,
    product_kind: str = "web",
) -> dict[str, Any]:
    """Screenshot the live dev container's ``path`` and return a vision critique.

    Returns the executor observation dict ``{ok, detail|error}``:
      * ok=False  — no running preview / render failed (a readable reason the
        agent can act on, e.g. "start the app first").
      * ok=True   — a verdict + concrete issues, OR a neutral MAX advisory when
        the screenshot exists but the subjective vision judge was unavailable.
    """
    # Lazy imports keep the pure agent engine + its unit tests free of the heavy
    # Playwright / dev_container dependency chain (same discipline as the
    # orchestrator executor in agent_builder).
    from omnia_api.services import dev_container, vision_audit
    from omnia_api.workers import preview

    try:
        pid = UUID(str(project_id))
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad project id"}

    rel = path if path.startswith("/") else "/" + path
    if bootstrap_url:
        parsed = urlsplit(bootstrap_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"ok": False, "error": "invalid preview bootstrap URL"}
        url = f"{parsed.scheme}://{parsed.netloc}{rel}"
    else:
        base = await dev_container.resolve_live_url(pid)
        if not base:
            return {
                "ok": False,
                "error": "preview not running — build or start the app first, then see",
            }
        url = base.rstrip("/") + rel

    try:
        widths = _MAX_SEE_WIDTHS if product_kind == "max_miniapp" else _WEB_SEE_WIDTHS
        shots = await preview.capture_live_url(
            url,
            widths,
            bootstrap_url=bootstrap_url,
            hide_platform_chrome=True,
        )
    except Exception as exc:
        return {"ok": False, "error": f"could not render {rel}: {type(exc).__name__}"}
    if not shots:
        return {"ok": False, "error": f"render produced no screenshot for {rel}"}
    if product_kind == "max_miniapp":
        missing_widths = sorted(set(_MAX_SEE_WIDTHS).difference(shots))
        if missing_widths:
            return {
                "ok": False,
                "error": (
                    "MAX render missed required viewport screenshot(s): "
                    + ", ".join(str(width) for width in missing_widths)
                ),
            }

    # A screenshot capture is comparatively expensive, while an unavailable or
    # unparsable judge response is often transient. Reuse the same captured
    # pixels for a bounded delayed retry window instead of sending the native
    # build agent through another paid reasoning turn just to call ``see`` again.
    verdict = None
    delays = _MAX_VISION_RETRY_DELAYS_SECONDS if product_kind == "max_miniapp" else (0,)
    for attempt, delay_seconds in enumerate(delays):
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        verdict = await vision_audit.audit_screenshots(
            shots,
            prompt_context=prompt_context,
            project_id=str(pid),
            product_kind=product_kind,
            retry_index=attempt,
        )
        if not verdict.skipped:
            if attempt:
                log.info(
                    "metric=max_vision_retry_recovered project_id=%s attempt=%d",
                    pid,
                    attempt + 1,
                )
            break
        log.warning(
            "metric=max_vision_retry_skipped project_id=%s attempt=%d total=%d reason=%s",
            pid,
            attempt + 1,
            len(delays),
            getattr(verdict, "skip_reason", "unknown") or "unknown",
        )
    assert verdict is not None

    # Browser-side signals a screenshot can't show: failed (>=400) fetches and JS
    # console/page errors on load. A failed request is a REAL runtime failure, so it
    # flips the observation to not-ok (the agent must not `done` over a broken fetch).
    diag_text = ""
    has_failed = False
    try:
        diag = await preview.capture_diagnostics(url, bootstrap_url=bootstrap_url)
        failed = diag.get("failed_requests") or []
        cons = diag.get("console_errors") or []
        has_failed = bool(failed)
        if failed or cons:
            blocks = []
            if failed:
                blocks.append(
                    "Failed network requests (fix — real runtime failures):\n"
                    + "\n".join(f"  - {x}" for x in failed)
                )
            if cons:
                blocks.append("Console / page errors:\n" + "\n".join(f"  - {x}" for x in cons))
            diag_text = "\n\nBROWSER SIGNALS:\n" + "\n\n".join(blocks)
    except Exception:
        pass

    if verdict.skipped:
        if product_kind == "max_miniapp":
            # The deterministic MAX gate still runs in the caller.  Once both
            # mobile screenshots exist, a transient outage of the optional
            # design judge must not discard an otherwise functional product.
            return {
                "ok": not has_failed,
                "verdict": "unscored",
                "score": None,
                "quality_advisory": True,
                "visual_audit_unavailable": True,
                "needs_fix": has_failed,
                "detail": (
                    f"LOOKED at {rel} at 360px and 390px; the optional visual judge was "
                    f"unavailable after {len(delays)} attempts.{diag_text}"
                ),
                "audit_attempts": len(delays),
            }
        return {
            "ok": True,
            "detail": (
                f"saw {rel}, but the vision judge was unavailable after {len(delays)} attempts"
            ),
            "proof_unavailable": True,
            "audit_attempts": len(delays),
        }
    max_quality_advisory = (
        product_kind == "max_miniapp"
        and verdict.verdict != "broken"
        and (verdict.verdict != "beautiful" or int(verdict.score) < 8)
    )
    # MAX completion is fact-gated by hydration, browser diagnostics and the
    # signed functional/release proof.  A subjective vision score is useful
    # feedback, but must not trap an otherwise working product in a paid
    # redesign loop.  The caller still turns failed browser requests and the
    # deterministic MAX functional gate into ``ok=False``/``needs_fix=True``.
    needs_fix = (
        verdict.verdict == "broken"
        if product_kind == "max_miniapp"
        else bool(verdict.issues) and verdict.verdict in {"broken", "generic"}
    )
    issue_rows = tuple(verdict.issues)
    if max_quality_advisory and not issue_rows:
        issue_rows = (
            "Главный экран: visual verdict не достиг production-grade уровня — "
            "усиль уникальную концепцию, продуктовую иерархию и mobile craft по брифу.",
        )
    issues = "\n".join(f"- {i}" for i in issue_rows) or "(no concrete issues)"

    feedback_heading = (
        "Optional visual polish notes" if max_quality_advisory else "Apply these concrete fixes"
    )

    return {
        "ok": not has_failed,
        "verdict": verdict.verdict,
        "score": verdict.score,
        "quality_advisory": max_quality_advisory,
        # For MAX, the visual-model opinion is advisory. Deterministic browser
        # and signed functional failures remain blocking through ``ok`` and the
        # caller-owned ``needs_fix`` override.
        "needs_fix": needs_fix,
        "detail": (
            f"LOOKED at {rel} — verdict: {verdict.verdict} ({verdict.score}/10)\n"
            f"{feedback_heading}:\n{issues}{diag_text}"
        ),
    }


__all__ = ["see_page"]
