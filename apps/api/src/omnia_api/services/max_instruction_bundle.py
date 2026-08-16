"""Compact, canonical MAX task facts assembled without duplicating platform prompts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from omnia_api.services.build_plan import BuildPlan

# The strict schema itself can legally carry ~8.5k characters and its explicit
# plan another ~7k. These caps bound context without cutting late integration
# or acceptance rows before the single repair pass.
MAX_PRODUCT_SPEC_TASK_MAX_CHARS = 20_000
MAX_PRODUCT_SPEC_PLAN_MAX_CHARS = 8_000
MAX_PRODUCT_SPEC_MAX_SKILLS = 8
_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _mapping(spec: Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(spec, Mapping):
        return spec
    dump = getattr(spec, "model_dump", None)
    if callable(dump):
        try:
            value = dump(mode="json")
        except (TypeError, ValueError):
            value = dump()
        if isinstance(value, Mapping):
            return value
    as_dict = getattr(spec, "dict", None)
    if callable(as_dict):
        value = as_dict()
        if isinstance(value, Mapping):
            return value
    return {}


def _text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if value is not None else ""


def _items(value: Any, *, limit: int, item_limit: int = 180) -> tuple[str, ...]:
    raw = (value,) if isinstance(value, str) else value if isinstance(value, (list, tuple)) else ()
    out: list[str] = []
    seen: set[str] = set()
    for row in raw:
        item = _text(row, item_limit)
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) == limit:
            break
    return tuple(out)


def normalize_max_skill_ids(skill_ids: Iterable[object] = ()) -> tuple[str, ...]:
    """Return bounded, canonical selected skill ids; no prompt text is trusted."""
    values = {
        candidate
        for raw in skill_ids
        if isinstance(raw, str) and _SKILL_ID_RE.fullmatch(candidate := _text(raw, 64).lower())
    }
    return tuple(sorted(values))[:MAX_PRODUCT_SPEC_MAX_SKILLS]


selected_max_skill_ids = normalize_max_skill_ids


def _bounded_lines(lines: Iterable[str], limit: int) -> str:
    """Join whole lines within a hard, visible character budget."""
    allowed = max(1, min(int(limit), MAX_PRODUCT_SPEC_TASK_MAX_CHARS))
    out: list[str] = []
    size = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        extra = len(line) + (1 if out else 0)
        if size + extra > allowed:
            remaining = allowed - size - (1 if out else 0)
            if remaining >= 2:
                out.append(line[: remaining - 1].rstrip() + "…")
            break
        out.append(line)
        size += extra
    return "\n".join(out)


def render_max_plan_checklist(
    plan: BuildPlan | None,
    *,
    max_chars: int = MAX_PRODUCT_SPEC_PLAN_MAX_CHARS,
) -> str:
    """Render only product-plan facts, not MAX runtime/system/design instructions."""
    if plan is None or plan.is_empty:
        return ""
    lines = ["PLAN CHECKLIST (implement these product facts; Omnia proves them automatically):"]
    if plan.summary:
        lines.append(f"- Outcome: {plan.summary}")
    lines.append(
        "- File pass: ProductApp.tsx composition; supporting product navigation/screens; "
        "product state/data hook when needed; globals.css visual system"
    )
    for screen in plan.screens:
        lines.append(f"- View {screen.route}: {screen.name or screen.purpose}")
    for entity in plan.entities:
        suffix = f" ({', '.join(entity.fields[:4])})" if entity.fields else ""
        lines.append(f"- Data: {entity.name}{suffix}")
    for capability in plan.capabilities:
        action = capability.action or capability.id
        action_literal = json.dumps(action, ensure_ascii=False)
        line = (
            f"- Action [{capability.id}]: {action}; control markers "
            f'data-omnia-capability="{capability.id}" and '
            f"data-omnia-capability-label={action_literal}; exactly one reachable enabled "
            f"semantic control whose accessible name matches {action_literal}; implement its "
            "real distinct flow, not a shared status toggle"
        )
        if capability.id == "primary_action":
            line += (
                '; add data-omnia-primary-action; keep CTA and real controls inside a form or '
                'data-omnia-primary-flow; after '
                'success show data-omnia-action-result="primary_action" with the action outcome '
                "and server-generated record id in data-omnia-record-id when state is created; "
                "for a catalog integration include a real value from the catalog "
                "response in that result; "
                'on a real managed request failure show role="alert" and '
                'data-omnia-action-error="primary_action", never a success marker'
            )
        lines.append(line)
    for criterion in plan.acceptance:
        lines.append(f"- Accept: {criterion}")
    return _bounded_lines(lines, min(max_chars, MAX_PRODUCT_SPEC_PLAN_MAX_CHARS))


def render_max_product_spec_task(
    spec: Mapping[str, Any] | object,
    *,
    selected_skill_ids: Iterable[object] = (),
    build_plan: BuildPlan | None = None,
    max_chars: int = MAX_PRODUCT_SPEC_TASK_MAX_CHARS,
    plan_max_chars: int = MAX_PRODUCT_SPEC_PLAN_MAX_CHARS,
) -> str:
    """Render canonical business facts for a MAX task under explicit char caps.

    This is deliberately not a second system prompt: it contains no platform
    policy, runtime API contract, or art-director prescription.  Those remain
    single-source in their existing providers.
    """
    data = _mapping(spec)
    lines = ["CANONICAL PRODUCT SPEC (business facts; do not invent missing facts):"]
    for label, key, limit in (
        ("Purpose", "purpose", 800),
        ("Audience", "audience", 400),
        ("Primary action", "primary_action", 240),
        ("Style intent", "style", 240),
    ):
        value = _text(data.get(key), limit)
        if value:
            lines.append(f"- {label}: {value}")
    action_kind = _text(data.get("primary_action_kind"), 32) or "managed_write"
    lines.append(f"- Primary action execution kind: {action_kind}")
    if data.get("history") is True:
        lines.append("- Persistent user history: required; restore it after reload")
    for label, key, limit, item_limit in (
        ("Screens", "screens", 8, 120),
        ("Capabilities", "capabilities", 8, 240),
        ("Data", "data", 8, 240),
        ("Integrations", "integrations", 6, 240),
        ("Acceptance", "acceptance", 8, 300),
    ):
        values = _items(data.get(key), limit=limit, item_limit=item_limit)
        if values:
            lines.append(f"- {label}: " + "; ".join(values))
    skills = normalize_max_skill_ids(selected_skill_ids)
    if skills:
        lines.append("- Applied capability guidance: " + ", ".join(skills))
    checklist = render_max_plan_checklist(build_plan, max_chars=plan_max_chars)
    if checklist:
        lines.append(checklist)
    return _bounded_lines(lines, max_chars)


@dataclass(frozen=True)
class MaxInstructionBundle:
    """Stable task payload plus normalized selected capability-pack ids."""

    task: str
    selected_skill_ids: tuple[str, ...]


def build_max_instruction_bundle(
    spec: Mapping[str, Any] | object,
    *,
    selected_skill_ids: Iterable[object] = (),
    build_plan: BuildPlan | None = None,
    max_chars: int = MAX_PRODUCT_SPEC_TASK_MAX_CHARS,
    plan_max_chars: int = MAX_PRODUCT_SPEC_PLAN_MAX_CHARS,
) -> MaxInstructionBundle:
    """Create deterministic, bounded task facts for one native MAX run."""
    skills = normalize_max_skill_ids(selected_skill_ids)
    return MaxInstructionBundle(
        task=render_max_product_spec_task(
            spec,
            selected_skill_ids=skills,
            build_plan=build_plan,
            max_chars=max_chars,
            plan_max_chars=plan_max_chars,
        ),
        selected_skill_ids=skills,
    )


__all__ = [
    "MAX_PRODUCT_SPEC_MAX_SKILLS",
    "MAX_PRODUCT_SPEC_PLAN_MAX_CHARS",
    "MAX_PRODUCT_SPEC_TASK_MAX_CHARS",
    "MaxInstructionBundle",
    "build_max_instruction_bundle",
    "normalize_max_skill_ids",
    "render_max_plan_checklist",
    "render_max_product_spec_task",
    "selected_max_skill_ids",
]
