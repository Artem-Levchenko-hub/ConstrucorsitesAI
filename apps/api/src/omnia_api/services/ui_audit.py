"""Objective HTML-quality audit (Phase H).

Implements the 10-point Malewicz Ch27 + Phase G rubric as deterministic,
LLM-free checks. Callers pass a ``{filename: html}`` dict and receive an
:class:`AuditReport` with score 0..10 and per-check :class:`Failure` list.

The 10 checks
=============

1. ``typography_count``        — major. ≤2 font families, ≤6 sizes, ≤4 weights.
2. ``interactive_sizes_consistency`` — major. Button/input heights in
   40–56 px range, ≤3 distinct values.
3. ``grid_alignment``          — minor. ≥80 % of section spacings on a
   single 8/12/16 base.
4. ``color_count``             — major. ≤8 unique hex colors.
5. ``gradient_discipline``     — minor. Gradients share one hue temperature.
6. ``button_rules``            — major. G4/G5/G6/G7 — asymmetric padding,
   min-height, single primary CTA.
7. ``icon_family_discipline``  — major. G9 — one icon family + uniform
   stroke-width.
8. ``accessibility``           — critical. ``alt`` on every ``<img>``;
   labelled or aria-labelled inputs. (WCAG contrast deferred.)
9. ``no_lorem_ipsum``          — critical. G12.
10. ``no_dark_patterns``       — major. G14 — no pre-checked opt-out toggles.

Each check produces 0 or 1 toward the score (severity is informative,
it doesn't weight the score — every check is one point). This keeps the
scoring boring and reportable; if we ever want weighted scoring we add it
later.

Scope OUT of this module
========================

* **Live baselining on real LLM generation** — costs $$ and needs a key.
  Build a separate runner that drives end-to-end generation, then feeds
  outputs here. This module is the measurement primitive only.
* **WCAG contrast computation** — needs a color-conversion library.
  Marked ``TODO(phase-h+)`` inside ``_check_accessibility``.
* **Golden-spec auditing** (palette_range, sections_present_min, etc.)
  lives in a separate ``golden_audit.py`` that diffs HTML against the
  per-template golden JSON. Out of scope here.

Design
======

The module is a leaf — no imports from routers / models / DB. Inputs are
strings, outputs are dataclasses. Single public function: ``audit``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from math import gcd

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    check_id: str
    severity: str  # "critical" | "major" | "minor"
    description: str
    evidence: str  # one-line snippet of the offending pattern


@dataclass(frozen=True)
class AuditReport:
    score: int
    max: int
    failures: tuple[Failure, ...]
    per_check: dict[str, bool]


# ---------------------------------------------------------------------------
# Regexes for structural extraction
# ---------------------------------------------------------------------------

# CSS / inline style ---------------------------------------------------------
_FONT_FAMILY_DECL = re.compile(
    r'font-family\s*:\s*([^;}"\n]+)', re.IGNORECASE
)
_FONT_SIZE_DECL = re.compile(
    r'font-size\s*:\s*([^;}"\n]+)', re.IGNORECASE
)
_FONT_WEIGHT_DECL = re.compile(
    r'font-weight\s*:\s*([^;}"\n]+)', re.IGNORECASE
)
_GOOGLE_FONTS_HREF = re.compile(
    r'<link[^>]+href\s*=\s*["\']([^"\']*fonts\.googleapis\.com[^"\']+)["\']',
    re.IGNORECASE,
)
_GOOGLE_FONTS_FAMILY = re.compile(r'family=([^&"\']+)', re.IGNORECASE)
_TAILWIND_FONT_FAMILY_CFG = re.compile(
    r'fontFamily\s*:\s*\{([^}]+)\}', re.IGNORECASE | re.DOTALL
)

# Tailwind utility classes that encode typography
_TW_TEXT_SIZE = re.compile(
    r'\btext-(xs|sm|base|lg|xl|2xl|3xl|4xl|5xl|6xl|7xl|8xl|9xl|\[[^\]]+\])\b'
)
# Font weight names that Tailwind ships
_TW_FONT_WEIGHT = re.compile(
    r'\bfont-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black)\b'
)

# Color extraction -----------------------------------------------------------
_HEX_COLOR = re.compile(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b')

# Gradients ------------------------------------------------------------------
_GRADIENT_DECL = re.compile(
    r'(linear-gradient|radial-gradient|conic-gradient)\s*\([^)]+\)',
    re.IGNORECASE,
)

# Button / interactive height extraction -------------------------------------
_TW_HEIGHT = re.compile(r'\bh-(\d+(?:\.\d+)?)\b')
# We track py-* on buttons as "vertical-size signal" — Tailwind py-3 ≈ 24px,
# combined with implicit text-baseline gives ~48px button height.
_TW_PY = re.compile(r'\bpy-(\d+(?:\.\d+)?)\b')
_TW_PX = re.compile(r'\bpx-(\d+(?:\.\d+)?)\b')
_TW_PT = re.compile(r'\bpt-(\d+(?:\.\d+)?)\b')
_TW_MT = re.compile(r'\bmt-(\d+(?:\.\d+)?)\b')

# Tailwind class snake -> px (Tailwind v4 default: 1 unit = 4px, but the
# spec asks for 16 px multiplier on section spacings. We keep the standard
# 4-px scale here and let the spacing check decide the base.)
_TW_PX_PER_UNIT = 4

# Icon family heuristics -----------------------------------------------------
_ICON_FAM_TOKENS = {
    "fontawesome": re.compile(r'\bfa[srlb]?-[a-z0-9-]+\b'),
    "lucide": re.compile(r'\blucide(?:-[a-z0-9-]+)?\b', re.IGNORECASE),
    "heroicons": re.compile(r'\bheroicon(?:s)?(?:-[a-z0-9-]+)?\b', re.IGNORECASE),
    "feather": re.compile(r'\bfeather(?:-[a-z0-9-]+)?\b', re.IGNORECASE),
    "phosphor": re.compile(r'\bph(?:osphor)?-[a-z0-9-]+\b', re.IGNORECASE),
}

# SVG stroke widths
_SVG_TAG = re.compile(r'<svg\b([^>]*)>', re.IGNORECASE)
_STROKE_WIDTH = re.compile(r'stroke-width\s*=\s*["\']?([0-9.]+)["\']?', re.IGNORECASE)

# Lorem / placeholder text ---------------------------------------------------
_LOREM = re.compile(
    r"(lorem\s+ipsum|consectetur\s+adipisc|dolor\s+sit\s+amet|"
    r"пример\s+текста|ваш\s+текст|заголовок\s+\d|текст\s+\d|"
    r"placeholder\s+text|sample\s+text)",
    re.IGNORECASE,
)

# Dark-pattern toggles -------------------------------------------------------
_DARK_OPTOUT_KEYWORDS = (
    "opt-out", "opt out", "unsubscribe", "don't subscribe", "do not send",
    "do not subscribe", "no thanks", "выкл", "не подписыв", "отказаться",
)


# ---------------------------------------------------------------------------
# Lightweight HTML walker
# ---------------------------------------------------------------------------


class _Collector(HTMLParser):
    """Single-pass walker that pulls out tags we care about.

    Keeps per-tag attribute lists and accumulates body text. We don't try
    to parse fully into a DOM — checks operate on the flat attribute lists.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.buttons: list[dict[str, str]] = []
        self.anchors_with_btn: list[dict[str, str]] = []
        self.inputs: list[dict[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.labels: list[dict[str, str]] = []
        self.svgs: list[dict[str, str]] = []
        self.icon_tags: list[dict[str, str]] = []  # <i class="fa-...">
        self.checkbox_inputs: list[dict[str, str]] = []
        # Section-like containers: <section>, <main>, <div class="container">,
        # plus typical landing wrappers. We snapshot their classes to extract
        # py-/pt-/mt- spacing.
        self.sections: list[dict[str, str]] = []
        # Inline <style> bodies for css-decl parsing
        self.style_bodies: list[str] = []
        self._in_style = False
        self._style_buf: list[str] = []
        self.text_parts: list[str] = []
        # Tailwind/font config blocks (in <script type=text/tailwindcss> or JSON)
        self.script_bodies: list[str] = []
        self._in_script = False
        self._script_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "style":
            self._in_style = True
            self._style_buf = []
            return
        if tag == "script":
            self._in_script = True
            self._script_buf = []
            return
        if tag == "button":
            self.buttons.append(a)
        elif tag == "a":
            cls = a.get("class", "")
            if cls and ("btn" in cls.lower()):
                self.anchors_with_btn.append(a)
        elif tag == "input":
            self.inputs.append(a)
            if a.get("type", "").lower() == "checkbox":
                self.checkbox_inputs.append(a)
        elif tag == "img":
            self.images.append(a)
        elif tag == "label":
            self.labels.append(a)
        elif tag == "svg":
            self.svgs.append(a)
        elif tag == "i":
            self.icon_tags.append(a)
        elif tag in ("section", "main", "header", "footer", "div", "article"):
            self.sections.append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._in_style:
            self.style_bodies.append("".join(self._style_buf))
            self._style_buf = []
            self._in_style = False
        elif tag == "script" and self._in_script:
            self.script_bodies.append("".join(self._script_buf))
            self._script_buf = []
            self._in_script = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Void tags like <img/>, <input/>
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._style_buf.append(data)
            return
        if self._in_script:
            self._script_buf.append(data)
            return
        stripped = data.strip()
        if stripped:
            self.text_parts.append(stripped)


# ---------------------------------------------------------------------------
# Style parser (public for tests / external consumers)
# ---------------------------------------------------------------------------


def parse_styles(html: str, css: str = "") -> dict:
    """Extract structural style data from html + optional standalone css.

    Returns a dict with the keys documented in the module docstring. Be
    liberal — false-positives in extraction cause stricter checks (= safer
    bias toward lower scores on suspicious input).
    """
    walker = _Collector()
    try:
        walker.feed(html)
        walker.close()
    except Exception:  # pragma: no cover — html.parser is forgiving but be safe
        pass

    style_pool = "\n".join(walker.style_bodies) + "\n" + css
    script_pool = "\n".join(walker.script_bodies)

    # --- Font families -----------------------------------------------------
    font_families: set[str] = set()

    def _normalize_family(raw: str) -> set[str]:
        out: set[str] = set()
        for chunk in raw.split(","):
            name = chunk.strip().strip("'\"").strip()
            if not name:
                continue
            # Skip CSS generic fallbacks — they don't count as "a font choice".
            low = name.lower()
            if low in {"sans-serif", "serif", "monospace", "cursive", "fantasy",
                       "system-ui", "-apple-system", "blinkmacsystemfont", "inherit",
                       "initial", "unset", "ui-sans-serif", "ui-serif",
                       "ui-monospace", "ui-rounded", "emoji", "math", "fangsong"}:
                continue
            out.add(name)
        return out

    for decl in _FONT_FAMILY_DECL.findall(style_pool):
        font_families |= _normalize_family(decl)

    # Inline style="font-family: ..." on any element
    for inline in re.findall(r'style\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        for m in _FONT_FAMILY_DECL.findall(inline):
            font_families |= _normalize_family(m)

    # Google Fonts links: <link href="...family=Inter:wght@400&family=Playfair">
    for href in _GOOGLE_FONTS_HREF.findall(html):
        for fam_raw in _GOOGLE_FONTS_FAMILY.findall(href):
            name = fam_raw.split(":")[0].replace("+", " ").strip()
            if name:
                font_families.add(name)

    # Tailwind config blocks: fontFamily: { sans: [...], serif: [...] }.
    # We don't care about the alias keys (sans/serif/display) — we want the
    # font name strings inside the value arrays. Capitalised quoted tokens
    # are a reasonable proxy for "real font name" (Inter, Playfair, JetBrains
    # Mono) and avoid grabbing CSS fallbacks like "sans-serif".
    for cfg in _TAILWIND_FONT_FAMILY_CFG.findall(script_pool + style_pool + html):
        for fam_name in re.findall(r'["\']([A-Z][A-Za-z][A-Za-z0-9 +-]+)["\']', cfg):
            if fam_name.strip() and fam_name.strip().lower() not in (
                "sans", "serif", "mono", "display"
            ):
                font_families.add(fam_name.strip())

    # --- Font sizes --------------------------------------------------------
    font_sizes: set[str] = set()
    for decl in _FONT_SIZE_DECL.findall(style_pool):
        font_sizes.add(decl.strip().lower())
    for inline in re.findall(r'style\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        for m in _FONT_SIZE_DECL.findall(inline):
            font_sizes.add(m.strip().lower())
    for m in _TW_TEXT_SIZE.findall(html):
        font_sizes.add(f"text-{m}")

    # --- Font weights ------------------------------------------------------
    font_weights: set[str] = set()
    for decl in _FONT_WEIGHT_DECL.findall(style_pool):
        font_weights.add(decl.strip().lower())
    for inline in re.findall(r'style\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        for m in _FONT_WEIGHT_DECL.findall(inline):
            font_weights.add(m.strip().lower())
    for m in _TW_FONT_WEIGHT.findall(html):
        font_weights.add(f"font-{m}")

    # --- Colors ------------------------------------------------------------
    colors: set[str] = set()
    for hex_val in _HEX_COLOR.findall(style_pool + " " + html + " " + css):
        h = hex_val.lower()
        if len(h) == 3:
            # Expand #abc -> #aabbcc so we don't double-count short vs long forms
            h = "".join(c * 2 for c in h)
        colors.add(h)

    # --- Gradients ---------------------------------------------------------
    gradients: list[str] = []
    for g in _GRADIENT_DECL.findall(style_pool + " " + html + " " + css):
        gradients.append(g)
    # Also grab full gradient declarations (with their argument list) for hue
    # analysis. The findall above just gets the function-name token in some
    # regex configurations; re-scan with a more forgiving pattern:
    gradients = re.findall(
        r'(?:linear-gradient|radial-gradient|conic-gradient)\s*\([^)]+\)',
        style_pool + " " + html + " " + css,
        re.IGNORECASE,
    )

    # --- Button classes ----------------------------------------------------
    button_classes: list[str] = []
    for b in walker.buttons:
        cls = b.get("class", "").strip()
        if cls:
            button_classes.append(cls)
    for a in walker.anchors_with_btn:
        cls = a.get("class", "").strip()
        if cls:
            button_classes.append(cls)

    # --- Input heights -----------------------------------------------------
    input_heights: list[str] = []
    for inp in walker.inputs:
        cls = inp.get("class", "")
        for m in _TW_HEIGHT.findall(cls):
            input_heights.append(f"h-{m}")
        for m in _TW_PY.findall(cls):
            input_heights.append(f"py-{m}")
    for b in walker.buttons + walker.anchors_with_btn:
        cls = b.get("class", "")
        for m in _TW_HEIGHT.findall(cls):
            input_heights.append(f"h-{m}")

    # --- Icon families -----------------------------------------------------
    icon_families: set[str] = set()
    # Scan body of html for icon family tokens
    body_for_icons = html
    for fam_name, pat in _ICON_FAM_TOKENS.items():
        if pat.search(body_for_icons):
            icon_families.add(fam_name)
    # SVG presence — if any <svg> exists and no library token, call it "svg"
    if walker.svgs and not icon_families:
        icon_families.add("svg")

    # --- Section spacings (in px) ------------------------------------------
    section_spacings: list[int] = []
    for sec in walker.sections:
        cls = sec.get("class", "")
        for unit in _TW_PY.findall(cls) + _TW_PT.findall(cls) + _TW_MT.findall(cls):
            try:
                section_spacings.append(int(float(unit) * _TW_PX_PER_UNIT))
            except ValueError:
                pass

    # --- Prose text --------------------------------------------------------
    prose_text = " ".join(walker.text_parts)

    # --- Dark-pattern checkboxes ------------------------------------------
    toggles_optout: list[str] = []
    # Build a label map: <label for="x">text</label>
    labels_by_for: dict[str, str] = {}
    # We don't capture label inner text in the walker; do a regex sweep instead.
    for m in re.finditer(
        r'<label\b[^>]*\bfor\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</label>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        labels_by_for[m.group(1)] = re.sub(r'<[^>]+>', '', m.group(2)).strip()

    for cb in walker.checkbox_inputs:
        is_checked = "checked" in cb  # presence of attr = checked
        if not is_checked:
            continue
        # Get label text — by id, then by aria-label/placeholder
        text = labels_by_for.get(cb.get("id", ""), "") or cb.get("aria-label", "") or \
            cb.get("placeholder", "") or cb.get("value", "")
        low = text.lower()
        if any(kw in low for kw in _DARK_OPTOUT_KEYWORDS):
            toggles_optout.append(text)

    return {
        "font_families": font_families,
        "font_sizes": font_sizes,
        "font_weights": font_weights,
        "colors": colors,
        "gradients": gradients,
        "button_classes": button_classes,
        "input_heights": input_heights,
        "icon_families": icon_families,
        "section_spacings": section_spacings,
        "prose_text": prose_text,
        "toggles_optout": toggles_optout,
        # Internal — checks lean on these too
        "_buttons": walker.buttons,
        "_anchors_btn": walker.anchors_with_btn,
        "_inputs": walker.inputs,
        "_labels": walker.labels,
        "_svgs": walker.svgs,
        "_images": walker.images,
    }


# ---------------------------------------------------------------------------
# Individual checks — each returns None on pass, Failure on fail
# ---------------------------------------------------------------------------


def _check_typography_count(parsed: dict, html_files: dict) -> Failure | None:
    fams = parsed["font_families"]
    sizes = parsed["font_sizes"]
    weights = parsed["font_weights"]
    problems = []
    if len(fams) > 2:
        problems.append(f"{len(fams)} font families")
    if len(sizes) > 6:
        problems.append(f"{len(sizes)} font sizes")
    if len(weights) > 4:
        problems.append(f"{len(weights)} font weights")
    if problems:
        return Failure(
            check_id="typography_count",
            severity="major",
            description=(
                "Typography exceeds Malewicz scale: max 2 families, 6 sizes, "
                "4 weights."
            ),
            evidence=(
                f"families={sorted(fams)[:5]} sizes={sorted(sizes)[:8]} "
                f"weights={sorted(weights)[:6]}"
            ),
        )
    return None


def _heights_in_px(parsed: dict) -> list[int]:
    """Collect button/input heights in px from h-N and py-N classes.

    Tailwind v4 default: 1 unit = 4 px. h-10 = 40 px. We add py-N values as
    pseudo-heights doubled (top + bottom + ~20 px text line). Keep it
    forgiving — false positives lower the score by one, which is safe.
    """
    out: list[int] = []
    for token in parsed["input_heights"]:
        m = re.match(r"h-(\d+(?:\.\d+)?)", token)
        if m:
            try:
                out.append(int(float(m.group(1)) * _TW_PX_PER_UNIT))
                continue
            except ValueError:
                pass
        m = re.match(r"py-(\d+(?:\.\d+)?)", token)
        if m:
            try:
                # py-N = N*4 px top + N*4 px bottom. Approximate baseline 20 px.
                out.append(int(float(m.group(1)) * _TW_PX_PER_UNIT * 2 + 20))
            except ValueError:
                pass
    # Also walk button classes for h-/py-
    for cls in parsed["button_classes"]:
        for m in _TW_HEIGHT.findall(cls):
            try:
                out.append(int(float(m) * _TW_PX_PER_UNIT))
            except ValueError:
                pass
        for m in _TW_PY.findall(cls):
            try:
                out.append(int(float(m) * _TW_PX_PER_UNIT * 2 + 20))
            except ValueError:
                pass
    return out


def _check_interactive_sizes_consistency(parsed: dict, html_files: dict) -> Failure | None:
    heights = _heights_in_px(parsed)
    if not heights:
        # No interactive elements measured — vacuously pass. (A page with no
        # buttons isn't getting penalised for "size inconsistency" — other
        # checks will catch missing CTAs.)
        return None
    distinct = sorted(set(heights))
    too_short = [h for h in distinct if h < 40]
    too_tall = [h for h in distinct if h > 56]
    problems = []
    if len(distinct) > 3:
        problems.append(f"{len(distinct)} distinct heights")
    if too_short:
        problems.append(f"below 40 px: {too_short}")
    if too_tall:
        problems.append(f"above 56 px: {too_tall}")
    if problems:
        return Failure(
            check_id="interactive_sizes_consistency",
            severity="major",
            description=(
                "Buttons/inputs must share a height system in 40–56 px range, "
                "max 3 distinct values."
            ),
            evidence=f"distinct={distinct} issues={problems}",
        )
    return None


def _check_grid_alignment(parsed: dict, html_files: dict) -> Failure | None:
    spacings = parsed["section_spacings"]
    if len(spacings) < 3:
        # Too little signal — skip (pass).
        return None
    # Find a base that ≥ 80% of values share.
    for base in (8, 12, 16):
        on_grid = sum(1 for s in spacings if s % base == 0)
        if on_grid / len(spacings) >= 0.8:
            return None
    # Try GCD of the dominant values as a fallback base.
    g = spacings[0]
    for s in spacings[1:]:
        g = gcd(g, s)
    if g >= 4:
        on_grid = sum(1 for s in spacings if s % g == 0)
        if on_grid / len(spacings) >= 0.8:
            return None
    return Failure(
        check_id="grid_alignment",
        severity="minor",
        description=(
            "≥80 % of section spacings should align to a single base "
            "(8/12/16 px)."
        ),
        evidence=f"spacings={sorted(set(spacings))[:12]}",
    )


def _check_color_count(parsed: dict, html_files: dict) -> Failure | None:
    colors = parsed["colors"]
    if len(colors) > 8:
        return Failure(
            check_id="color_count",
            severity="major",
            description="Palette over budget: ≤8 unique hex colors per page.",
            evidence=f"count={len(colors)} sample={sorted(colors)[:12]}",
        )
    return None


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _temperature_of_hex(hex_val: str) -> str:
    """Classify a hex color as warm / cool / neutral.

    Heuristic: convert to HSL hue and bucket. Saturation < 10 % = neutral.
    """
    r, g, b = _hex_to_rgb(hex_val)
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2 / 255
    if mx == mn:
        return "neutral"
    d = (mx - mn) / 255
    s = d / (1 - abs(2 * l - 1)) if (1 - abs(2 * l - 1)) > 0 else 0
    if s < 0.15:
        return "neutral"
    rr, gg, bb = r / 255, g / 255, b / 255
    mx_n, mn_n = max(rr, gg, bb), min(rr, gg, bb)
    delta = mx_n - mn_n
    if delta == 0:
        return "neutral"
    if mx_n == rr:
        h = ((gg - bb) / delta) % 6
    elif mx_n == gg:
        h = (bb - rr) / delta + 2
    else:
        h = (rr - gg) / delta + 4
    h *= 60
    if h < 0:
        h += 360
    # Hue buckets: warm = red/orange/yellow (0–60 + 300–360), cool = green/cyan/blue/purple (60–300)
    # Note: green (60–180) is technically cool-ish; treat 60–90 as borderline neutral.
    if 0 <= h < 60 or h >= 300:
        return "warm"
    if 60 <= h < 90:
        return "neutral"
    return "cool"


def _check_gradient_discipline(parsed: dict, html_files: dict) -> Failure | None:
    gradients = parsed["gradients"]
    if not gradients:
        return None
    temps_used: set[str] = set()
    for g_decl in gradients:
        stops = _HEX_COLOR.findall(g_decl)
        for stop in stops:
            h = stop.lower()
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            t = _temperature_of_hex("#" + h)
            if t != "neutral":
                temps_used.add(t)
    # Warm + cool mixed in same page => fail
    if "warm" in temps_used and "cool" in temps_used:
        return Failure(
            check_id="gradient_discipline",
            severity="minor",
            description=(
                "Gradients must share one hue temperature (all warm OR all "
                "cool OR all neutral). Mixed warm+cool reads as visual noise."
            ),
            evidence=f"gradients={len(gradients)} temps={sorted(temps_used)}",
        )
    return None


def _check_button_rules(parsed: dict, html_files: dict) -> Failure | None:
    btns = parsed["_buttons"] + parsed["_anchors_btn"]
    if not btns:
        # No buttons => vacuously pass. Pages without CTAs get flagged by
        # other checks (and golden-spec audit, out of scope here).
        return None

    problems: list[str] = []

    # Per-button structural checks
    for b in btns:
        cls = b.get("class", "")
        # Asymmetric padding: px >= 2 * py is the Malewicz default
        px_vals = [float(x) for x in _TW_PX.findall(cls)]
        py_vals = [float(x) for x in _TW_PY.findall(cls)]
        if px_vals and py_vals:
            if max(px_vals) < 2 * min(py_vals):
                problems.append(
                    f'symmetric padding in "{cls[:60]}" '
                    f'(px={px_vals} py={py_vals})'
                )
        # Min-height: h-10+ OR py-3+ (gives ~44 px button)
        h_vals = [float(x) for x in _TW_HEIGHT.findall(cls)]
        if h_vals and max(h_vals) < 10:
            problems.append(f'button below h-10 in "{cls[:60]}"')
        elif not h_vals:
            # No explicit h — require py-3+ as proxy
            if py_vals and max(py_vals) < 2:
                problems.append(f'button vertical padding too small in "{cls[:60]}"')

    # Visual hierarchy: ONE primary-CTA *style* per page, even if it appears
    # multiple times. Hero "Start" + form "Subscribe" both wearing
    # `bg-accent text-white` is fine (same brand CTA, reinforced). Mixing
    # `bg-accent` on one button + `bg-primary` on another = two competing
    # primaries → fail.
    primary_tokens = ("bg-accent", "accent-bg", "btn-primary", "primary-cta",
                      "bg-primary")
    primary_token_hits: set[str] = set()
    for b in btns:
        cls = b.get("class", "").lower()
        for tok in primary_tokens:
            if tok in cls:
                primary_token_hits.add(tok)
    if len(primary_token_hits) > 1:
        problems.append(
            f"multiple primary-CTA styles compete: {sorted(primary_token_hits)}"
        )

    if problems:
        return Failure(
            check_id="button_rules",
            severity="major",
            description=(
                "Buttons violate Malewicz G4/G5/G6/G7: asymmetric padding "
                "(px ≥ 2×py), min-height 44 px, exactly one primary CTA."
            ),
            evidence=" | ".join(problems[:4]),
        )
    return None


def _check_icon_family_discipline(parsed: dict, html_files: dict) -> Failure | None:
    families = parsed["icon_families"]
    svgs = parsed["_svgs"]

    # >1 named family in the same page = visual noise (G9)
    named = families - {"svg"}
    if len(named) > 1:
        return Failure(
            check_id="icon_family_discipline",
            severity="major",
            description="One icon family per page (G9).",
            evidence=f"families={sorted(named)}",
        )

    # Mixed stroke-widths across <svg> elements
    if svgs:
        strokes: set[float] = set()
        unstroked = 0
        for s in svgs:
            sw = s.get("stroke-width")
            if sw:
                try:
                    strokes.add(float(sw))
                except ValueError:
                    pass
            else:
                # No stroke-width attr — could be filled icon. Track separately.
                unstroked += 1
        # If we have both stroked and unstroked icons in the same page, that's
        # a mix. If only stroked, more than one width is a mix.
        if len(strokes) > 1:
            return Failure(
                check_id="icon_family_discipline",
                severity="major",
                description="All <svg> icons must share one stroke-width (G9).",
                evidence=f"stroke_widths={sorted(strokes)}",
            )

    return None


def _check_accessibility(parsed: dict, html_files: dict) -> Failure | None:
    # TODO(phase-h+): wcag contrast — needs color-conversion library.
    problems: list[str] = []

    # <img> must have alt=
    for img in parsed["_images"]:
        if "alt" not in img:
            problems.append(f'<img src="{img.get("src", "?")[:50]}"> missing alt')

    # <input> must have label OR aria-label OR placeholder
    # Build label map from html (any file)
    labelled_ids: set[str] = set()
    for content in html_files.values():
        for m in re.finditer(
            r'<label\b[^>]*\bfor\s*=\s*["\']([^"\']+)["\']',
            content,
            re.IGNORECASE,
        ):
            labelled_ids.add(m.group(1))

    for inp in parsed["_inputs"]:
        inp_id = inp.get("id", "")
        if inp.get("type", "").lower() in {"hidden", "submit", "button"}:
            continue
        labelled = inp_id and inp_id in labelled_ids
        has_aria = bool(inp.get("aria-label"))
        has_placeholder = bool(inp.get("placeholder"))
        if not (labelled or has_aria or has_placeholder):
            problems.append(
                f'<input id="{inp_id or "?"}" type="{inp.get("type", "?")}"> '
                f'has no label/aria-label/placeholder'
            )

    if problems:
        return Failure(
            check_id="accessibility",
            severity="critical",
            description=(
                "Missing alt-text on images or label/aria/placeholder on "
                "inputs."
            ),
            evidence=" | ".join(problems[:4]),
        )
    return None


def _check_no_lorem_ipsum(parsed: dict, html_files: dict) -> Failure | None:
    for path, content in html_files.items():
        # Strip tags for a cleaner text scan
        stripped = re.sub(r'<[^>]+>', ' ', content)
        m = _LOREM.search(stripped)
        if m:
            return Failure(
                check_id="no_lorem_ipsum",
                severity="critical",
                description=(
                    "Placeholder text leaked into the output (Lorem ipsum or "
                    "Russian equivalent). G12 violation."
                ),
                evidence=f"{path}: …{stripped[max(0, m.start()-20):m.end()+20]}…",
            )
    return None


def _check_no_dark_patterns(parsed: dict, html_files: dict) -> Failure | None:
    if parsed["toggles_optout"]:
        return Failure(
            check_id="no_dark_patterns",
            severity="major",
            description=(
                "Pre-checked opt-out toggle detected — dark pattern (G14). "
                "Defaults must be opt-in."
            ),
            evidence=f"labels={parsed['toggles_optout'][:3]}",
        )
    return None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


_CheckFunc = Callable[[dict, dict[str, str]], "Failure | None"]

_CHECK_FUNCS: tuple[tuple[str, _CheckFunc], ...] = (
    ("typography_count", _check_typography_count),
    ("interactive_sizes_consistency", _check_interactive_sizes_consistency),
    ("grid_alignment", _check_grid_alignment),
    ("color_count", _check_color_count),
    ("gradient_discipline", _check_gradient_discipline),
    ("button_rules", _check_button_rules),
    ("icon_family_discipline", _check_icon_family_discipline),
    ("accessibility", _check_accessibility),
    ("no_lorem_ipsum", _check_no_lorem_ipsum),
    ("no_dark_patterns", _check_no_dark_patterns),
)


def format_failures_for_retry(report: AuditReport) -> str:
    """Build a Russian retry-feedback message from a low-score AuditReport.

    Used by Phase L4 (audit-driven retry loop): when the first generation
    pass produces ``score < threshold``, this string is appended to the
    next LLM call as a user message so the model knows exactly what to
    fix in its second attempt.

    Output format is plain-text bullet list — easy for the model to act
    on without re-reading the system prompt. We deliberately quote
    ``evidence`` snippets so the model sees the actual offending
    classes/values, not a generic "your buttons are bad" summary.
    """
    if not report.failures:
        return ""

    lines = [
        f"Предыдущий ответ не прошёл объективную проверку дизайна "
        f"(score={report.score}/{report.max}). Конкретные нарушения:",
    ]
    severity_emoji = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
    for f in report.failures:
        prefix = severity_emoji.get(f.severity, "•")
        lines.append(f"{prefix} [{f.check_id}] {f.description}")
        if f.evidence:
            lines.append(f"   Найдено: {f.evidence[:200]}")
    lines.append(
        "\nИсправь только перечисленные проблемы и верни новый PageIR JSON. "
        "Не меняй секции / структуру / контент которые НЕ упомянуты в "
        "нарушениях — они корректны."
    )
    return "\n".join(lines)


def audit(html_files: dict[str, str]) -> AuditReport:
    """Run all 10 checks on the combined HTML+CSS pool.

    ``html_files`` is ``{filename: content}``. Structural checks
    (typography, colors, etc.) operate on the *concatenated* pool — a 3-file
    page is one design system. Per-file checks (Lorem, dark-pattern toggles)
    iterate the files individually.

    Returns a fully-populated :class:`AuditReport`.
    """
    # Concat for structural extraction
    combined_html = "\n".join(html_files.values())
    parsed = parse_styles(combined_html)

    per_check: dict[str, bool] = {}
    failures: list[Failure] = []
    for check_id, func in _CHECK_FUNCS:
        result = func(parsed, html_files)
        passed = result is None
        per_check[check_id] = passed
        if not passed:
            failures.append(result)

    score = sum(1 for v in per_check.values() if v)
    return AuditReport(
        score=score,
        max=len(_CHECK_FUNCS),
        failures=tuple(failures),
        per_check=per_check,
    )
