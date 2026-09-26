"""Build spec — the onboarding chips reified into a deterministic contract.

The discovery popup asks the owner a handful of design questions (dark or light,
accent family, which sections, what tone). Their answers are not prose: they are
a small set of axes worth storing, previewing and handing to the agent. This
module is the ONE place that parses them out of free text, normalises them to a
canonical vocabulary and resolves them into concrete values.

Three readers, one vocabulary
=============================
* ``discovery`` — decides whether enough axes are decided to stop asking
  (:func:`spec_confidence`) and compiles a spec straight out of a hand-typed
  first prompt (:func:`compile_build_spec`);
* ``generation.onboarding`` — paints the live mini-preview of the answers
  (:func:`spec_preview` resolves the accent family to a concrete HEX);
* ``generation.acceptance`` — persists the spec into ``projects.discovery_spec``,
  from where ``project_memory`` carries it into the agent's product contract.

What used to live here
======================
The module was born as the "chip→pixel" gate: it also opened the built page in a
browser and asserted the painted pixels honoured the answers. That audit — and
its sibling ``wow_dom_gate`` — judged static pages produced by the site builder,
which no longer exists here; for a MAX app the behaviour gates
(``functional_gate``, ``security_gate``, ``isolation_gate``) are the real check.
The audit left with the builder; the vocabulary stayed, because the answers it
speaks are still what the owner picked.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# ── the chip vocabulary ───────────────────────────────────────────────────────

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

# Representative HEX per family — the concrete accent a chip-picked family
# resolves to. Each value is hand-tuned to sit at the CENTRE of its family's
# range, so the swatch the onboarding preview paints is the accent the build
# gets. CTA swatches (vivid, mid-lightness), not full palettes — the agent
# derives shades around the family. R-04: the ONE source every reader shares.
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


# ── helpers (pure) ────────────────────────────────────────────────────────────

def _norm(s: str | None) -> str:
    """Lowercase, strip accents/diacritics, collapse whitespace."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    flat = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", flat).strip().lower()


# ── the spec (reified scripted discovery answers) ─────────────────────────────

@dataclass(frozen=True)
class FidelitySpec:
    """What the user picked in onboarding, reified as an explicit contract.

    ``None`` / empty on an axis means "not decided" — an axis the owner never
    steered is left to the agent instead of being guessed here.
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

        Round-trips via :meth:`from_dict` — the persisted shape ``project_memory``
        reads back when it hands the agent the product contract.
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
# leaves tone NULL rather than guessing from prose (the same abstain discipline
# the onboarding popup shows the owner). Substring match, first hit wins.
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

def spec_preview(spec: FidelitySpec | None) -> dict[str, Any] | None:
    """Resolve a chip-spec into a small live-preview payload for the onboarding UI.

    The onboarding popup gathers design axes turn by turn; this marshals the
    CUMULATIVE :class:`FidelitySpec` into the handful of resolved tokens the
    workspace needs to paint a live mini-hero that morphs on every answer —
    «покажи ЧТО построим», not just «эхо что сказал» (NORTH STAR pillars 2×3).

    Resolves ``primary_family`` to its concrete accent HEX through the SAME
    :data:`_FAMILY_HEX` table every other reader uses (R-04 single source), so
    the preview swatch lands on the exact accent the build will get.
    ``accent`` is ``None`` when no family is decided yet (the UI falls back to its
    own neutral accent). Returns ``None`` for a ``None`` / empty spec (nothing
    decided yet → the popup shows no preview), so an undecided first question
    stays clean and the preview only appears once an axis is actually steered.
    """
    if spec is None or spec.is_empty:
        return None
    return {
        "accent": _FAMILY_HEX.get(spec.primary_family or ""),
        "accent_family": spec.primary_family,
        "dark_mode": spec.dark_mode,
        "tone": spec.tone,
        "sections": list(spec.sections),
    }

def compile_build_spec(prompt: str) -> FidelitySpec:
    """Reify a single raw build prompt into a :class:`FidelitySpec`, no chips, no LLM.

    The zero-question intent compiler (V2.12): the North Star's pillar 2 says the
    best onboarding is its *absence* when intent is already clear. A rich prompt
    like «тёмный минималистичный лендинг с каталогом и отзывами на фиолетовом»
    carries the same design decisions a chip interview would extract — so we read
    them straight from the text, deterministically, through the **same**
    :meth:`FidelitySpec.from_answers` / :func:`_detect_tone` extractors the chip
    flow uses (R-04 single source). No new parsing rules,
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


__all__ = [
    "FidelitySpec",
    "compile_build_spec",
    "spec_confidence",
    "spec_from_discovery",
    "spec_preview",
]
