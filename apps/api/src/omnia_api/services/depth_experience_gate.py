"""Deterministic source gate for meaningful interactive depth.

The generation prompt is intentionally permissive about art direction, but the
result must contain at least one *real* depth implementation.  A decorative SVG
with ``data-parallax`` is not 3D and deliberately does not satisfy this gate.

Accepted evidence is one of:

* Omnia's managed ``data-omnia-depth`` WebGL/media primitive;
* an authored WebGL/Three/@react-three-fiber scene;
* an animated canvas scene;
* a media-backed multi-layer scene with perspective and pointer/scroll input.

The scan is source-only, cheap, and works for both standalone HTML and generated
React/Next/Vite files.  Backend-only projects are inert because they expose no
visual surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NO_DEPTH_EXPERIENCE = "no-depth-experience"
FLAT_SVG_PSEUDO_3D = "flat-svg-pseudo-3d"

_VISUAL_EXTENSIONS = (".html", ".htm", ".tsx", ".jsx", ".vue", ".svelte")
_VISUAL_SURFACE = re.compile(
    r"<(?:html|main|section|canvas|img|video)\b|className\s*=|createRoot\s*\(",
    re.IGNORECASE,
)
_MANAGED_DEPTH = re.compile(r"\bdata-omnia-depth(?:\s*=|\b)", re.IGNORECASE)
_WEBGL = re.compile(
    r"""getContext\s*\(\s*["']webgl2?["']|WebGL(?:2)?RenderingContext|
        \bTHREE\.|from\s+["']three["']|@react-three/fiber|<Canvas\b""",
    re.IGNORECASE | re.VERBOSE,
)
_CANVAS = re.compile(r"<canvas\b|createElement\s*\(\s*['\"]canvas['\"]", re.IGNORECASE)
_ANIMATION = re.compile(
    r"requestAnimationFrame|useFrame\s*\(|setAnimationLoop\s*\(", re.IGNORECASE
)
_MEDIA = re.compile(r"<(?:img|video)\b|url\s*\(\s*['\"]?https?://", re.IGNORECASE)
_LAYER = re.compile(
    r"data-depth-layer|translateZ\s*\(|perspective\s*(?:\(|:)|preserve-3d",
    re.IGNORECASE,
)
_INPUT = re.compile(
    r"pointermove|mousemove|deviceorientation|scrollY|addEventListener\s*\(\s*['\"]scroll",
    re.IGNORECASE,
)
_SVG = re.compile(r"<svg\b", re.IGNORECASE)
_PARALLAX_ONLY = re.compile(
    r"data-parallax|parallax-layer|translate[3dXY]*\s*\(", re.IGNORECASE
)


@dataclass(frozen=True)
class DepthExperienceReport:
    judged: bool
    passed: bool
    kind: str | None = None
    classes: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if not self.judged:
            return "depth: inert (no visual surface)"
        if self.passed:
            return f"depth: clean ({self.kind})"
        return (
            "depth: missing a genuine interactive depth scene; flat SVG/parallax "
            "does not count"
        )


def _visual_source(files: dict[str, str]) -> str:
    chunks: list[str] = []
    for path, body in files.items():
        if not isinstance(body, str):
            continue
        normalized = path.replace("\\", "/").lower()
        if normalized.endswith(_VISUAL_EXTENSIONS):
            chunks.append(body)
    return "\n".join(chunks)


def scan(files: dict[str, str]) -> DepthExperienceReport:
    """Return depth evidence for a generated visual surface."""

    source = _visual_source(files)
    if not source or not _VISUAL_SURFACE.search(source):
        return DepthExperienceReport(judged=False, passed=True)

    managed_runtime = any(
        path.replace("\\", "/").lower().endswith("omnia-depth.js")
        for path in files
    ) or "omnia-depth.js" in source
    if _MANAGED_DEPTH.search(source) and managed_runtime:
        return DepthExperienceReport(judged=True, passed=True, kind="omnia-webgl")
    if _WEBGL.search(source):
        return DepthExperienceReport(judged=True, passed=True, kind="webgl")
    if _CANVAS.search(source) and _ANIMATION.search(source):
        return DepthExperienceReport(judged=True, passed=True, kind="animated-canvas")
    if _MEDIA.search(source) and _LAYER.search(source) and _INPUT.search(source):
        return DepthExperienceReport(judged=True, passed=True, kind="layered-media")

    classes = [NO_DEPTH_EXPERIENCE]
    if _SVG.search(source) and _PARALLAX_ONLY.search(source):
        classes.append(FLAT_SVG_PSEUDO_3D)
    return DepthExperienceReport(
        judged=True,
        passed=False,
        classes=tuple(classes),
    )


def feedback(report: DepthExperienceReport) -> str:
    if report.passed or not report.judged:
        return ""
    return (
        "[глубина] Добавь один уместный интерактивный depth-момент: предпочтительно "
        "управляемый Omnia WebGL-хост `data-omnia-depth` с палитрой проекта; либо "
        "реальный WebGL/Three/canvas; либо фото/видео с несколькими слоями, CSS "
        "perspective/translateZ и реакцией на pointer/scroll. Обычный SVG, "
        "градиент и плоский parallax НЕ считаются 3D. Сохрани читаемый статичный "
        "fallback и отключай движение при prefers-reduced-motion."
    )


__all__ = [
    "FLAT_SVG_PSEUDO_3D",
    "NO_DEPTH_EXPERIENCE",
    "DepthExperienceReport",
    "feedback",
    "scan",
]
