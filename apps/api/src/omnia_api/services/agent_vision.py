"""Standalone visual-review utility, not exposed or invoked by generation.

Retained for compatibility and independent diagnostic tests. It can capture a
live page and return a vision critique, but no generation executor routes here.

Composes three existing pieces, adds nothing structural:
  dev_container.resolve_live_url  → where the running app lives
  preview.capture_live_url_report → screenshots (1440 + 360) + failure notes
  vision_audit.audit_screenshots  → vision-model verdict + concrete issues

Missing preview or render failure returns a failed observation with a reason.
Only MAX callers explicitly normalize unavailable visual infrastructure; real
browser HTTP failures remain blocking even when the vision judge is skipped.
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
      * ok=False  — app/runtime failure, or missing browser proof. Only the MAX
        caller may explicitly normalize unavailable visual infrastructure.
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

    def _capture_notes(report: Any) -> str:
        summary = str(getattr(report, "summary", lambda: "")() or "").strip()
        if not summary:
            return ""
        return "\n\nCAPTURE NOTES:\n- " + summary.replace("; ", "\n- ")

    def _diagnostic_text(diag: Mapping[str, Any]) -> tuple[str, bool]:
        failed = [str(x) for x in (diag.get("failed_requests") or [])]
        cons = [str(x) for x in (diag.get("console_errors") or [])]
        stage = [str(x) for x in (diag.get("stage_errors") or [])]
        has_failed = bool(failed)
        blocks = []
        if failed:
            blocks.append(
                "Failed network requests (fix — real runtime failures):\n"
                + "\n".join(f"  - {x}" for x in failed)
            )
        if cons:
            blocks.append("Console / page errors:\n" + "\n".join(f"  - {x}" for x in cons))
        if stage:
            blocks.append("Capture stage notes:\n" + "\n".join(f"  - {x}" for x in stage))
        if not blocks:
            return "", has_failed
        return "\n\nBROWSER SIGNALS:\n" + "\n\n".join(blocks), has_failed

    try:
        report = await preview.capture_live_url_report(
            url,
            _SEE_WIDTHS,
            bootstrap_url=bootstrap_url,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                f"visual QA unavailable for {rel}: "
                f"browser setup failed ({type(exc).__name__})"
            ),
            "proof_unavailable": True,
        }
    shots = report.screenshots
    diag_text = ""
    has_failed = False
    try:
        diag = await preview.capture_diagnostics(url, bootstrap_url=bootstrap_url)
        diag_text, has_failed = _diagnostic_text(diag)
    except Exception:
        pass
    if not shots:
        if has_failed:
            return {
                "ok": False,
                "detail": (
                    f"could not render {rel} because the app failed while loading."
                    f"{diag_text}"
                ),
            }
        return {
            "ok": False,
            "error": (
                f"visual QA unavailable for {rel}: no screenshot landed."
                f"{_capture_notes(report)}{diag_text}"
            ),
            "proof_unavailable": True,
        }

    verdict = await vision_audit.audit_screenshots(
        shots, prompt_context=prompt_context, project_id=str(pid)
    )
    capture_text = _capture_notes(report)

    if verdict.skipped:
        return {
            "ok": not has_failed,
            "detail": (
                f"saw {rel}, but the vision judge was unavailable (skipped)"
                f"{capture_text}{diag_text}"
            ),
        }

    issues = "\n".join(f"- {i}" for i in verdict.issues) or "(no concrete issues)"
    return {
        "ok": not has_failed,
        "detail": (
            f"LOOKED at {rel} — verdict: {verdict.verdict} ({verdict.score}/10)\n"
            f"Apply these concrete fixes:\n{issues}{capture_text}{diag_text}"
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
