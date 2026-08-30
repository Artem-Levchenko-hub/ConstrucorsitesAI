"""Agent VISION tool — the engine behind the builder loop's `see` action.

Gives the agent real EYES: screenshot the live dev-container page it is building,
hand it to the same Awwwards-strict vision judge the acceptance gate uses
(`vision_audit`), and return concrete fix-deltas as the agent's observation. So
the agent stops being a blind author — it LOOKS at what it drew and fixes
"ugly"/"broken", not just "compiles".

Composes three existing pieces, adds nothing structural:
  dev_container.resolve_live_url  → where the running app lives
  preview.capture_live_url        → screenshot it (1440 + 360)
  vision_audit.audit_screenshots  → vision-model verdict + concrete issues

Fail-soft everywhere (R-10): no running preview, a render timeout, or a skipped
vision verdict all degrade to a harmless observation dict, never an exception
that could kill the loop.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

# Viewports handed to the vision judge — one wide + one narrow is enough to judge
# composition and mobile, and matches `vision_audit._VISION_WIDTHS`.
_SEE_WIDTHS = (1440, 360)


async def see_page(
    project_id: UUID | str,
    *,
    path: str = "/",
    prompt_context: str = "",
    bootstrap_url: str | None = None,
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
        shots = await preview.capture_live_url(
            url,
            _SEE_WIDTHS,
            bootstrap_url=bootstrap_url,
        )
    except Exception as exc:
        return {"ok": False, "error": f"could not render {rel}: {type(exc).__name__}"}
    if not shots:
        return {"ok": False, "error": f"render produced no screenshot for {rel}"}

    verdict = await vision_audit.audit_screenshots(
        shots, prompt_context=prompt_context, project_id=str(pid)
    )
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
        return {
            "ok": not has_failed,
            "detail": (
                f"saw {rel}, but the vision judge was unavailable (skipped){diag_text}"
            ),
        }

    issues = "\n".join(f"- {i}" for i in verdict.issues) or "(no concrete issues)"
    return {
        "ok": not has_failed,
        "detail": (
            f"LOOKED at {rel} — verdict: {verdict.verdict} ({verdict.score}/10)\n"
            f"Apply these concrete fixes:\n{issues}{diag_text}"
        ),
    }


def normalize_max_see_observation(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep real browser failures blocking; soften only missing QA infrastructure."""

    observation = dict(result)
    if observation.get("ok") or observation.get("detail"):
        return observation
    return {
        "ok": True,
        "detail": str(observation.get("error") or "MAX visual QA unavailable"),
        "proof_unavailable": True,
    }


__all__ = ["normalize_max_see_observation", "see_page"]
