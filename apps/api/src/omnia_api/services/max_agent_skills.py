"""On-demand capability packs for the native Google MAX agent.

The catalog lives with the MAX runtime template and is mounted read-only in
production.  Only the small INDEX is part of the invariant system prompt; full
packs are returned through ``read_skill`` when Gemini decides they are useful.
This gives the model more expertise without turning that expertise into another
visual template or permanently inflating every provider turn.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from omnia_api.services import agent_builder, skill_library

_MAX_TEMPLATE = "max-miniapp-nextjs"
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
