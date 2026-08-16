"""Bounded capability packs for MAX generation.

ProductSpec/kernel runs route the smallest useful set deterministically and
preload compact, kernel-safe excerpts once. Historical edit runs retain the
read-only catalog helpers, but the strict fresh-build path never spends model
turns choosing or loading skills.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from omnia_api.services import agent_builder, skill_library

_MAX_TEMPLATE = "max-miniapp-nextjs"
MAX_SKILL_SELECTION_LIMIT = 6
_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9-]{2,}")
_DATA_SIGNALS = (
    "аналит",
    "статист",
    "метрик",
    "график",
    "chart",
    "dashboard",
    "trend",
)
_RU_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("фитнес", ("fitness", "sports", "health", "training")),
    ("тренир", ("fitness", "sports", "coach", "training")),
    ("медиц", ("medical", "healthcare", "health")),
    ("здоров", ("health", "wellness", "medical")),
    ("финанс", ("finance", "fintech", "banking")),
    ("магазин", ("commerce", "ecommerce", "retail")),
    ("обуч", ("education", "learning")),
    ("курс", ("education", "learning")),
    ("еда", ("food", "restaurant")),
    ("ресторан", ("food", "restaurant", "hospitality")),
    ("бронир", ("booking", "services", "hospitality")),
    ("крипт", ("crypto", "fintech", "finance")),
    ("игр", ("gaming", "entertainment")),
    ("аналит", ("analytics", "dashboard", "metrics", "comparison")),
    ("статист", ("statistics", "metrics", "trend", "time series")),
    ("график", ("chart", "visualization", "trend")),
)

_DOMAIN_SKILLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("domain-fitness", ("fitness", "фитнес", "тренир", "workout", "спорт")),
    ("domain-restaurant", ("restaurant", "ресторан", "еда", "меню", "iiko")),
    ("domain-booking", ("booking", "бронир", "запис", "slot")),
    ("domain-education", ("education", "обуч", "курс", "урок")),
    ("domain-commerce", ("commerce", "магазин", "товар", "каталог", "корзин")),
)
_ROUTER_CANDIDATES = (
    "ui-ux-pro-max",
    "max-platform",
    "product-strategy",
    *(skill for skill, _signals in _DOMAIN_SKILLS),
    "production-readiness",
    "ai-native-ux",
    "trust-safety",
)
_PERSISTENCE_SIGNALS = (
    "persistence",
    "persisted",
    "history",
    "profile",
    "booking",
    "order",
    "save",
    "сохран",
    "истори",
    "профил",
    "бронир",
    "заказ",
)
_AI_SIGNALS = ("ai", "ии", "gpt", "claude", "gemini", "нейро", "ассистент")
_PAYMENT_SIGNALS = ("payment", "payments", "checkout", "yookassa", "юкас", "оплат")
_TRUST_SIGNALS = (
    "trust",
    "safety",
    "privacy",
    "personal_data",
    "user_content",
    "health",
    "medical",
    "безопас",
    "приват",
    "персональн",
    "медиц",
    "здоров",
)


def _structured_product_text(product_spec: object) -> str:
    """Make a bounded, deterministic routing corpus from a ProductSpec-like value."""

    if isinstance(product_spec, str):
        return product_spec.casefold()
    if not isinstance(product_spec, Mapping):
        for method_name in ("model_dump", "to_dict"):
            method = getattr(product_spec, method_name, None)
            if not callable(method):
                continue
            candidate = method()
            if isinstance(candidate, Mapping):
                product_spec = candidate
                break
        else:
            attributes = getattr(product_spec, "__dict__", None)
            if isinstance(attributes, Mapping):
                product_spec = attributes

    parts: list[str] = []

    def visit(value: object, *, key: str = "", depth: int = 0) -> None:
        if depth > 5 or len(parts) >= 160:
            return
        if isinstance(value, Mapping):
            for raw_key in sorted(value, key=lambda item: str(item)):
                name = str(raw_key).strip().casefold()
                child = value[raw_key]
                if isinstance(child, bool):
                    if child:
                        parts.append(name)
                    continue
                parts.append(name)
                visit(child, key=name, depth=depth + 1)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for child in value:
                visit(child, key=key, depth=depth + 1)
            return
        if value is not None and not isinstance(value, bool):
            text = str(value).strip().casefold()
            if text:
                parts.append(text[:240])

    visit(product_spec)
    return " ".join(parts)


def _has_signal(corpus: str, signals: Iterable[str]) -> bool:
    return any(signal.casefold() in corpus for signal in signals)


def _available_max_skills(available_skill_ids: Iterable[str] | None) -> frozenset[str]:
    if available_skill_ids is not None:
        return frozenset(str(skill).strip().casefold() for skill in available_skill_ids)
    return frozenset(
        skill
        for skill in _ROUTER_CANDIDATES
        if agent_builder.load_stack_skill(_MAX_TEMPLATE, skill) is not None
    )


def select_max_skills(
    product_spec: object,
    *,
    available_skill_ids: Iterable[str] | None = None,
    max_skills: int = MAX_SKILL_SELECTION_LIMIT,
) -> tuple[str, ...]:
    """Route a ProductSpec/dict to the smallest stable set of MAX packs.

    The selector is intentionally policy-only: callers can pass their catalog
    snapshot in tests, while production checks the stack allowlist.  It never
    requests screenshot critique packs; visual proof belongs to runtime gates,
    not first-draft capability loading.
    """

    if max_skills < 1:
        return ()
    available = _available_max_skills(available_skill_ids)
    corpus = _structured_product_text(product_spec)
    selected: list[str] = []

    def add(skill: str) -> None:
        if skill in available and skill not in selected and len(selected) < max_skills:
            selected.append(skill)

    # These packs are short platform/creative context, never inferred from a
    # possibly incomplete discovery response.
    add("ui-ux-pro-max")
    add("max-platform")

    domain_skill = next(
        (skill for skill, signals in _DOMAIN_SKILLS if _has_signal(corpus, signals)),
        "product-strategy",
    )
    add(domain_skill)
    if domain_skill != "product-strategy" and domain_skill not in available:
        add("product-strategy")

    if _has_signal(corpus, _PERSISTENCE_SIGNALS):
        add("production-readiness")
    if _has_signal(corpus, _AI_SIGNALS):
        add("ai-native-ux")
    if _has_signal(corpus, (*_PAYMENT_SIGNALS, *_TRUST_SIGNALS)):
        add("trust-safety")
    return tuple(selected)


# Short alias for native-loop callers.  Keep both names while integrations move
# from prompt-derived routing to structured discovery specs.
route_max_skills = select_max_skills


def render_selected_max_skills(
    skill_ids: Iterable[str],
    *,
    prompt: str,
    project_id: str,
    max_chars: int = 8_000,
) -> str:
    """Preload routed packs once without reintroducing planning/proof ceremony.

    The historical UI pack asks the model to explore three directions, persist a
    design spec and critique screenshots.  ProductSpec runs have already chosen
    one style and Omnia owns verification, so only the implementation craft bar
    and project-matched evidence belong in this compact context.
    """

    remaining = max(0, min(int(max_chars), 8_000))
    sections: list[str] = []
    for skill_id in tuple(dict.fromkeys(str(item).strip().casefold() for item in skill_ids)):
        if remaining <= 0 or skill_id == "visual-evaluation":
            break
        result = read_max_skill(skill_id, prompt=prompt, project_id=project_id)
        if not result.get("ok"):
            continue
        body = str(result.get("content") or "").strip()
        marker = body.find("\n\n")
        if marker >= 0:
            body = body[marker + 2 :]
        if skill_id == "ui-ux-pro-max":
            craft_start = body.find("## Craft bar")
            evidence_start = body.find("PLUGIN EVIDENCE:")
            craft = ""
            if craft_start >= 0:
                craft_end = body.find("## Freedom with standards", craft_start)
                craft = body[craft_start : craft_end if craft_end >= 0 else evidence_start]
            evidence = body[evidence_start:] if evidence_start >= 0 else ""
            evidence = evidence.replace(
                "Treat these as raw material for the three art directions. You may combine, "
                "transform or reject them; never copy a preset wholesale.",
                "The ProductSpec already selected the style. Pick at most one compatible "
                "candidate and apply it consistently; never start another design exploration.",
            ).replace(
                "Possible visual languages to debate, not templates:",
                "Optional visual-language candidates; choose at most one, never a template:",
            )
            body = "\n\n".join(part.strip() for part in (craft, evidence) if part.strip())
        elif skill_id == "product-strategy":
            # The final legacy paragraph asks for max-design-spec.json.  The
            # deterministic ProductSpec/BuildPlan supersede that second plan.
            body = body.split("\nBefore code,", 1)[0].rstrip()
            body = body.replace(
                "7. Use realistic Russian content and data whose relationships tell a "
                "coherent\n   story. Demo data should prove the interaction, not fill "
                "rectangles.",
                "7. Use realistic Russian copy and canonical static reference content.\n"
                "   User-owned data starts empty and appears only after a real managed action.",
            )
        elif skill_id == "max-platform":
            body = body.replace(
                "- Use the installed MAX Bridge and Omnia integration client. Read the locked "
                "files\n  or call `docs` when an API signature is uncertain; never invent a "
                "bridge method,\n  package export or server route.",
                "- Use the installed MAX Bridge and Omnia integration client with the exact "
                "managed\n  signatures in the environment manifest; never invent a bridge "
                "method, package\n  export or server route.",
            )
        elif skill_id == "production-readiness":
            body = body.replace(
                "Reload the app and prove the saved result returns for the same user while a\n"
                "  different verified MAX user cannot see it.",
                "Restore saved results after reload for the same verified user; all managed reads "
                "remain user-scoped.",
            ).replace(
                "Validate offline/slow network, empty account, expired launch data, server error\n"
                "  and duplicate tap. No action may end in a decorative toast with no state "
                "change.",
                "Render honest offline/slow-network, empty-account, expired-launch, server-error "
                "and duplicate-tap states. No action may end in a decorative toast without a "
                "state change.",
            )
            body = body.split("\nBefore `done`,", 1)[0].rstrip()
        elif skill_id == "trust-safety":
            body = body.split("\n## Safety proof", 1)[0].rstrip()
        per_skill = min(2_000, remaining)
        excerpt = body[:per_skill].rstrip()
        if not excerpt:
            continue
        section = (
            f"SELECTED CAPABILITY `{skill_id}` (apply once; ProductSpec style/plan are final; "
            "do not add planning, redesign or proof turns):\n"
            f"{excerpt}"
        )
        sections.append(section)
        remaining -= len(section) + 2
    return "\n\n".join(sections)[: max(0, min(int(max_chars), 8_000))]


def _tokens(prompt: str) -> tuple[str, ...]:
    lowered = (prompt or "").lower()
    words = [word.lower() for word in _WORD_RE.findall(lowered)[:80]]
    for needle, hints in _RU_HINTS:
        if needle in lowered:
            words.extend(hints)
    return tuple(dict.fromkeys(words))


def _stable_seed(project_id: str) -> int:
    digest = hashlib.sha256((project_id or "max").encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _ui_ux_pro_evidence(prompt: str, project_id: str) -> str:
    """Return compact, project-matched evidence from vendored ui-ux-pro-max.

    The values are candidates, not a selected preset.  Gemini still owns the
    direction and may adapt or reject every candidate when the brief calls for a
    stronger solution.
    """
    tokens = _tokens(prompt)
    seed = _stable_seed(project_id)
    palette = skill_library.lookup_palette(*tokens)
    fonts = skill_library.lookup_font_pairing(*tokens)
    patterns = skill_library.lookup_design_patterns(*tokens, limit=3)
    guidelines = skill_library.lookup_filtered_ux_guidelines(
        *tokens, severity="High", limit=5, seed=seed
    )
    icon = skill_library.lookup_icon_family(*tokens)
    charts = (
        skill_library.lookup_chart_types(*tokens, limit=3)
        if any(signal in (prompt or "").lower() for signal in _DATA_SIGNALS)
        else ()
    )

    sections = [
        "PLUGIN EVIDENCE: ui-ux-pro-max (project-matched, not a visual prescription).",
        "Treat these as raw material for the three art directions. You may combine, "
        "transform or reject them; never copy a preset wholesale.",
    ]
    if palette:
        sections.append(
            "Accessible colour candidate: "
            f"{palette['product_type']} · primary {palette['primary']} / "
            f"on-primary {palette['on_primary']} · accent {palette['accent']} / "
            f"on-accent {palette['on_accent']} · background {palette['background']} / "
            f"foreground {palette['foreground']} · destructive {palette['destructive']}."
        )
    if fonts:
        sections.append(
            "Typography candidate: "
            f"{fonts['heading']} + {fonts['body']} ({fonts['keywords']}); "
            f"CSS import: {fonts['css_import']}"
        )
    if patterns:
        sections.append("Possible visual languages to debate, not templates:")
        for pattern in patterns:
            sections.append(
                f"- {pattern['name']}: {pattern['vibe_tags']}; "
                f"{pattern['summary'][:180]} (usability {pattern['usability_score']}/10)."
            )
    # The upstream library may recommend Phosphor/Font Awesome, while the MAX
    # starter intentionally ships only lucide-react. Never turn optional design
    # evidence into a missing dependency and a red build.
    if icon and "lucide" in icon["library"].lower():
        sections.append(
            f"Icon evidence: {icon['library']} / {icon['style']}; keep one coherent family."
        )
    if charts:
        sections.append("Data-viz candidates:")
        for chart in charts:
            sections.append(
                f"- {chart['data_type']} → {chart['best_chart']}; use when "
                f"{chart['when_to_use'][:140]}; avoid when {chart['when_not'][:120]}."
            )
    if guidelines:
        sections.append("High-severity UX evidence:")
        for rule in guidelines:
            sections.append(
                f"- {rule['category']}/{rule['issue']}: DO {rule['do']}; DON'T {rule['dont']}."
            )
    return "\n".join(sections)


def read_max_skill(skill_id: str, *, prompt: str, project_id: str) -> dict[str, Any]:
    """Load one MAX capability pack with a structured recovery observation."""
    loaded = agent_builder.load_stack_skill(_MAX_TEMPLATE, skill_id)
    if loaded is None:
        return {
            "ok": False,
            "status": "error",
            "summary": f"Unknown MAX capability pack: {skill_id!r}.",
            "next_actions": [
                "Read the capability catalog already present in the system prompt.",
                "Retry read_skill with one exact slug from that catalog.",
                "Stop loading skills and implement directly if no pack is relevant.",
            ],
            "artifacts": [],
            "error": (
                f"status: error\nsummary: unknown skill {skill_id!r}\n"
                "next_actions:\n- use one exact catalog slug\n"
                "- continue without a skill if none applies\nartifacts: []"
            ),
        }

    path, body = loaded
    if skill_id.strip().lower() == "ui-ux-pro-max":
        body = f"{body.rstrip()}\n\n{_ui_ux_pro_evidence(prompt, project_id)}"
    content = (
        "status: success\n"
        f"summary: loaded optional capability pack {skill_id}\n"
        "next_actions:\n"
        "- extract only principles relevant to the current product bottleneck\n"
        "- reconcile them with the brief and existing art direction\n"
        "- implement and prove the result with build/runtime_check/see\n"
        f"artifacts:\n- {path}\n\n{body.strip()}"
    )
    return {
        "ok": True,
        "status": "success",
        "summary": f"Loaded {skill_id}.",
        "next_actions": ["Apply relevant principles, then verify the result."],
        "artifacts": [path],
        "content": content,
    }
