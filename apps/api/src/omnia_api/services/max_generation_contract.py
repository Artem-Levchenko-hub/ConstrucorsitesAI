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
        "- No product home page or visual template exists initially. Create "
        "src/app/page.tsx, product styling, screens and navigation from scratch.",
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
        "- After implementation: run a clean build, runtime_check the finished home screen "
        "and see it once through the signed MAX preview. Apply concrete visual findings, but "
        "do not retry unavailable QA infrastructure or generic probe/verify_isolation.",
    ]
    if capabilities:
        lines.append("- Explicit brief coverage (each needs visible UI and behaviour):")
        lines.extend(f"  - {label}" for _key, label, _needles in capabilities)
    if _ASYNC_STATES_PROMPT_RE.search(prompt):
        lines.append("  - loading, empty, error/retry and success states named in the brief")
    return "\n".join(lines)


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
    "requested_max_capabilities",
]
