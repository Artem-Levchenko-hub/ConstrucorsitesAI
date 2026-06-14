"""Chip→Pixel fidelity gate — request↔output truth check (V1.6 slice 4/5).

Closes the gap on pillar 2 of the North Star: the *magic of live render* is only
magic if what the user **picked in onboarding actually shows up in the pixels**.
Where ``wow_dom_gate`` asks "is this page objectively good?" and ``perf_a11y_gate``
asks "is it fast and usable?", this gate asks the one question neither can:
**did the rendered page honour the discovery answers?** Pick *dark + violet,
sections [каталог, отзывы, контакты], tone playful* → the gate asserts the painted
page is dark, its accent is in the violet hue family, those three sections exist
as anchors, and (if a tone is declared) it matches. Swap an answer → the verdict
flips. A mismatch is a FAIL — a beautiful page that ignored the brief is a lie.

Design — **JS extracts, Python scores** (R-01 deep module, same split as 2/5 & 3/5)
==================================================================================
The injected ``_FIDELITY_JS`` does *only* DOM extraction: the painted page
background, the dominant CTA fill colour, every section anchor (id / nav-hash /
heading text), and any declared ``data-omnia-tone`` marker. Every threshold, hue
band, keyword set and verdict lives in pure Python (:func:`evaluate_fidelity` and
helpers), so the whole gate is unit-testable with a hand-built dict — no browser,
no LLM, no flake. The async :func:`audit_files` / :func:`audit_url` wrappers are
the only browser-touching code and fail soft (R-10): a render error yields
``rendered=False`` (the gate abstains) rather than raising into the caller.

Why the **painted CTA fill**, not ``--primary``?
================================================
"Chip→**pixel**" — we judge the colour the user's eye lands on, not a stylesheet
token. Custom-property formats vary wildly (hex, ``rgb()``, shadcn HSL channel
triplets ``263 70% 50%``) and a token can be declared yet never painted. The
largest saturated CTA fill is the rendered accent, always an ``rgb()`` — robust
and on-philosophy. The ``--primary`` var is a fallback only.

Why tone **abstains** when undeclared
=====================================
Page *tone* (playful / strict / premium) is prose, not a reliable CSS signal —
guessing it from copy would be a flaky vibe-check, the exact anti-pattern the
ratchet exists to kill. So the tone axis reads an explicit ``data-omnia-tone``
marker: declared-but-wrong → FAIL (and a swap flips it); declared-and-right →
PASS; **absent → ABSTAIN** (no finding). When the generator starts emitting the
marker the axis gains full teeth for free; until then it never false-fails.
(Note: the ``.tone-warm`` / ``.tone-cool`` classes are *photo colour-grading*,
NOT page tone, and are deliberately ignored here.)
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render_settle import goto_and_settle
from .wow_dom_gate import (
    GATE_HEIGHT,
    GATE_WIDTH,
    Rgb,
    relative_luminance,
    rgb_to_hsl,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_FIDELITY_JS`` and scored by Python.
Obs = dict[str, Any]

# ── tunables (the testable thresholds) ────────────────────────────────────────

# A surface is "dark" below this WCAG relative luminance, "light" above the
# bright floor. The gap is a no-man's-land: a dark request must land clearly dark
# and a light request clearly light, else the palette intent wasn't honoured.
_DARK_LUM_MAX = 0.18
_LIGHT_LUM_MIN = 0.50
# A CTA fill counts as the accent only when it is clearly saturated and
# mid-luminance — never a near-black/white/grey tint (mirrors wow_dom_gate).
_ACCENT_MIN_SATURATION = 0.30
_ACCENT_MIN_LIGHTNESS = 0.12
_ACCENT_MAX_LIGHTNESS = 0.92
_ACCENT_MIN_ALPHA = 0.5

# Check ids — the vocabulary of the subscore.
PALETTE_BG = "palette-bg"
PRIMARY_FAMILY = "primary-family"
SECTION_ANCHOR = "section-anchor"
TONE_MARKER = "tone-marker"

CHECKS: tuple[str, ...] = (PALETTE_BG, PRIMARY_FAMILY, SECTION_ANCHOR, TONE_MARKER)

# Hue families on the 0..360 wheel, as inclusive ranges (a couple wrap past 360).
# Bands are wide enough that two shades of one brand colour read as one family,
# narrow enough that a different brand hue is a different family. ``#A855F7``
# (the seeded "Violet" accent) sits at hue ≈271° → squarely inside ``violet``.
_FAMILY_BANDS: dict[str, tuple[tuple[float, float], ...]] = {
    "red": ((345.0, 360.0), (0.0, 12.0)),
    "orange": ((12.0, 45.0),),
    "amber": ((40.0, 65.0),),
    "yellow": ((50.0, 70.0),),
    "green": ((75.0, 160.0),),
    "emerald": ((130.0, 175.0),),
    "teal": ((165.0, 200.0),),
    "cyan": ((180.0, 205.0),),
    "blue": ((200.0, 250.0),),
    "indigo": ((240.0, 262.0),),
    "violet": ((255.0, 300.0),),
    "purple": ((265.0, 305.0),),
    "magenta": ((300.0, 330.0),),
    "pink": ((320.0, 348.0),),
}
# Free-text palette words → canonical family key (the chip vocabulary, RU + EN).
_FAMILY_ALIASES: dict[str, str] = {
    "фиолет": "violet", "фиолетовый": "violet", "лиловый": "violet", "violet": "violet",
    "пурпур": "purple", "пурпурный": "purple", "purple": "purple",
    "индиго": "indigo", "indigo": "indigo",
    "синий": "blue", "голубой": "blue", "blue": "blue",
    "бирюз": "teal", "бирюзовый": "teal", "teal": "teal", "cyan": "cyan",
    "зелёный": "green", "зеленый": "green", "green": "green",
    "изумруд": "emerald", "изумрудный": "emerald", "emerald": "emerald",
    "красный": "red", "red": "red",
    "оранж": "orange", "оранжевый": "orange", "orange": "orange",
    "янтар": "amber", "amber": "amber",
    "жёлтый": "yellow", "желтый": "yellow", "yellow": "yellow",
    "розовый": "pink", "pink": "pink",
    "малиновый": "magenta", "magenta": "magenta", "fuchsia": "magenta",
}

# Representative HEX per family — the swatch the generation-side directive hands
# the writer so a chip-picked palette lands ON the family band the render-time
# gate (``family_of_hue`` below) reads back. Each value is hand-tuned to sit at
# the CENTRE of its family's exclusive sub-range (bands overlap; first match by
# insertion order wins), so a writer that uses exactly this HEX passes
# ``PRIMARY_FAMILY`` — closing the loop "writer honours spec → gate agrees".
# CTA swatches (vivid, mid-lightness), not full palettes — the writer derives
# shades around the family. R-04: the ONE source both prompt builders read.
_FAMILY_HEX: dict[str, str] = {
    "red": "#DA493E",
    "orange": "#DA8A3E",
    "amber": "#DACF3E",
    "yellow": "#C5DA3E",
    "green": "#45DA3E",
    "emerald": "#3EDABA",
    "teal": "#3EC7DA",
    "cyan": "#3E9EDA",
    "blue": "#3E5FDA",
    "indigo": "#683EDA",
    "violet": "#AA3EDA",
    "purple": "#DA3ED2",
    "magenta": "#DA3EAB",
    "pink": "#DA3E79",
}

# Canonical section → keyword sets. ``id``/nav-hash are matched against the EN +
# translit set; visible headings against the RU + EN set. A section counts as
# present if ANY signal hits — an authored anchor, a nav link, or a heading.
_SECTION_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "catalog": {
        "anchor": ("catalog", "products", "shop", "menu", "katalog", "tovary", "uslugi", "store"),
        "heading": (
            "каталог", "товары", "продукты", "меню", "услуги", "ассортимент",
            "catalog", "products",
        ),
    },
    "testimonials": {
        "anchor": ("testimonial", "reviews", "review", "otzyv", "feedback"),
        "heading": ("отзыв", "отзывы", "что говорят", "reviews", "testimonial"),
    },
    "contacts": {
        "anchor": ("contact", "contacts", "kontakt"),
        "heading": ("контакт", "связаться", "свяж", "напишите нам", "contact"),
    },
    "pricing": {
        "anchor": ("pricing", "price", "tariff", "plans", "ceny", "tarif"),
        "heading": ("цены", "цена", "тариф", "тарифы", "стоимость", "pricing", "plans"),
    },
    "features": {
        "anchor": ("features", "benefits", "vozmozhnosti", "preimushchestva"),
        "heading": ("возможности", "преимущества", "почему мы", "features", "benefits"),
    },
    "faq": {
        "anchor": ("faq", "questions"),
        "heading": ("faq", "вопрос", "часто задаваемые", "q&a"),
    },
    "about": {
        "anchor": ("about", "about-us", "onas"),
        "heading": ("о нас", "о компании", "about"),
    },
    "gallery": {
        "anchor": ("gallery", "portfolio", "works", "galereya", "raboty"),
        "heading": ("галерея", "портфолио", "работы", "наши работы", "gallery", "portfolio"),
    },
}
# Free-text section words → canonical key, so scripted answers can be RU prose.
_SECTION_ALIASES: dict[str, str] = {
    "каталог": "catalog", "товары": "catalog", "продукты": "catalog", "меню": "catalog",
    "услуги": "catalog", "ассортимент": "catalog", "catalog": "catalog", "products": "catalog",
    "отзыв": "testimonials", "отзывы": "testimonials", "reviews": "testimonials",
    "testimonials": "testimonials",
    "контакт": "contacts", "контакты": "contacts", "contacts": "contacts", "contact": "contacts",
    "цены": "pricing", "тарифы": "pricing", "стоимость": "pricing", "pricing": "pricing",
    "возможности": "features", "преимущества": "features", "features": "features",
    "faq": "faq", "вопросы": "faq",
    "о нас": "about", "about": "about",
    "галерея": "gallery", "портфолио": "gallery", "работы": "gallery", "gallery": "gallery",
}


# ── the spec (reified scripted discovery answers) ─────────────────────────────


@dataclass(frozen=True)
class FidelitySpec:
    """What the user picked in onboarding, reified for a deterministic check.

    ``None`` / empty on an axis means "don't assert it" — the gate only judges
    answers that were actually given.
    """

    dark_mode: bool | None = None
    primary_family: str | None = None
    sections: tuple[str, ...] = ()
    tone: str | None = None

    @staticmethod
    def from_answers(
        palette: str | None = None,
        sections: str | list[str] | tuple[str, ...] | None = None,
        tone: str | None = None,
    ) -> FidelitySpec:
        """Parse scripted discovery answers into a spec.

        ``palette="dark + violet"`` → ``dark_mode=True, primary_family="violet"``;
        ``sections=["каталог","отзывы"]`` → canonical keys; ``tone="playful"`` →
        normalised tone token. Unknown words are ignored, never guessed.
        """
        dark: bool | None = None
        fam: str | None = None
        if palette:
            low = palette.lower()
            if re.search(r"тёмн|темн|dark|night|ноч", low):
                dark = True
            elif re.search(r"светл|light|day|белый|white", low):
                dark = False
            for word in re.findall(r"[a-zа-яё]+", low):
                hit = _FAMILY_ALIASES.get(word) or next(
                    (canon for alias, canon in _FAMILY_ALIASES.items() if word.startswith(alias)),
                    None,
                )
                if hit:
                    fam = hit
                    break
        secs = _canonical_sections(sections)
        return FidelitySpec(
            dark_mode=dark,
            primary_family=fam,
            sections=secs,
            tone=(tone.strip().lower() or None) if tone else None,
        )

    @property
    def is_empty(self) -> bool:
        """No axis carries an assertable answer — onboarding said nothing."""
        return (
            self.dark_mode is None
            and self.primary_family is None
            and not self.sections
            and self.tone is None
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for ``projects.discovery_spec`` (JSONB).

        Round-trips via :meth:`from_dict` — the persisted shape downstream gates
        read back when the gauntlet runs over a built project.
        """
        return {
            "dark_mode": self.dark_mode,
            "primary_family": self.primary_family,
            "sections": list(self.sections),
            "tone": self.tone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FidelitySpec:
        """Rebuild a spec from a persisted ``discovery_spec`` JSONB row.

        Inverse of :meth:`to_dict`. Defensive on purpose — a partial or legacy
        row (missing keys, ``sections`` stored as a bare list) reifies to an
        abstaining axis rather than raising, so a malformed row degrades to an
        empty spec (no assertion) instead of sinking the build (R-10).
        """
        if not data:
            return cls()
        secs = data.get("sections") or ()
        if isinstance(secs, str):
            secs = (secs,)
        return cls(
            dark_mode=data.get("dark_mode"),
            primary_family=data.get("primary_family"),
            sections=tuple(secs),
            tone=data.get("tone"),
        )


def _canonical_sections(
    sections: str | list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if not sections:
        return ()
    items = re.split(r"[,;/]| и | and ", sections) if isinstance(sections, str) else list(sections)
    out: list[str] = []
    for raw in items:
        word = _norm(raw)
        if not word:
            continue
        canon = _SECTION_ALIASES.get(word) or next(
            (c for alias, c in _SECTION_ALIASES.items() if alias in word), None
        )
        if canon and canon not in out:
            out.append(canon)
    return tuple(out)


# Tone words (chip labels / free text) → canonical tone token. Conservative on
# purpose: only an explicit tone word sets the axis, so an undecided onboarding
# leaves tone NULL rather than guessing from prose (same abstain discipline as
# the render-time tone gate). Substring match, first hit wins.
_TONE_ALIASES: tuple[tuple[str, str], ...] = (
    ("премиум", "premium"), ("premium", "premium"), ("люкс", "premium"), ("luxury", "premium"),
    ("дружелюб", "friendly"), ("friendly", "friendly"), ("тёпл", "friendly"), ("тепл", "friendly"),
    ("игрив", "playful"), ("playful", "playful"), ("весёл", "playful"), ("весел", "playful"),
    ("минимал", "minimal"), ("minimal", "minimal"), ("лаконичн", "minimal"), ("сдержан", "minimal"),
    ("строг", "corporate"), ("корпоратив", "corporate"), ("corporate", "corporate"),
    ("делов", "corporate"), ("официальн", "corporate"),
)


def _detect_tone(text: str | None) -> str | None:
    low = (text or "").lower()
    for alias, canon in _TONE_ALIASES:
        if alias in low:
            return canon
    return None


def spec_from_discovery(
    history: list[dict[str, str]] | None,
    latest_prompt: str | None = None,
) -> FidelitySpec | None:
    """Marshal raw discovery answers (chip taps + free text) into a spec.

    The user's onboarding turns — their chip taps and any "Другое" free text —
    are the source of truth for the design they steered toward. We gather every
    user-role turn plus the newest prompt and reify the palette / sections / tone
    signal through the same :meth:`FidelitySpec.from_answers` extractor the
    gauntlet uses (R-04 single source). Returns ``None`` when nothing assertable
    was said, so an undecided onboarding persists NULL rather than an empty spec.
    """
    parts: list[str] = []
    for m in history or []:
        if (m.get("role") or "") == "user":
            content = (m.get("content") or "").strip()
            if content:
                parts.append(content)
    if latest_prompt and latest_prompt.strip():
        parts.append(latest_prompt.strip())
    # Comma-join so multi-section answers split cleanly in _canonical_sections.
    answers = ", ".join(parts)
    if not answers:
        return None
    spec = FidelitySpec.from_answers(
        palette=answers, sections=answers, tone=_detect_tone(answers)
    )
    return None if spec.is_empty else spec


def compile_build_spec(prompt: str) -> FidelitySpec:
    """Reify a single raw build prompt into a :class:`FidelitySpec`, no chips, no LLM.

    The zero-question intent compiler (V2.12): the North Star's pillar 2 says the
    best onboarding is its *absence* when intent is already clear. A rich prompt
    like «тёмный минималистичный лендинг с каталогом и отзывами на фиолетовом»
    carries the same design decisions a chip interview would extract — so we read
    them straight from the text, deterministically, through the **same**
    :meth:`FidelitySpec.from_answers` / :func:`_detect_tone` extractors the chip
    flow and the render-time gate use (R-04 single source). No new parsing rules,
    no guessing: a word that isn't a known palette / section / tone alias is
    ignored, never invented.

    Always returns a spec (never ``None``); an unsteerable prompt («сделай сайт»)
    reifies to an empty spec (:attr:`FidelitySpec.is_empty`) — paired with
    :func:`spec_confidence` this is the "is the intent clear enough to skip the
    popup?" signal. Mirrors :func:`spec_from_discovery` but on a single string and
    without the ``None``-on-empty collapse, so callers can score the axis count.
    """
    return spec_from_discovery(None, prompt) or FidelitySpec()


def spec_confidence(spec: FidelitySpec) -> int:
    """How many independent intent axes the prompt pinned down (0–4).

    One point each for a decided theme, an accent family, a tone, and *any*
    sections (sections score once — it's a single "did we learn the structure?"
    signal, not a per-section tally, so a three-section prompt doesn't outweigh a
    palette+theme+tone one). Higher = the prompt steered more of the design on its
    own; the zero-question short-circuit fires only above a conservative floor so
    a thin one-axis hint still earns an onboarding question.
    """
    return int(spec.dark_mode is not None) + int(bool(spec.primary_family)) + int(
        bool(spec.sections)
    ) + int(spec.tone is not None)


def spec_prompt_directive(spec: FidelitySpec | None) -> str:
    """Render a chip-spec into a top-of-prompt directive the writer must obey.

    The generation-side leg of the causality bridge: the gauntlet already JUDGES
    a build against ``discovery_spec`` (gate side), but until now the writer never
    SAW it — a "тёмная тема" chip never reached the prompt, so the writer rendered
    light, the gate caught the mismatch, regeneration ran on the same raw prompt,
    and the project entered a deterministic reject loop (gate-teeth without
    generation-honour = net-negative). This emits the missing directive so a
    chip-picked axis actually steers generation.

    The block is imperative and ranks the user's explicit chip choice ABOVE the
    preset palette / brief / training default — those are guesses, a tapped chip
    is a decision. The palette line names the family AND a concrete HEX
    (:data:`_FAMILY_HEX`) chosen to land on the same family band ``family_of_hue``
    reads, so honouring it also satisfies the gate (no honour-but-still-fail).

    Returns ``""`` for ``None`` / empty spec — caller skips the section and the
    prompt is byte-identical to the pre-V2.5c build (back-compat, R-10).
    """
    if spec is None or spec.is_empty:
        return ""
    lines = [
        "ЯВНЫЙ ВЫБОР ПОЛЬЗОВАТЕЛЯ (онбординг-чипы) — ВЫСШИЙ приоритет. Перебивает "
        "пресет, бриф и любой training-default. Тапнутый чип = решение, а не догадка. "
        "Реализуй ТОЧНО:"
    ]
    if spec.primary_family:
        hexv = _FAMILY_HEX.get(spec.primary_family)
        if hexv:
            lines.append(
                f"  • Главный акцент (--primary / CTA / ссылки / фокус) = семейство "
                f"«{spec.primary_family}». Ставь HEX {hexv} (или близкий оттенок ТОГО ЖЕ "
                f"семейства). Любой запрет на этот цвет в блоке палитры/брифа — СНЯТ: "
                f"пользователь выбрал его явно."
            )
    if spec.dark_mode is True:
        lines.append(
            "  • Тема ТЁМНАЯ: фон тёмный (#0A0A0A…#16181D), текст светлый (#E5E7EB+), "
            'в Tailwind/:root установи dark_mode=true.'
        )
    elif spec.dark_mode is False:
        lines.append(
            "  • Тема СВЕТЛАЯ: фон светлый (#FFFFFF…#F8FAFC), текст тёмный, dark_mode=false."
        )
    if spec.tone:
        lines.append(
            f"  • Тон/вайб = «{spec.tone}» — выдержи во всей вёрстке: типографика, "
            f"плотность, motion, copy."
        )
    if spec.sections:
        rendered = []
        for canon in spec.sections:
            kw = _SECTION_KEYWORDS.get(canon)
            anchor = kw["anchor"][0] if kw else canon
            rendered.append(f'«{canon}» (<section id="{anchor}"> + видимый заголовок)')
        lines.append(
            "  • ОБЯЗАТЕЛЬНЫЕ секции, каждая отдельным блоком: " + ", ".join(rendered) + "."
        )
    return "\n".join(lines)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class FidelityFinding:
    """One axis where the rendered page contradicted the spec."""

    check: str
    detail: str


@dataclass(frozen=True)
class FidelityReport:
    """Verdict + JSON subscore of one chip→pixel audit."""

    findings: tuple[FidelityFinding, ...]
    rendered: bool
    # Axes that were *checked* (spec gave an answer AND the page gave a signal).
    # Tone with no declared marker abstains → not counted here, not a finding.
    checked: tuple[str, ...] = ()
    detected: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence."""
        return self.rendered and not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        """Machine-readable subscore — emitted into the gauntlet's JSON."""
        return {
            "gate": "chip-pixel",
            "rendered": self.rendered,
            "passed": self.passed,
            "checked": list(self.checked),
            "failed": list(self.classes),
            "detected": self.detected,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "chip-pixel: ABSTAIN (render harness did not run)"
        if self.passed:
            checked = ", ".join(self.checked) or "nothing requested"
            return f"chip-pixel: clean (honoured: {checked})"
        lines = [f"chip-pixel: {len(self.findings)} fidelity mismatch(es):"]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── helpers (pure) ─────────────────────────────────────────────────────────────


def _norm(s: str | None) -> str:
    """Lowercase, strip accents/diacritics, collapse whitespace."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    flat = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", flat).strip().lower()


def _hue_in(hue: float, ranges: tuple[tuple[float, float], ...]) -> bool:
    return any(lo <= hue <= hi for lo, hi in ranges)


def family_of_hue(hue: float) -> str | None:
    """First family band containing ``hue`` (insertion order of ``_FAMILY_BANDS``)."""
    for name, ranges in _FAMILY_BANDS.items():
        if _hue_in(hue, ranges):
            return name
    return None


def _dominant_accent(obs: Obs) -> tuple[float, str] | None:
    """The largest saturated CTA fill → (hue, hex). The painted accent the eye
    lands on. ``None`` when the page has no qualifying coloured CTA."""
    best: tuple[float, float, str] | None = None  # (area, hue, hex)
    for f in obs.get("fills", ()):
        bg = f.get("bg")
        if not bg or len(bg) < 4 or bg[3] < _ACCENT_MIN_ALPHA:
            continue
        r, g, b = bg[0], bg[1], bg[2]
        hue, sat, light = rgb_to_hsl((r, g, b))
        if sat < _ACCENT_MIN_SATURATION:
            continue
        if not (_ACCENT_MIN_LIGHTNESS < light < _ACCENT_MAX_LIGHTNESS):
            continue
        area = float(f.get("area") or 0)
        if best is None or area > best[0]:
            best = (area, hue, _hex((r, g, b)))
    if best is None:
        return None
    return (best[1], best[2])


def _hex(rgb: Rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(255.0, c))):02x}" for c in rgb)


def _section_present(canon: str, obs: Obs) -> bool:
    spec = _SECTION_KEYWORDS.get(canon)
    if not spec:
        return False
    anchors = {_norm(a) for a in obs.get("ids", ())} | {_norm(h) for h in obs.get("navHrefs", ())}
    for kw in spec["anchor"]:
        if any(kw in a for a in anchors if a):
            return True
    headings = [_norm(h) for h in obs.get("headings", ())]
    return any(kw in h for kw in spec["heading"] for h in headings if h)


# ── observation scoring (pure, the testable core) ─────────────────────────────


def _score_palette_bg(obs: Obs, spec: FidelitySpec) -> tuple[list[FidelityFinding], dict[str, Any]]:
    if spec.dark_mode is None:
        return [], {}
    bg = obs.get("pageBg")
    if not bg or len(bg) < 3:
        return [], {}
    rgb = (float(bg[0]), float(bg[1]), float(bg[2]))
    lum = relative_luminance(rgb)
    detected = {"page_bg": _hex(rgb), "page_bg_luminance": round(lum, 3)}
    if spec.dark_mode and lum > _DARK_LUM_MAX:
        return [
            FidelityFinding(
                PALETTE_BG,
                f"asked dark theme but page background {_hex(rgb)} is light "
                f"(luminance {lum:.2f} > {_DARK_LUM_MAX})",
            )
        ], detected
    if not spec.dark_mode and lum < _LIGHT_LUM_MIN:
        return [
            FidelityFinding(
                PALETTE_BG,
                f"asked light theme but page background {_hex(rgb)} is dark "
                f"(luminance {lum:.2f} < {_LIGHT_LUM_MIN})",
            )
        ], detected
    return [], detected


def _score_primary_family(
    obs: Obs, spec: FidelitySpec
) -> tuple[list[FidelityFinding], dict[str, Any]]:
    if not spec.primary_family:
        return [], {}
    want = _FAMILY_ALIASES.get(spec.primary_family.lower(), spec.primary_family.lower())
    bands = _FAMILY_BANDS.get(want)
    if bands is None:  # unknown requested family — can't assert, don't guess.
        return [], {}
    accent = _dominant_accent(obs)
    if accent is None:  # asked for a colour, page painted no coloured CTA → a miss.
        return [
            FidelityFinding(
                PRIMARY_FAMILY,
                f"asked {want} accent but no saturated CTA fill was painted",
            )
        ], {}
    hue, hexc = accent
    detected = {
        "accent_hex": hexc,
        "accent_hue": round(hue, 1),
        "accent_family": family_of_hue(hue),
    }
    if not _hue_in(hue, bands):
        got = family_of_hue(hue) or f"{hue:.0f}°"
        return [
            FidelityFinding(
                PRIMARY_FAMILY,
                f"asked {want} accent but painted CTA is {hexc} ({got}) — wrong colour family",
            )
        ], detected
    return [], detected


def _score_sections(obs: Obs, spec: FidelitySpec) -> tuple[list[FidelityFinding], dict[str, Any]]:
    if not spec.sections:
        return [], {}
    present = [s for s in spec.sections if _section_present(s, obs)]
    missing = [s for s in spec.sections if s not in present]
    detected = {"sections_present": present, "sections_missing": missing}
    if missing:
        return [
            FidelityFinding(
                SECTION_ANCHOR,
                f"requested section(s) {', '.join(missing)} have no anchor/heading on the page",
            )
        ], detected
    return [], detected


def _score_tone(obs: Obs, spec: FidelitySpec) -> tuple[list[FidelityFinding], dict[str, Any], bool]:
    """Returns (findings, detected, checked). ``checked`` is False when the page
    declared no tone marker — the axis ABSTAINS rather than false-failing."""
    if not spec.tone:
        return [], {}, False
    declared = obs.get("declaredTone")
    if not declared:
        return [], {}, False  # abstain — no reliable signal
    want = spec.tone.strip().lower()
    got = str(declared).strip().lower()
    detected = {"declared_tone": got}
    if got != want:
        return [
            FidelityFinding(TONE_MARKER, f"asked tone «{want}» but page declares «{got}»")
        ], detected, True
    return [], detected, True


def evaluate_fidelity(obs: Obs, spec: FidelitySpec, *, rendered: bool = True) -> FidelityReport:
    """Score a raw DOM observation against the spec → :class:`FidelityReport`.

    This is the whole gate, browser-free. ``obs`` is exactly what ``_FIDELITY_JS``
    returns; a hand-built dict is how the gate is unit-tested, and swapping the
    ``spec`` against a fixed ``obs`` is how "swap the answer → the verdict flips"
    is proven.
    """
    if not rendered:
        return FidelityReport(findings=(), rendered=False)

    findings: list[FidelityFinding] = []
    checked: list[str] = []
    detected: dict[str, Any] = {}

    bg_f, bg_d = _score_palette_bg(obs, spec)
    if spec.dark_mode is not None and bg_d:
        checked.append(PALETTE_BG)
    findings += bg_f
    detected.update(bg_d)

    pf_f, pf_d = _score_primary_family(obs, spec)
    if spec.primary_family:
        checked.append(PRIMARY_FAMILY)
    findings += pf_f
    detected.update(pf_d)

    sec_f, sec_d = _score_sections(obs, spec)
    if spec.sections:
        checked.append(SECTION_ANCHOR)
    findings += sec_f
    detected.update(sec_d)

    tone_f, tone_d, tone_checked = _score_tone(obs, spec)
    if tone_checked:
        checked.append(TONE_MARKER)
    findings += tone_f
    detected.update(tone_d)

    return FidelityReport(
        findings=tuple(findings),
        rendered=True,
        checked=tuple(checked),
        detected=detected,
    )


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Returns the raw observation scored by evaluate_fidelity(). It reads; it never
# judges. Colours → [r,g,b,a]; the painted page background walks body→html to the
# first opaque colour; fills are the saturated CTA backgrounds; sections are
# every id, in-page nav hash, and h1–h3 heading text; tone is the explicit
# data-omnia-tone / data-tone marker (absent → null → the tone axis abstains).
_FIDELITY_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const hex2 = (h) => {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    if (h.length !== 6) return null;
    const n = parseInt(h, 16);
    if (isNaN(n)) return null;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  };
  const parseColor = (s) => {
    if (!s) return null;
    s = s.trim();
    if (s[0] === '#') return hex2(s);
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    return [p[0] || 0, p[1] || 0, p[2] || 0, p.length > 3 ? p[3] : 1];
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // painted page background: body → html, first opaque solid colour
  const pageBgOf = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const cs = getComputedStyle(n);
      const c = parseColor(cs.backgroundColor);
      if (c && c[3] > 0.5) return c;
      n = n.parentElement;
    }
    return [255, 255, 255, 1];
  };

  // saturated CTA fills (the painted accent candidates)
  const fills = [];
  document.querySelectorAll('button, [role=button], a').forEach((el) => {
    if (!visible(el)) return;
    const cs = getComputedStyle(el);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return;
    const c = parseColor(cs.backgroundColor);
    if (!c || c[3] < 0.5) return;
    const r = el.getBoundingClientRect();
    fills.push({ bg: c, tag: el.tagName.toLowerCase(), area: r.width * r.height });
  });

  // section signals
  const ids = [];
  document.querySelectorAll('[id]').forEach((el) => {
    if (ids.length < 250) ids.push(el.id);
  });
  const navHrefs = [];
  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    const h = a.getAttribute('href');
    if (h && h.length > 1 && navHrefs.length < 120) navHrefs.push(h.slice(1));
  });
  const headings = [];
  document.querySelectorAll('h1, h2, h3').forEach((h) => {
    if (headings.length >= 120) return;
    const t = (h.innerText || h.textContent || '').replace(/\s+/g, ' ').trim();
    if (t) headings.push(t.slice(0, 80));
  });

  // declared tone marker (NOT the .tone-warm/.tone-cool photo-grading classes)
  const tone =
    document.body.getAttribute('data-omnia-tone') ||
    document.body.getAttribute('data-tone') ||
    document.documentElement.getAttribute('data-omnia-tone') ||
    null;

  return { pageBg: pageBgOf(document.body), fills, ids, navHrefs, headings, declaredTone: tone };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page, spec: FidelitySpec) -> FidelityReport:
    obs = await page.evaluate(_FIDELITY_JS)
    return evaluate_fidelity(obs, spec)


async def audit_url(
    url: str, spec: FidelitySpec, *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> FidelityReport:
    """Audit a LIVE url against ``spec``. Fail-soft (R-10): a render/navigation
    error → an ABSTAIN report (``rendered=False``) rather than a raise."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    viewport={"width": int(width), "height": GATE_HEIGHT},
                    reduced_motion="reduce",
                )
                try:
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    return await _audit_page(page, spec)
                finally:
                    await page.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("chip_pixel_gate: url audit failed (abstain): %r", exc)
        return FidelityReport((), rendered=False)


async def audit_files(
    files: dict[str, str],
    spec: FidelitySpec,
    *,
    width: int = GATE_WIDTH,
    timeout_ms: int = 15_000,
) -> FidelityReport:
    """Audit a static ``{path: html}`` page set against ``spec`` (needs index.html)."""
    if "index.html" not in files:
        return FidelityReport((), rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-chippix-") as tmp:
            workdir = Path(tmp)
            for path, content in files.items():
                full = workdir / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
            index_uri = (workdir / "index.html").as_uri()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        viewport={"width": int(width), "height": GATE_HEIGHT},
                        reduced_motion="reduce",
                    )
                    try:
                        await goto_and_settle(page, index_uri, timeout_ms=timeout_ms)
                        return await _audit_page(page, spec)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
    except Exception as exc:
        log.warning("chip_pixel_gate: files audit failed (abstain): %r", exc)
        return FidelityReport((), rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print(
            "usage: python -m omnia_api.services.chip_pixel_gate <url|index.html-dir> "
            "[--palette '<text>'] [--sections 'a,b,c'] [--tone <tone>]"
        )
        return 2
    target = argv[1]
    palette = sections = tone = None
    rest = argv[2:]
    for i, a in enumerate(rest):
        if a == "--palette" and i + 1 < len(rest):
            palette = rest[i + 1]
        elif a == "--sections" and i + 1 < len(rest):
            sections = rest[i + 1]
        elif a == "--tone" and i + 1 < len(rest):
            tone = rest[i + 1]
    spec = FidelitySpec.from_answers(palette=palette, sections=sections, tone=tone)

    if target.startswith(("http://", "https://")):
        report = asyncio.run(audit_url(target, spec))
    else:
        root = Path(target)
        files = {
            str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in root.rglob("*.html")
        }
        report = asyncio.run(audit_files(files, spec))
    print(report.summary())
    print(json.dumps(report.subscore(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "CHECKS",
    "FidelityFinding",
    "FidelityReport",
    "FidelitySpec",
    "audit_files",
    "audit_url",
    "compile_build_spec",
    "evaluate_fidelity",
    "family_of_hue",
    "spec_confidence",
    "spec_from_discovery",
]
