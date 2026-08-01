"""Product-fidelity contract for full MAX Mini App generations.

The MAX runtime scaffold is deliberately buildable before the model starts.  A
green compiler result therefore proves only that the platform substrate still
works; it does not prove that the requested product was built.  This module
turns the user's explicit brief into a small deterministic acceptance checklist
that the native agent must satisfy before ``done`` is accepted.

The checks intentionally stay source/evidence based.  They do not judge taste
or invent requirements: they look only for capabilities the user named.  MAX
preview authentication is different from the generic web-app harness. Visual
review receives a signed MAX preview session; generic persistence/isolation
tools stay advisory and must never turn a clean product into another model loop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

_CAPABILITIES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "workouts",
        "тренировки",
        (r"трениров", r"workout", r"exercise"),
        ("трениров", "workout", "exercise"),
    ),
    (
        "sleep",
        "сон и восстановление",
        (r"\bсон\b", r"\bсн(?:а|ом|е|у)\b", r"sleep", r"восстановлен"),
        ("сон", "сна", "sleep", "восстановлен"),
    ),
    (
        "nutrition",
        "питание",
        (r"питан", r"рацион", r"калори", r"nutrition", r"meal"),
        ("питан", "рацион", "калори", "nutrition", "meal"),
    ),
    (
        "statistics",
        "статистика и графики",
        (r"статист", r"график", r"динамик", r"chart", r"analytics"),
        ("статист", "график", "динамик", "chart", "analytics"),
    ),
    (
        "history",
        "история действий",
        (r"истори", r"history", r"timeline"),
        ("истори", "history", "timeline"),
    ),
    (
        "profile",
        "профиль пользователя",
        (r"профил", r"profile", r"account"),
        ("профил", "profile", "account"),
    ),
    (
        "notifications",
        "уведомления",
        (r"уведом", r"напомин", r"notification", r"reminder"),
        ("уведом", "напомин", "notification", "reminder"),
    ),
    (
        "search",
        "поиск",
        (r"поиск", r"search"),
        ("поиск", "search"),
    ),
    (
        "filters",
        "фильтры",
        (r"фильтр", r"filter"),
        ("фильтр", "filter"),
    ),
    (
        "booking",
        "запись и бронирование",
        (r"бронир", r"запис[ьи]", r"booking", r"slot"),
        ("бронир", "запис", "booking", "slot"),
    ),
    (
        "catalog",
        "каталог",
        (r"каталог", r"catalog", r"товар"),
        ("каталог", "catalog", "товар"),
    ),
    (
        "loyalty",
        "лояльность и баллы",
        (r"лояльн", r"балл", r"loyalty", r"reward"),
        ("лояльн", "балл", "loyalty", "reward"),
    ),
)

_AI_PROMPT_RE = re.compile(
    r"(?:\bии\b|искусственн\w* интеллект|ai[- ]?(?:тренер|ассистент|анализ)|"
    r"нейросет|gemini|claude|gpt)",
    re.IGNORECASE,
)
_PERSISTENCE_PROMPT_RE = re.compile(
    r"(?:сохран|истори|профил|трениров|питан|сон|уведом|заказ|запис|бронир|данн)",
    re.IGNORECASE,
)
_ASYNC_STATES_PROMPT_RE = re.compile(
    r"(?:loading|empty|error|retry|загрузк|пуст\w*|ошибк|повтор)",
    re.IGNORECASE,
)

_PRODUCT_SUFFIXES = (".ts", ".tsx", ".css")
_NON_PRODUCT_PATHS = {
    "src/app/layout.tsx",
    "src/app/globals.css",
    "src/lib/omnia/max-config.ts",
    "src/components/MaxAppProvider.tsx",
    "src/components/OmniaCompliance.tsx",
    "src/lib/omnia/client.ts",
    "src/lib/omnia/integration-client.ts",
}
_MANAGED_DB_PATHS = {
    "src/lib/db/index.ts",
    "src/lib/db/schema.ts",
    "src/app/api/max/session/route.ts",
    "src/app/api/max/webhook/route.ts",
    "src/app/api/omnia/actions/route.ts",
    "src/app/api/omnia/consents/route.ts",
    "src/app/api/omnia/events/route.ts",
    "src/app/api/omnia/preview-session/route.ts",
}

_MAX_DESIGN_SPEC_PATH = ".omnia/max-design-spec.json"
_REQUIRED_DESIGN_STATES = frozenset({"loading", "empty", "error", "success"})


def _is_product_source(path: str) -> bool:
    """Return whether a file is model-owned product UI/behaviour.

    Managed MAX files contain onboarding copy, loading/error strings and SDK
    names. Including them in the product corpus can therefore satisfy semantic
    checks before the model has built the requested product.
    """

    return (
        path.startswith("src/")
        and path.endswith(_PRODUCT_SUFFIXES)
        and path not in _NON_PRODUCT_PATHS
        and path not in _MANAGED_DB_PATHS
        and not path.startswith("src/lib/max/")
        and not path.startswith("src/app/api/max/")
        and not path.startswith("src/app/api/omnia/")
        and "/legal/" not in path
        and "/support/" not in path
    )


def requested_max_capabilities(prompt: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """Return only explicitly named product capabilities, in stable order."""

    found: list[tuple[str, str, tuple[str, ...]]] = []
    for key, label, prompt_patterns, source_needles in _CAPABILITIES:
        if any(re.search(pattern, prompt, re.IGNORECASE) for pattern in prompt_patterns):
            found.append((key, label, source_needles))
    return found


def build_max_product_contract(prompt: str) -> str:
    """Human/model-readable checklist appended to a full MAX build task."""

    capabilities = requested_max_capabilities(prompt)
    lines = [
        "MAX PRODUCT ACCEPTANCE CONTRACT (done is rejected until this is true):",
        "- Before product code, write .omnia/max-design-spec.json as valid JSON with: "
        "product_promise, primary_action, three distinct directions_considered, "
        "chosen_direction, chosen_rationale, screens, visual_system, motion and states. "
        "The three directions must differ in composition/type/density/motion, not colour. "
        "Keep this project-specific spec aligned with the final implementation so a later "
        "continuation preserves the art direction instead of inventing a new template.",
        "- No product home page or visual template exists initially. Create "
        "src/app/page.tsx, product styling, screens and navigation from scratch.",
        "- Build a coherent mobile product with real screens/views and actions. Organise "
        "the source however best fits the product: completion is judged by behaviour and "
        "brief coverage, never by an arbitrary number of files. Decorative tabs are not screens.",
        "- Every button must execute a real state change or persisted request. No decorative "
        "controls, fake timers, TODOs, simulated success or claimed integrations.",
        "- Use createMaxAction for persisted MAX user activity. Never store a provider key "
        "in source code or expose it to the browser.",
        "- Never import @/lib/db or drizzle-orm and never create parallel /api/max or "
        "/api/omnia routes. Use the managed integration client for tenant-safe reads, "
        "writes, AI, consent and events.",
        "- If the brief asks for AI, call requestOmniaAI from "
        "@/lib/omnia/integration-client. It reaches the managed Google model server-side; "
        "the exact shape is `const { answer } = await requestOmniaAI({ message, "
        "instructions, context })`. setTimeout/random/static text is not AI.",
        "- After implementation: run a clean build, runtime_check the finished home screen "
        "and see it through the signed MAX preview. Apply concrete visual findings, then "
        "rebuild/runtime-check/see again until the visual verdict is clean; "
        "do not retry unavailable QA infrastructure or generic probe/verify_isolation.",
    ]
    if capabilities:
        lines.append("- Explicit brief coverage (each needs visible UI and behaviour):")
        lines.extend(f"  - {label}" for _key, label, _needles in capabilities)
    if _ASYNC_STATES_PROMPT_RE.search(prompt):
        lines.append("  - loading, empty, error/retry and success states named in the brief")
    return "\n".join(lines)


def normalize_max_globals_css(css: str) -> str:
    """Move every CSS import ahead of generated rules and Tailwind last.

    Tailwind expands ``@import "tailwindcss"`` into hundreds of rules.  A font
    import placed immediately after it therefore becomes an illegal late import
    only in the real Next/Turbopack compiler; ``tsc --noEmit`` cannot see it.
    Keeping external imports first is deterministic and does not alter the
    model-owned product styles.
    """

    lines = css.splitlines()
    imports = [line for line in lines if line.strip().lower().startswith("@import ")]
    if not imports:
        return css

    ordered_imports = sorted(
        imports,
        key=lambda line: 1 if "tailwindcss" in line.lower() else 0,
    )
    import_order_is_safe = imports == ordered_imports
    import_location_is_safe = True
    seen_rule = False
    in_comment = False
    for line in lines:
        stripped = line.strip()
        if in_comment:
            if "*/" in stripped:
                in_comment = False
            continue
        if stripped.startswith("/*"):
            in_comment = "*/" not in stripped
            continue
        if not stripped or stripped.lower().startswith("@charset "):
            continue
        if stripped.lower().startswith("@import "):
            if seen_rule:
                import_location_is_safe = False
            continue
        seen_rule = True

    if import_order_is_safe and import_location_is_safe:
        return css

    charsets = [line for line in lines if line.strip().lower().startswith("@charset ")]
    body = [
        line for line in lines if not line.strip().lower().startswith(("@charset ", "@import "))
    ]
    while body and not body[0].strip():
        body.pop(0)

    normalized = "\n".join([*charsets, *ordered_imports, "", *body]).rstrip()
    return normalized + ("\n" if css.endswith("\n") else "")


def max_source_completion_gap(
    prompt: str,
    files: Mapping[str, str],
) -> str | None:
    """Return a source/product gap independently of runtime proof infrastructure.

    Keeping this separate lets the caller decide whether another model segment
    could materially improve the product.  A failed screenshot/login harness is
    not a source gap and therefore never authorises another paid segment.
    """

    page = files.get("src/app/page.tsx", "")
    if not page:
        return (
            "MAX product has no home page. Create src/app/page.tsx with the actual "
            "requested product before done."
        )
    if "max-generation-canvas" in page:
        return (
            "MAX product still contains the retired generation canvas. Replace "
            "src/app/page.tsx with the actual requested product before done."
        )

    spec_raw = files.get(_MAX_DESIGN_SPEC_PATH, "")
    if not spec_raw:
        return (
            "MAX product has no persistent art direction. Create "
            f"{_MAX_DESIGN_SPEC_PATH} with the three explored directions and chosen "
            "product-specific design/motion system before done."
        )
    try:
        spec = json.loads(spec_raw)
    except (TypeError, json.JSONDecodeError):
        return f"{_MAX_DESIGN_SPEC_PATH} is not valid JSON. Repair the design spec before done."
    if not isinstance(spec, dict):
        return f"{_MAX_DESIGN_SPEC_PATH} must contain one JSON object."

    required_text = ("product_promise", "primary_action", "chosen_direction", "chosen_rationale")
    missing_text = [key for key in required_text if not str(spec.get(key) or "").strip()]
    directions = spec.get("directions_considered")
    direction_names = (
        {
            str(item.get("name") if isinstance(item, dict) else item).strip().casefold()
            for item in directions
            if str(item.get("name") if isinstance(item, dict) else item).strip()
        }
        if isinstance(directions, list)
        else set()
    )
    screens = spec.get("screens")
    visual_system = spec.get("visual_system")
    motion = spec.get("motion")
    states = {str(item).strip().casefold() for item in (spec.get("states") or [])}
    if missing_text:
        return f"MAX design spec is incomplete: missing {', '.join(missing_text)}."
    if len(direction_names) < 3:
        return "MAX design spec must compare three genuinely distinct art directions."
    if not isinstance(screens, list) or not screens:
        return "MAX design spec must define the product screens/views before implementation."
    if not isinstance(visual_system, dict) or not visual_system:
        return "MAX design spec must define a project-specific visual_system."
    if not isinstance(motion, list) or not motion:
        return "MAX design spec must define purposeful interaction motion."
    missing_states = sorted(_REQUIRED_DESIGN_STATES.difference(states))
    if missing_states:
        return "MAX design spec is missing product states: " + ", ".join(missing_states) + "."

    capabilities = requested_max_capabilities(prompt)
    product_sources = [content for path, content in files.items() if _is_product_source(path)]
    corpus = "\n".join(content.lower() for content in product_sources)
    unsafe_product_db_paths = [
        path
        for path, content in files.items()
        if path.startswith("src/")
        and path.endswith((".ts", ".tsx"))
        and path not in _MANAGED_DB_PATHS
        and ("@/lib/db" in content or "drizzle-orm" in content)
    ]
    if unsafe_product_db_paths:
        return (
            "Product files bypass the managed MAX persistence boundary: "
            + ", ".join(sorted(unsafe_product_db_paths))
            + ". Remove direct DB imports and use createMaxAction/getMaxActions."
        )

    # Reject a renamed starter or a one-line feature list, but never prescribe
    # source architecture. A complete product may legitimately live in one
    # substantial page component; the former file-count rule rejected exactly
    # that shape after build + runtime + visual proof had already passed.
    if len(corpus.strip()) < 900:
        return (
            "The product implementation is still too thin. Build the actual mobile UI, "
            "navigation/views, interactions and states from the brief before done."
        )
    missing = [
        label
        for _key, label, needles in capabilities
        if not any(needle in corpus for needle in needles)
    ]
    if missing:
        return "Explicit brief capabilities are still missing: " + ", ".join(missing) + "."

    if _AI_PROMPT_RE.search(prompt):
        if "requestomniaai" not in corpus:
            return (
                "The brief requests real AI, but the product does not call requestOmniaAI. "
                "Use the managed server-side Google AI primitive; do not simulate analysis."
            )
        if re.search(r"settimeout\s*\([^)]*(?:analy|анализ|coach|тренер)", corpus, re.DOTALL):
            return "A timer is still simulating AI work. Replace it with requestOmniaAI."

    if _PERSISTENCE_PROMPT_RE.search(prompt) and not any(
        marker in corpus
        for marker in ("createmaxaction", "/api/omnia/actions", "maxbusinessactions")
    ):
        return (
            "The brief requires user data/history, but no persisted MAX action flow exists. "
            "Use createMaxAction (or a user-scoped API route) and render the saved result."
        )

    if _ASYNC_STATES_PROMPT_RE.search(prompt):
        state_groups = (
            ("loading", "загруз"),
            ("empty", "пуст"),
            ("error", "ошиб"),
            ("retry", "повтор"),
        )
        absent_states = [
            english
            for english, russian in state_groups
            if english not in corpus and russian not in corpus
        ]
        if absent_states:
            return "Named async states are missing from the UI: " + ", ".join(absent_states) + "."

    return None


def max_completion_gap(
    prompt: str,
    files: Mapping[str, str],
    evidence: Mapping[str, int],
) -> str | None:
    """Return the actionable product/runtime gap for the native MAX agent.

    A clean build is still enforced by the native loop's fact gate. The MAX-safe
    evidence is ``runtime_check`` plus one ``see`` using a signed preview session.
    Generic ``probe`` and ``verify_isolation`` require a normal web login and are
    intentionally not blocking for MAX.
    """

    source_gap = max_source_completion_gap(prompt, files)
    if source_gap:
        return source_gap
    if evidence.get("runtime_check_after_write", 0) < 1:
        return "Run runtime_check on the finished product after the last source write."
    if evidence.get("see_after_write", 0) < 1:
        return "Run see once through the signed MAX preview after the last source write."
    return None


__all__ = [
    "build_max_product_contract",
    "max_completion_gap",
    "max_source_completion_gap",
    "normalize_max_globals_css",
    "requested_max_capabilities",
]
