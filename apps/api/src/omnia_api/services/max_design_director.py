# ruff: noqa: E501
"""Deterministic pre-code product direction for the MAX App Engineer.

The model remains the author of the interface, but it no longer starts from an
empty aesthetic prompt.  This module compiles the business brief into a small,
persistable DesignDNA and a truthful managed-capability contract *before* any
product file is written.  It is deliberately pure and dependency-free: retries,
repairs and follow-up edits receive the same decision, while unrelated generator
stacks are untouched.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from omnia_api.services.design_tokens import tokens_for_project
from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize

_KEY = "max_app_engineer"


def _has(text: str, *needles: str) -> bool:
    lowered = text.casefold()
    return any(needle.casefold() in lowered for needle in needles)


def _clean(value: object, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class DesignConcept:
    id: str
    name: str
    composition: str
    typography: str
    geometry_density: str
    motion: str
    signature_interaction: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "composition": self.composition,
            "typography": self.typography,
            "geometry_density": self.geometry_density,
            "motion": self.motion,
            "signature_interaction": self.signature_interaction,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DesignConcept | None:
        fields = {key: _clean(value.get(key)) for key in cls.__dataclass_fields__}
        if not all(fields.values()):
            return None
        return cls(**fields)


@dataclass(frozen=True)
class MaxCapability:
    id: str
    label: str
    integration: str
    truth_contract: str
    executable_proof: str
    ui_marker_required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "integration": self.integration,
            "truth_contract": self.truth_contract,
            "executable_proof": self.executable_proof,
            "ui_marker_required": self.ui_marker_required,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> MaxCapability | None:
        fields = {
            key: _clean(value.get(key))
            for key in ("id", "label", "integration", "truth_contract", "executable_proof")
        }
        if not all(fields.values()):
            return None
        return cls(**fields, ui_marker_required=bool(value.get("ui_marker_required", True)))


@dataclass(frozen=True)
class MaxDesignDNA:
    audience: str
    emotional_promise: str
    primary_action: str
    appearance: str
    semantic_colors: Mapping[str, str]
    chart_language: str
    anti_patterns: tuple[str, ...]
    concepts: tuple[DesignConcept, ...]
    chosen_id: str
    chosen_rationale: str
    screens: tuple[str, ...]
    skill_slices: tuple[str, ...]
    capabilities: tuple[MaxCapability, ...]

    @property
    def chosen(self) -> DesignConcept:
        return next(concept for concept in self.concepts if concept.id == self.chosen_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "audience": self.audience,
            "emotional_promise": self.emotional_promise,
            "primary_action": self.primary_action,
            "appearance": self.appearance,
            "semantic_colors": dict(self.semantic_colors),
            "chart_language": self.chart_language,
            "anti_patterns": list(self.anti_patterns),
            "concepts": [concept.to_dict() for concept in self.concepts],
            "chosen_id": self.chosen_id,
            "chosen_rationale": self.chosen_rationale,
            "screens": list(self.screens),
            "skill_slices": list(self.skill_slices),
            "capabilities": [capability.to_dict() for capability in self.capabilities],
        }

    def design_spec(self) -> dict[str, object]:
        chosen = self.chosen
        return {
            "director": "omnia-max-app-engineer",
            "product_promise": self.emotional_promise,
            "audience": self.audience,
            "primary_action": self.primary_action,
            "appearance": self.appearance,
            "directions_considered": [concept.to_dict() for concept in self.concepts],
            "chosen_direction": chosen.name,
            "chosen_id": chosen.id,
            "chosen_rationale": self.chosen_rationale,
            "screens": list(self.screens),
            "visual_system": {
                "composition": chosen.composition,
                "typography": chosen.typography,
                "semantic_colors": dict(self.semantic_colors),
                "geometry_density": chosen.geometry_density,
                "chart_language": self.chart_language,
            },
            "motion": [chosen.motion, chosen.signature_interaction],
            "signature_interaction": chosen.signature_interaction,
            "states": ["loading", "empty", "error", "success"],
            "anti_pattern_blacklist": list(self.anti_patterns),
            "skill_slices": list(self.skill_slices),
            "capability_contract": [capability.to_dict() for capability in self.capabilities],
        }

    def prompt_block(self) -> str:
        spec = json.dumps(self.design_spec(), ensure_ascii=False, indent=2)
        capability_lines = "\n".join(
            f"- {item.id}: {item.truth_contract} PROOF: {item.executable_proof}"
            for item in self.capabilities
        )
        return (
            "\n\nOMNIA MAX APP ENGINEER — DESIGN DIRECTOR DECISION (server-owned, "
            "compiled before code):\n"
            "Three materially different concepts were explored; implement ONLY the selected "
            "one coherently. This is an art direction, not a screen template. Write this exact "
            "JSON to `.omnia/max-design-spec.json`; repairs must preserve the selected DNA.\n"
            f"```json\n{spec}\n```\n"
            "MANAGED CAPABILITY CONTRACT — never simulate success and never hide unavailable "
            "providers. Await the named managed primitive and render pending/error/unavailable/"
            'success truthfully. Put `data-omnia-capability="<id>"` on each user-facing '
            "capability whose contract requests a marker.\n"
            f"{capability_lines}\n"
            "Load the required premium-mobile-foundation skill plus the listed domain slices "
            "before the first product write."
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object] | None) -> MaxDesignDNA | None:
        if not isinstance(value, Mapping):
            return None
        raw_concepts = value.get("concepts")
        raw_capabilities = value.get("capabilities")
        raw_anti_patterns = value.get("anti_patterns")
        raw_screens = value.get("screens")
        raw_skill_slices = value.get("skill_slices")
        if not isinstance(raw_concepts, list) or not isinstance(raw_capabilities, list):
            return None
        concepts = tuple(
            concept
            for row in raw_concepts
            if isinstance(row, Mapping) and (concept := DesignConcept.from_dict(row))
        )
        capabilities = tuple(
            capability
            for row in raw_capabilities
            if isinstance(row, Mapping) and (capability := MaxCapability.from_dict(row))
        )
        colors = value.get("semantic_colors")
        if len(concepts) != 3 or not isinstance(colors, Mapping):
            return None
        try:
            dna = cls(
                audience=_clean(value.get("audience")),
                emotional_promise=_clean(value.get("emotional_promise")),
                primary_action=_clean(value.get("primary_action")),
                appearance=_clean(value.get("appearance")),
                semantic_colors={str(k): _clean(v, 32) for k, v in colors.items()},
                chart_language=_clean(value.get("chart_language")),
                anti_patterns=tuple(_clean(x) for x in raw_anti_patterns if _clean(x))
                if isinstance(raw_anti_patterns, list)
                else (),
                concepts=concepts,
                chosen_id=_clean(value.get("chosen_id")),
                chosen_rationale=_clean(value.get("chosen_rationale"), 500),
                screens=tuple(_clean(x, 100) for x in raw_screens if _clean(x))
                if isinstance(raw_screens, list)
                else (),
                skill_slices=tuple(_clean(x, 80) for x in raw_skill_slices if _clean(x))
                if isinstance(raw_skill_slices, list)
                else (),
                capabilities=capabilities,
            )
            _ = dna.chosen
            return dna
        except (StopIteration, TypeError):
            return None


def _domain(brief: str) -> str:
    groups = (
        ("fitness", ("фитнес", "тренир", "workout", "спорт")),
        ("restaurant", ("ресторан", "еда", "меню", "iiko")),
        ("booking", ("бронир", "запис", "слот", "booking")),
        ("education", ("обуч", "курс", "урок", "education")),
        ("commerce", ("магазин", "товар", "каталог", "корзин", "shop")),
    )
    return next((name for name, cues in groups if _has(brief, *cues)), "service")


def _capabilities(brief: str) -> tuple[MaxCapability, ...]:
    rows: list[MaxCapability] = [
        MaxCapability(
            "max_bridge",
            "MAX identity and Bridge",
            "useMaxApp / managed Bridge",
            "Use verified initData/profile only; nullable profile has neutral copy.",
            "signed MAX bootstrap + hydration gate",
            False,
        ),
        MaxCapability(
            "legal",
            "Support and legal",
            "managed /support and /legal routes",
            "Privacy, terms and support stay reachable from the product.",
            "signed browser navigation to managed legal routes",
            False,
        ),
    ]
    conditional = (
        (
            _has(brief, "ии", " ai ", "нейро", "gpt", "claude", "ассистент"),
            MaxCapability(
                "managed_ai",
                "Managed AI",
                "requestOmniaAI",
                "Render a result only after the awaited managed call; expose pending, failure and retry.",
                "source contract + signed primary action",
                True,
            ),
        ),
        (
            _has(brief, "оплат", "юкас", "yookassa", "checkout"),
            MaxCapability(
                "payments",
                "Payments",
                "getOmniaIntegrations + createOmniaPayment",
                "Show connected/unavailable; success follows provider confirmation, never a local timer.",
                "managed call + confirmation_url + signed action",
                True,
            ),
        ),
        (
            _has(brief, "лид", "заяв", "контакт", "бронир", "запис", "lead"),
            MaxCapability(
                "leads",
                "Leads and bookings",
                "createMaxAction + getMaxActions",
                "Persist the submitted lead for the verified account and restore it after reload.",
                "signed action then reload persistence",
                True,
            ),
        ),
        (
            _has(brief, "каталог", "товар", "меню", "iiko", "catalog"),
            MaxCapability(
                "catalog",
                "Catalog",
                "omniaMaxConfig.content / getOmniaCatalog when connected",
                "Business catalog content may be static; tenant/provider data must stay honest and unavailable visibly.",
                "managed configuration/provider state + navigation",
                True,
            ),
        ),
        (
            _has(brief, "аналит", "статист", "график", "метрик", "chart"),
            MaxCapability(
                "analytics",
                "Analytics",
                "getMaxActions / managed event data",
                "Charts use real or explicitly empty data; never invent personal achievements.",
                "accessible chart label + empty/populated state",
                True,
            ),
        ),
        (
            _has(brief, "сохран", "истори", "профил", "заказ", "тренир", "прогресс"),
            MaxCapability(
                "persisted_actions",
                "Persistent user actions",
                "createMaxAction + getMaxActions",
                "Await writes, show failures and restore the same account's state on mount.",
                "signed click and reload restoration",
                True,
            ),
        ),
    )
    rows.extend(capability for selected, capability in conditional if selected)
    return tuple(dict((row.id, row) for row in rows).values())


def compile_max_design_dna(
    brief: str,
    *,
    project_id: str,
    build_plan: object | None = None,
) -> MaxDesignDNA:
    """Compile a stable DesignDNA and capability plan without another model call."""

    brief = brief.strip()
    domain = _domain(brief)
    audience = {
        "fitness": "mobile-first people building a repeatable training habit",
        "restaurant": "hungry guests choosing and ordering quickly inside MAX",
        "booking": "busy customers selecting a trustworthy available time",
        "education": "learners who need visible progress and a clear next lesson",
        "commerce": "mobile shoppers comparing and completing a confident choice",
        "service": "MAX users completing the business's primary task with minimal friction",
    }[domain]
    promise = {
        "fitness": "energy and calm control over the next achievable step",
        "restaurant": "appetite, confidence and a frictionless path to the order",
        "booking": "certainty that the right slot is reserved without ambiguity",
        "education": "focused momentum and a visible sense of mastery",
        "commerce": "confident discovery with a satisfying, truthful completion",
        "service": "clarity, trust and immediate progress toward the user's goal",
    }[domain]
    action = {
        "fitness": "start or record the next training action",
        "restaurant": "choose an item and continue the real order flow",
        "booking": "choose a slot and submit the booking",
        "education": "continue the next lesson or exercise",
        "commerce": "select a product and continue the real purchase flow",
        "service": "complete the brief's primary business action",
    }[domain]
    appearance = "dark" if _has(brief, "тёмн", "темн", "dark", "ночн", "неон") else "light"
    token = tokens_for_project(project_id, industry_hint=brief).palette
    colors = {
        "canvas": "#0f1115" if appearance == "dark" else token.bg,
        "surface": "#191c22" if appearance == "dark" else token.surface,
        "text": "#f5f7fa" if appearance == "dark" else token.text,
        "muted": "#9ca3af" if appearance == "dark" else token.muted,
        "primary": token.primary,
        "accent": token.accent,
        "danger": "#dc2626",
    }
    object_name = {
        "fitness": "training pulse",
        "restaurant": "dish-to-order path",
        "booking": "availability timeline",
        "education": "learning path",
        "commerce": "collection-to-choice path",
        "service": "primary task object",
    }[domain]
    concepts = (
        DesignConcept(
            "precision-instrument",
            "Precision Instrument",
            "Task-first instrument panel with one dominant live object, contextual controls and compact secondary evidence; not a card dashboard.",
            "Condensed or tight display headings, tabular metrics and restrained supporting text.",
            "Compact 8px rhythm, crisp separators, small-to-medium radii and high information precision.",
            "Short causal state transitions and numeric interpolation; reduced-motion keeps instant state clarity.",
            f"A tactile {object_name} that acknowledges input and exposes the next move in place.",
        ),
        DesignConcept(
            "editorial-journey",
            "Editorial Journey",
            "A vertically paced narrative with oversized task-led moments, asymmetric editorial grouping and progressive disclosure.",
            "Expressive display scale paired with highly legible humanist body text.",
            "Spacious 12px rhythm, generous negative space, mixed edge geometry and rare elevated surfaces.",
            "Chapter-like spatial continuity with interruptible transforms and calm completion reveal.",
            f"The {object_name} unfolds as a guided story rather than a form or dashboard.",
        ),
        DesignConcept(
            "kinetic-canvas",
            "Kinetic Canvas",
            "The domain object owns the viewport as a spatial canvas; controls orbit the current state and details move into sheets.",
            "Bold geometric display voice, concise labels and strong numeric hierarchy.",
            "Balanced 10px rhythm, sculpted containers, layered depth and explicit touch affordances.",
            "Spring-like direct manipulation, sheet choreography and a meaningful completion response.",
            f"Directly manipulate the {object_name}; the canvas responds before revealing detail.",
        ),
    )
    seed = int.from_bytes(hashlib.sha256(f"{project_id}:{brief}".encode()).digest()[:2], "big")
    preferred = {
        "fitness": "kinetic-canvas",
        "restaurant": "editorial-journey",
        "booking": "precision-instrument",
        "education": "editorial-journey",
        "commerce": "kinetic-canvas",
        "service": concepts[seed % 3].id,
    }[domain]
    chosen = next(concept for concept in concepts if concept.id == preferred)
    screens = tuple(
        _clean(getattr(screen, "route", "") or getattr(screen, "name", ""), 100)
        for screen in getattr(build_plan, "screens", ())
        if _clean(getattr(screen, "route", "") or getattr(screen, "name", ""), 100)
    ) or ("primary", "activity", "profile")
    domain_skill = f"domain-{domain}" if domain != "service" else "product-strategy"
    skills = ("premium-mobile-foundation", domain_skill)
    capabilities = _capabilities(f" {brief} ")
    chart_language = (
        "Accessible SVG/native CSS marks with direct labels, tabular figures and honest empty data; "
        "no decorative fake series."
        if any(item.id == "analytics" for item in capabilities)
        else "Use data graphics only when real data exists; prefer task state over decorative charts."
    )
    return MaxDesignDNA(
        audience=audience,
        emotional_promise=promise,
        primary_action=action,
        appearance=appearance,
        semantic_colors=colors,
        chart_language=chart_language,
        anti_patterns=(
            "generic equal-card dashboard",
            "purple gradient as substitute for hierarchy",
            "marketing hero inside a task app",
            "fake personal history, metrics or success",
            "tiny touch targets, unlabeled icons and content under safe areas",
            "motion without reduced-motion equivalent",
        ),
        concepts=concepts,
        chosen_id=chosen.id,
        chosen_rationale=(
            f"{chosen.name} best turns the {domain} promise into the primary action for {audience}; "
            "the other concepts remain documented alternatives, not colour variants."
        ),
        screens=screens,
        skill_slices=skills,
        capabilities=capabilities,
    )


def merge_into_discovery(
    discovery_spec: Mapping[str, Any] | None, dna: MaxDesignDNA
) -> dict[str, Any]:
    out = dict(discovery_spec or {})
    out[_KEY] = dna.to_dict()
    return out


def read_from_discovery(discovery_spec: Mapping[str, Any] | None) -> MaxDesignDNA | None:
    if not isinstance(discovery_spec, Mapping):
        return None
    value = discovery_spec.get(_KEY)
    return MaxDesignDNA.from_dict(value if isinstance(value, Mapping) else None)


def completion_gap(dna: MaxDesignDNA, files: Mapping[str, str]) -> str | None:
    """Reject drift from the server-selected direction and capability proof hooks."""

    raw = files.get(".omnia/max-design-spec.json", "")
    try:
        spec = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "Restore the Design Director JSON in .omnia/max-design-spec.json."
    if not isinstance(spec, dict):
        return "The MAX Design Director artifact must be one JSON object."
    if spec.get("chosen_id") != dna.chosen_id:
        return f"Preserve the Design Director selection: chosen_id must be {dna.chosen_id}."
    directions = spec.get("directions_considered")
    ids = {str(item.get("id") or "") for item in directions or [] if isinstance(item, Mapping)}
    expected = {concept.id for concept in dna.concepts}
    if ids != expected:
        return "Preserve all three materially distinct Design Director concepts in the design spec."
    source = "\n".join(
        content
        for path, content in files.items()
        if path.startswith("src/") and path.endswith((".ts", ".tsx"))
    )
    for capability in dna.capabilities:
        if capability.ui_marker_required and not re.search(
            r"data-omnia-capability\s*=\s*[{]?['\"]" + re.escape(capability.id) + r"['\"]",
            source,
            re.IGNORECASE,
        ):
            return (
                f"Capability {capability.id} lacks executable UI evidence. Add "
                f'data-omnia-capability="{capability.id}" to its real control or state.'
            )
    return None


def evidence_verdict(dna: MaxDesignDNA, files: Mapping[str, str]) -> FunctionalVerdict:
    gap = completion_gap(dna, files)
    checks = [
        Check("max_design_dna", gap is None, gap or f"selected={dna.chosen_id}; concepts=3"),
        Check(
            "max_capability_contract",
            gap is None,
            "selected=" + ",".join(item.id for item in dna.capabilities),
        ),
    ]
    return summarize(checks)


__all__ = [
    "DesignConcept",
    "MaxCapability",
    "MaxDesignDNA",
    "compile_max_design_dna",
    "completion_gap",
    "evidence_verdict",
    "merge_into_discovery",
    "read_from_discovery",
]
