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

from typing import Any
from uuid import UUID

# Viewports handed to the vision judge — one wide + one narrow is enough to judge
# composition and mobile, and matches `vision_audit._VISION_WIDTHS`.
_SEE_WIDTHS = (1440, 360)


async def see_page(
    project_id: UUID | str,
    *,
    path: str = "/",
    prompt_context: str = "",
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

    base = await dev_container.resolve_live_url(pid)
    if not base:
        return {
            "ok": False,
            "error": "preview not running — build or start the app first, then see",
        }
    rel = path if path.startswith("/") else "/" + path
    url = base.rstrip("/") + rel

    try:
        shots = await preview.capture_live_url(url, _SEE_WIDTHS)
    except Exception as exc:  # noqa: BLE001 — a render error must not kill the loop
        return {"ok": False, "error": f"could not render {rel}: {type(exc).__name__}"}
    if not shots:
        return {"ok": False, "error": f"render produced no screenshot for {rel}"}

    verdict = await vision_audit.audit_screenshots(
        shots, prompt_context=prompt_context, project_id=str(pid)
    )
    if verdict.skipped:
        return {
            "ok": True,
            "detail": f"saw {rel}, but the vision judge was unavailable (skipped)",
        }
    issues = "\n".join(f"- {i}" for i in verdict.issues) or "(no concrete issues)"
    return {
        "ok": True,
        "detail": (
            f"LOOKED at {rel} — verdict: {verdict.verdict} ({verdict.score}/10)\n"
            f"Apply these concrete fixes:\n{issues}"
        ),
    }


__all__ = ["see_page"]
