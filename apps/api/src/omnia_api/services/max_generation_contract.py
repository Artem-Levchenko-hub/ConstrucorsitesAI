"""Product-fidelity contract for full MAX Mini App generations.

The MAX runtime scaffold is deliberately buildable before the model starts.  A
green compiler result therefore proves only that the platform substrate still
works; it does not prove that the requested product was built.  This module
turns the user's explicit brief into a small deterministic acceptance checklist
that the native agent must satisfy before ``done`` is accepted.

The checks intentionally stay source/evidence based.  They do not judge taste
or invent requirements: they look only for capabilities the user named and for
real browser/tool evidence gathered by the same agent loop.
"""

from __future__ import annotations

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
        "- The loaded home screen is an EMPTY GENERATION CANVAS, not a product template. "
        "Replace it completely; do not merely recolour or rename it.",
        "- Build a coherent mobile product with domain components/routes and real actions. "
        "For a multi-feature brief, one giant page.tsx with decorative tabs is insufficient.",
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
        "- After implementation: clean build, runtime_check key routes, probe persisted "
        "actions, then see the main screen at desktop+mobile. Apply the visual findings and "
        "run see again before done.",
    ]
    if capabilities:
        lines.append("- Explicit brief coverage (each needs visible UI and behaviour):")
        lines.extend(f"  - {label}" for _key, label, _needles in capabilities)
    if _ASYNC_STATES_PROMPT_RE.search(prompt):
        lines.append("  - loading, empty, error/retry and success states named in the brief")
    return "\n".join(lines)


def max_completion_gap(
    prompt: str,
    files: Mapping[str, str],
    evidence: Mapping[str, int],
) -> str | None:
    """Return a concrete rejection message, or ``None`` when the build is complete.

    ``files`` is the effective source tree (baseline/seed plus model writes).
    ``evidence`` contains successful native-tool counts and ``*_after_write``
    markers maintained by the loop.
    """

    page = files.get("src/app/page.tsx", "")
    if not page or "max-generation-canvas" in page:
        return (
            "MAX product is still the empty generation canvas. Replace "
            "src/app/page.tsx with the actual requested product before done."
        )

    capabilities = requested_max_capabilities(prompt)
    corpus = "\n".join(
        content.lower()
        for path, content in files.items()
        if path.startswith("src/") and path.endswith(_PRODUCT_SUFFIXES)
    )
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
    missing = [
        label
        for _key, label, needles in capabilities
        if not any(needle in corpus for needle in needles)
    ]
    if missing:
        return "Explicit brief capabilities are still missing: " + ", ".join(missing) + "."

    product_files = [
        path
        for path, content in files.items()
        if path.startswith("src/")
        and path.endswith((".ts", ".tsx"))
        and path not in _NON_PRODUCT_PATHS
        and not path.startswith("src/lib/max/")
        and not path.startswith("src/app/api/max/")
        and not path.startswith("src/app/api/omnia/")
        and "/legal/" not in path
        and "/support/" not in path
        and len(content.strip()) >= 180
    ]
    minimum_files = 4 if len(capabilities) >= 4 else 2
    if len(product_files) < minimum_files:
        return (
            f"Product breadth is too thin ({len(product_files)}/{minimum_files} meaningful "
            "product files). Split the domain into real screens/components/API behaviour; "
            "do not ship a cosmetic single-page patch."
        )

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

    if evidence.get("runtime_check_after_write", 0) < 1:
        return "Run runtime_check on the finished product after the last source write."
    if evidence.get("see_after_write", 0) < 1:
        return "Run see on the finished product after the last source write."
    if len(capabilities) >= 4 and evidence.get("see", 0) < 2:
        return (
            "This is a multi-feature product: run a second see after applying the first "
            "desktop/mobile visual critique."
        )
    if _PERSISTENCE_PROMPT_RE.search(prompt) and evidence.get("probe", 0) < 1:
        return "Prove at least one persisted user action end-to-end with probe before done."
    return None


__all__ = [
    "build_max_product_contract",
    "max_completion_gap",
    "requested_max_capabilities",
]
