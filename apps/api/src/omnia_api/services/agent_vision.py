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

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

# A MAX Mini App is a mobile product inside the messenger, not a desktop landing
# page. Two common phone widths catch compact and regular layouts without paying
# for an irrelevant 1440px render.
_WEB_SEE_WIDTHS = (1440, 360)
_MAX_SEE_WIDTHS = (390, 360)


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
      * ok=True   — a verdict + concrete issues, OR a neutral note when the
        vision judge was unavailable (skipped) so the agent isn't misled.
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
        )
    except Exception as exc:
        return {"ok": False, "error": f"could not render {rel}: {type(exc).__name__}"}
    if not shots:
        return {"ok": False, "error": f"render produced no screenshot for {rel}"}

    verdict = await vision_audit.audit_screenshots(
        shots,
        prompt_context=prompt_context,
        project_id=str(pid),
        product_kind=product_kind,
    )
    if verdict.skipped:
        return {
            "ok": True,
            "detail": f"saw {rel}, but the vision judge was unavailable (skipped)",
        }
    max_quality_failed = product_kind == "max_miniapp" and (
        verdict.verdict != "beautiful" or int(verdict.score) < 8
    )
    needs_fix = (
        max_quality_failed
        if product_kind == "max_miniapp"
        else bool(verdict.issues) and verdict.verdict in {"broken", "generic"}
    )
    issue_rows = tuple(verdict.issues)
    if needs_fix and not issue_rows:
        issue_rows = (
            "Главный экран: visual verdict не достиг production-grade уровня — "
            "усиль уникальную концепцию, продуктовую иерархию и mobile craft по брифу.",
        )
    issues = "\n".join(f"- {i}" for i in issue_rows) or "(no concrete issues)"

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

    return {
        "ok": not has_failed,
        "verdict": verdict.verdict,
        "score": verdict.score,
        # Every actionable verdict blocks visual proof. The native MAX loop
        # applies the concrete delta, rebuilds, and asks again until the judge
        # returns a clean production-grade result (or explicitly skips).
        "needs_fix": needs_fix,
        "detail": (
            f"LOOKED at {rel} — verdict: {verdict.verdict} ({verdict.score}/10)\n"
            f"Apply these concrete fixes:\n{issues}{diag_text}"
        ),
    }


__all__ = ["see_page"]
