"""Acceptance gate for freeform generation (Phase 11, Sprint 2.2).

The single orchestrator that decides whether a generated page is good enough
to ship, and — when it isn't — produces concrete feedback the model can act
on. Three layers, cheapest first:

  1. structure  — deterministic hard checks (no dead links, exactly one <h1>).
                  `ui_audit` design-rubric failures are surfaced as *advice*,
                  not blockers: freeform deliberately bends discipline rules,
                  so only true brokenness blocks here.
  2. responsive — render at 375 / 768 / 1440 and reject horizontal overflow.
  3. gauntlet   — `accept_gauntlet.run` fans every landed quality gate (the
                  deterministic defect-registry ratchet always; the rendered
                  wow-dom / perf-a11y / chip-pixel legs when dialed on). This is
                  the SHIP DECISION since V1.6: a real gauntlet finding blocks.
  4. vision     — (optional, ADVISORY since V1.6) screenshot → vision model
                  "broken/generic/beautiful". Feeds feedback; no longer blocks.

This is "rigidity in acceptance, freedom in composition": we don't constrain
*how* the page is built, we verify the result and feed back what to fix. All
layers fail SOFT — a render, vision, or gauntlet error never hard-fails
generation, it just drops that layer's signal (R-10).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from omnia_api.core.config import get_settings
from omnia_api.services import accept_gauntlet, originality, vision_audit
from omnia_api.services.chip_pixel_gate import FidelitySpec
from omnia_api.services.link_validator import find_dead_links
from omnia_api.services.ui_audit import audit as ui_audit

log = logging.getLogger(__name__)

_H1_OPEN_RE = re.compile(r"<h1[\s>]", re.IGNORECASE)


@dataclass(frozen=True)
class AcceptanceResult:
    """Verdict of one acceptance pass."""

    passed: bool
    score: int  # vision score, or 10 when vision didn't run
    verdict: str  # vision verdict or "structural" / "ok"
    structural_ok: bool
    responsive_ok: bool
    vision_ran: bool
    issues: tuple[str, ...] = ()
    feedback: str = ""
    # dHash of the accepted page (Sprint 4) — caller stores it in the pool.
    fingerprint: int | None = None


def _html_pool(files: dict[str, str]) -> dict[str, str]:
    return {
        p: c for p, c in files.items() if p.endswith((".html", ".htm"))
    }


def _structural_issues(files: dict[str, str]) -> list[str]:
    """Hard, deterministic brokenness — the things that make a page unusable.

    Kept narrow on purpose: dead links and a missing/duplicated <h1>. Design
    discipline (fonts, colours, buttons) is `ui_audit`'s job and is advisory
    here so freeform creativity isn't punished as "broken".
    """
    issues: list[str] = []

    dead = find_dead_links(files)
    if dead:
        issues.extend(f"[ссылка] {d}" for d in dead[:8])

    index = files.get("index.html", "")
    h1_count = len(_H1_OPEN_RE.findall(index))
    if h1_count == 0:
        issues.append("[структура] на странице нет ни одного <h1> — добавь один заголовок H1.")
    elif h1_count > 1:
        issues.append(
            f"[структура] на странице {h1_count} тегов <h1> — оставь ровно один, "
            "остальные сделай <h2>."
        )
    return issues


def _advisory_issues(html_pool: dict[str, str]) -> list[str]:
    """Soft `ui_audit` findings — fed to the model as advice, never blocking."""
    try:
        report = ui_audit(html_pool)
    except Exception as exc:
        log.warning("acceptance: ui_audit failed (ignored): %r", exc)
        return []
    return [f"[дизайн] {f.description}" for f in report.failures[:6]]


def _build_feedback(
    structural: list[str],
    overflow_widths: list[int],
    advisory: list[str],
    vision: vision_audit.VisionVerdict,
    originality: list[str] | None = None,
    gauntlet: list[str] | None = None,
) -> str:
    lines: list[str] = []
    lines.extend(structural)
    if gauntlet:
        lines.extend(gauntlet)
    if originality:
        lines.extend(originality)
    for w in overflow_widths:
        lines.append(
            f"[адаптив] при ширине {w}px появляется горизонтальный скролл — "
            "контент шире экрана. Сделай секции/картинки адаптивными (max-w-full, "
            "грид с переносом, без фиксированных широких блоков)."
        )
    if vision.verdict in {"broken", "generic"} and vision.issues:
        lines.append(f"[дизайн/vision: {vision.verdict}, score {vision.score}/10]")
        lines.extend(f"  • {i}" for i in vision.issues)
    # Advisory ui_audit findings go last — least important.
    lines.extend(advisory)
    if not lines:
        return ""
    return (
        "Приёмка страницы не пройдена. Исправь перечисленное и верни ПОЛНЫЕ файлы "
        "целиком в тех же <file>-блоках (ничего лишнего не меняй):\n"
        + "\n".join(f"— {ln}" if not ln.startswith("  ") else ln for ln in lines)
    )


async def evaluate(
    files: dict[str, str],
    *,
    project_id: str,
    prompt_context: str = "",
    user_id: str | None = None,
    run_vision: bool | None = None,
    min_score: int | None = None,
    widths: tuple[int, ...] | None = None,
    run_originality: bool | None = None,
    discovery_spec: dict[str, object] | None = None,
) -> AcceptanceResult:
    """Run the gate over `files` and return a pass/fail verdict + feedback.

    `run_vision` / `min_score` default to settings (`USE_VISION_AUDIT`,
    `ACCEPTANCE_MIN_SCORE`). Renders ONCE and reuses the screenshots for both
    the overflow check and the vision audit.
    """
    settings = get_settings()
    if run_vision is None:
        run_vision = settings.use_vision_audit
    if min_score is None:
        min_score = settings.acceptance_min_score
    if run_originality is None:
        run_originality = settings.use_originality

    html_pool = _html_pool(files)
    if not html_pool or "index.html" not in files:
        return AcceptanceResult(
            passed=False,
            score=0,
            verdict="broken",
            structural_ok=False,
            responsive_ok=False,
            vision_ran=False,
            issues=("нет index.html для проверки",),
            feedback=(
                "Не найден index.html — верни полноценную страницу "
                'в <file path="index.html">.'
            ),
        )

    # ── 1. structure (hard) + advisory ───────────────────────────────────
    structural = _structural_issues(files)
    advisory = _advisory_issues(html_pool)
    structural_ok = not structural

    # ── 2. render once (overflow) + 3. reuse shots for vision ─────────────
    overflow_widths: list[int] = []
    screenshots: dict[int, bytes] = {}
    responsive_ok = True
    try:
        # Imported lazily: pulls Playwright into the process only when the
        # gate actually runs (keeps the flag-off path import-light, R-07).
        from omnia_api.workers.preview import DEFAULT_CAPTURE_WIDTHS, capture

        # full_page=True: the vision/design judge must see the WHOLE landing, not
        # only the first viewport. A minimal/airy hero (type-as-hero + whitespace,
        # exactly what the brief asks for) read as "empty / loading screen / no
        # CTA" when only the top screen was shot → false "broken" → destructive
        # repair. Overflow detection is unaffected (it uses scroll_width).
        shots = await capture(
            files, widths=widths or DEFAULT_CAPTURE_WIDTHS, full_page=True
        )
        for w, res in shots.items():
            screenshots[w] = res.png
            if res.has_overflow:
                overflow_widths.append(w)
        overflow_widths.sort()
        responsive_ok = not overflow_widths
    except Exception as exc:
        log.warning("acceptance: render harness failed (responsive skipped): %r", exc)

    # ── 4. vision (optional, fail-soft) ───────────────────────────────────
    verdict = vision_audit.VisionVerdict(verdict="ok", score=10, issues=(), skipped=True)
    vision_ran = False
    if run_vision and screenshots:
        verdict = await vision_audit.audit_screenshots(
            screenshots,
            prompt_context=prompt_context,
            user_id=user_id,
            project_id=project_id,
        )
        vision_ran = not verdict.skipped
    # Vision is ADVISORY since V1.6 (the gauntlet is the ship decision): its
    # verdict no longer gates `passed`, it only feeds feedback. `min_score` is
    # kept on the signature for callers/back-compat.
    _ = min_score

    # ── 5. originality (optional, fail-soft) — Sprint 4 anti-generic ──────
    orig_fp: int | None = None
    orig_issue: str | None = None
    if run_originality and screenshots:
        widest = max(screenshots)
        orig_fp, orig_issue = await originality.originality_issue(
            project_id,
            screenshots[widest],
            max_distance=settings.originality_max_distance,
        )
    originality_ok = orig_issue is None

    # ── 6. acceptance gauntlet — the SHIP DECISION (V1.6 keystone) ────────
    # The deterministic defect-registry leg always runs (cheap, pure). The
    # COMPOSITION legs (taste + hierarchy, desktop width) are the ALWAYS-ON
    # richness floor — `acceptance_gauntlet_composition_gates` (default ON, V1.6
    # 14/5): before it, the pillar-1 awwwards promise gated ZERO shipping
    # requests. The chip-pixel FIDELITY leg is also ALWAYS-ON (V2.5.2) whenever a
    # non-empty `discovery_spec` exists — `acceptance_gauntlet_fidelity_gate`
    # (default ON) — so the user's onboarding answers causally gate the render.
    # The remaining TOUCH/correctness legs (wow-dom 44px / perf-a11y / data) stay
    # behind `acceptance_gauntlet_render_gates` until calibration
    # (11/5). We block ship on a REAL finding (`hard_failed`) — a render flake
    # that merely abstains never sinks an otherwise-good page (R-10). The vision
    # verdict, formerly a ship blocker, is now ADVISORY: it only feeds feedback.
    gauntlet_lines: list[str] = []
    gauntlet_classes: list[str] = []
    gauntlet_ok = True
    # The chip-pixel leg only asserts the axes the user actually steered in
    # onboarding (V2.5): a persisted `discovery_spec` reifies those answers so
    # the gate can flag a request↔render mismatch. A None / empty spec asserts
    # nothing — byte-identical to the pre-V2.5 no-op default.
    spec = FidelitySpec.from_dict(discovery_spec) if discovery_spec else None
    # V2.5.2 — turn the chip-pixel leg into an ALWAYS-ON hard ship-block, but only
    # when there is a real, non-empty spec to honour. It is decoupled from the
    # `render_gates` flag (which keeps the wow-dom touch leg behind calibration):
    # a chip→pixel mismatch (dark+violet requested, light render) now fails ship.
    # No spec / empty spec → leg stays off → no extra render, behaviour unchanged.
    fidelity = (
        settings.acceptance_gauntlet_fidelity_gate
        and spec is not None
        and not spec.is_empty
    )
    try:
        gauntlet = await accept_gauntlet.run(
            files=files,
            spec=spec,
            include_rendered=settings.acceptance_gauntlet_render_gates,
            composition=settings.acceptance_gauntlet_composition_gates,
            fidelity=fidelity,
        )
        if gauntlet.hard_failed:
            gauntlet_ok = False
            gauntlet_classes = list(gauntlet.failed_classes)
            gauntlet_lines = [
                f"[гейт:{g.gate}] {g.summary}" for g in gauntlet.hard_failed
            ]
    except Exception as exc:  # defensive — gauntlet must never hard-fail the gate
        log.warning("acceptance: gauntlet failed (ignored): %r", exc)

    passed = structural_ok and responsive_ok and originality_ok and gauntlet_ok

    feedback = "" if passed else _build_feedback(
        structural,
        overflow_widths,
        advisory,
        verdict,
        originality=[orig_issue] if orig_issue else [],
        gauntlet=gauntlet_lines,
    )
    issues = tuple(
        structural
        + gauntlet_classes
        + [f"overflow@{w}px" for w in overflow_widths]
        + ([orig_issue] if orig_issue else [])
        + list(verdict.issues)
    )
    final_verdict = (
        "ok"
        if passed
        else (
            "broken"
            if not gauntlet_ok
            else (
                verdict.verdict
                if vision_ran
                else ("generic" if orig_issue else "structural")
            )
        )
    )
    return AcceptanceResult(
        passed=passed,
        score=verdict.score,
        verdict=final_verdict,
        structural_ok=structural_ok,
        responsive_ok=responsive_ok,
        vision_ran=vision_ran,
        issues=issues,
        feedback=feedback,
        fingerprint=orig_fp,
    )


__all__ = ["AcceptanceResult", "evaluate"]
