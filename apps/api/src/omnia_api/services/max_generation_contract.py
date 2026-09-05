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
_MUTATION_PROMPT_RE = re.compile(
    r"(?:добав|созда|сохран|запис|отмет|измен|редакт|удал|внес|логир|трек|"
    r"уч[её]т|вести|оформ|брони|заказ|оплат|отправ|зафикс|"
    r"\b(?:add|create|save|edit|update|delete|track|log|record|submit|book|order|pay)\b)",
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


def _is_managed_max_backend_path(path: str) -> bool:
    """Return whether MAX Studio, rather than generated product code, owns a path."""

    return (
        path in _MANAGED_DB_PATHS
        or path.startswith("src/app/api/max/")
        or path.startswith("src/app/api/omnia/")
    )

_FAKE_USER_DATA_RE = re.compile(
    r"(?:\b(?:demo|mock|fake|sample|fixture|placeholder|seed(?:ed)?)"
    r"[-_\s]*(?:data|dataset|records?|state|profile|history|workouts?|meals?|metrics?)\b|"
    r"\b(?:data|dataset|records?|state|profile|history|workouts?|meals?|metrics?)"
    r"[-_\s]*(?:demo|mock|fake|sample|fixture|placeholder|seed(?:ed)?)\b|"
    r"(?:демо|тестов|фиктивн|примерн|заглушк)\w*[-_\s]*"
    r"(?:данн|набор|профил|истори|трениров|питан|метрик)\w*)",
    re.IGNORECASE,
)

# Strongly user-owned state names. Immutable domain references (for example,
# REFERENCE_EXERCISES) deliberately stay outside this list: a technique catalog
# may be static, but the current user's meals/readiness/history may not be.
_CONST_LITERAL_RE = re.compile(
    r"\b(?:export\s+)?const\s+(?P<name>[A-Z_$][A-Z0-9_$]*)"
    r"\b[^\n=]*=\s*(?!\[\s*\]|\{\s*\})(?:\[|\{|[-+]?\d|[\"'`])",
    re.IGNORECASE,
)
_USER_STATE_NAMES = frozenset(
    {
        "workout",
        "workouts",
        "recentworkouts",
        "workouthistory",
        "meal",
        "meals",
        "mealtoday",
        "mealstoday",
        "readiness",
        "readinesscurrent",
        "weekload",
        "userprofile",
        "currentuser",
        "personalstats",
        "progressdata",
        "sleepdata",
        "nutritiondata",
        "activitydata",
        "dashboarddata",
        "historydata",
        "history",
        "profile",
        "progress",
        "stats",
        "metrics",
        "sessions",
        "activities",
        "usermetrics",
        "usersessions",
    }
)
_REFERENCE_CATALOG_PATH_RE = re.compile(
    r"(?:^|[/_.-])(?:reference|catalog|taxonomy|library)(?:[/_.-]|$)", re.IGNORECASE
)
_EXPLICIT_REFERENCE_PATH_RE = re.compile(
    r"(?:^|[/_.-])(?:reference|taxonomy|library)(?:[/_.-]|$)", re.IGNORECASE
)
_REFERENCE_CATALOG_DECL_RE = re.compile(
    r"\bconst\s+(?:REFERENCE_[A-Z0-9_]+|[A-Z0-9_]+_(?:CATALOG|TAXONOMY|LIBRARY))\b",
    re.IGNORECASE,
)
_DIRECT_DB_RE = re.compile(
    r"(?:@/lib/db|drizzle-orm|(?:from\s+|import\s*\(|require\s*\()\s*[\"'](?:pg|postgres)[\"'])",
    re.IGNORECASE,
)


def _direct_db_product_paths(files: Mapping[str, str]) -> dict[str, str]:
    return {
        path: content
        for path, content in files.items()
        if path.startswith("src/")
        and path.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"))
        and not _is_managed_max_backend_path(path)
        and _DIRECT_DB_RE.search(content)
    }


def _unsafe_direct_db_paths(files: Mapping[str, str]) -> list[str]:
    """Reject raw product DB access until isolation is enforced below app code.

    A source scan cannot prove that every query is tied to the authenticated MAX
    user: unrelated ``requireMaxUser``/``user.id`` text can satisfy a regex while
    a query remains unscoped.  Product persistence therefore stays on the managed
    API boundary until the runtime provides DB-enforced row isolation.
    """

    return sorted(_direct_db_product_paths(files))


def unsafe_max_backend_paths(files: Mapping[str, str]) -> list[str]:
    """Public, deterministic gate used by both native and text agent loops."""
    return _unsafe_direct_db_paths(files)


def _is_product_source(path: str) -> bool:
    return (
        path.startswith("src/")
        and path.endswith(_PRODUCT_SUFFIXES)
        and path not in _NON_PRODUCT_PATHS
        and path not in _MANAGED_DB_PATHS
        and not path.startswith("src/lib/max/")
        and not path.startswith("src/lib/omnia/")
        and not path.startswith("src/app/api/max/")
        and not path.startswith("src/app/api/omnia/")
        and "/legal/" not in path
        and "/support/" not in path
    )


def _fake_user_data_paths(files: Mapping[str, str]) -> list[str]:
    def has_hardcoded_user_state(content: str) -> bool:
        return any(
            match.group("name").replace("_", "").replace("$", "").lower() in _USER_STATE_NAMES
            for match in _CONST_LITERAL_RE.finditer(content)
        )

    hits: list[str] = []
    for path, content in files.items():
        if not _is_product_source(path):
            continue
        is_reference_catalog = bool(
            _EXPLICIT_REFERENCE_PATH_RE.search(path)
            or (
                _REFERENCE_CATALOG_PATH_RE.search(path)
                and _REFERENCE_CATALOG_DECL_RE.search(content)
            )
        )
        if not is_reference_catalog and (
            has_hardcoded_user_state(content) or _FAKE_USER_DATA_RE.search(content)
        ):
            hits.append(path)
    return sorted(hits)


def _explicit_capability_mentions(prompt: str, pattern: str) -> bool:
    # Match word beginnings, not arbitrary substrings: демонстрационные and
    # интеграционные are not requests for рацион (nutrition).
    for match in re.finditer(r"\b(?:" + pattern + r")\w*", prompt, re.IGNORECASE):
        before = re.split(
            r"[.!?;\n]|\b(?:но|зато|but)\b|,\s*(?=(?:нужен|нужна|нужны|нужно|"
            r"добавь|добавьте|сделай|реализуй|покажи|add|include)\b)",
            prompt[:match.start()], flags=re.IGNORECASE,
        )[-1]
        after = re.split(r"[.!?;,\n]", prompt[match.end():], maxsplit=1)[0]
        # Deliberately narrow negation handling: no inferred requirements from
        # explicit exclusions. A later positive mention can still request it.
        negated_before = re.search(
            r"\b(?:без|without|no|не\s+(?!только\b)(?:нуж\w*|добав\w*|дела\w*|"
            r"показыв\w*|требу\w*)|do\s+not\s+(?:add|include))\b[^.!?;\n]*$",
            before, re.IGNORECASE,
        )
        negated_after = re.match(
            r"\s+(?:не\s+(?:нуж\w*|требу\w*)|is\s+not\s+required)\b",
            after, re.IGNORECASE,
        )
        if not negated_before and not negated_after:
            return True
    return False


def requested_max_capabilities(prompt: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """Return only explicitly named product capabilities, in stable order."""

    found: list[tuple[str, str, tuple[str, ...]]] = []
    for key, label, prompt_patterns, source_needles in _CAPABILITIES:
        if any(_explicit_capability_mentions(prompt, pattern) for pattern in prompt_patterns):
            found.append((key, label, source_needles))
    return found


def build_max_product_contract(prompt: str, *, portable: bool = False) -> str:
    """Human/model-readable checklist appended to a full MAX build task."""

    capabilities = requested_max_capabilities(prompt)
    if portable:
        return (
            "PORTABLE MAX PRODUCT ACCEPTANCE: implement the complete requested product, "
            "declare real build/test/service commands, then pass build and signed runtime_check. "
            "Do not fabricate user data or integration success. Explicit brief coverage: "
            + ", ".join(label for _key, label, _needles in capabilities)
        )
    lines = [
        "MAX PRODUCT ACCEPTANCE CONTRACT (done is rejected until this is true):",
        "- No product home page or visual template exists initially. Create "
        "src/app/page.tsx, product styling, screens and navigation from scratch.",
        "- Build a coherent mobile product with domain components/routes and real actions. "
        "For a multi-feature brief, one giant page.tsx with decorative tabs is insufficient.",
        "- Every button must execute a real state change or persisted request. No decorative "
        "controls, fake timers, TODOs, simulated success or claimed integrations.",
        "- Never fabricate the current user's history, profile, progress, workouts, meals or "
        "metrics with demo/mock/test constants. Load user-owned state with getMaxActions; when "
        "it is empty, show an honest empty/onboarding state. Static immutable reference "
        "catalogs are allowed only when they are not presented as the user's activity.",
        "- Use createMaxAction for persisted MAX user activity and getMaxActions to read it "
        "(page long histories with getMaxActions({ limit, cursor }) when needed). Never "
        "store a provider key in source code or expose it to the browser.",
        "- The reserved /api/max and /api/omnia routes remain platform-owned. Custom "
        "product APIs may authenticate with requireMaxUser(), but must not import the raw "
        "project DB/Drizzle/pg client. Persist user state only through the managed "
        "integration client, which enforces MAX identity outside generated product code.",
        "- If the brief asks for AI, call requestOmniaAI from "
        "@/lib/omnia/integration-client. It reaches the managed Google model server-side; "
        "the exact shape is `const { answer } = await requestOmniaAI({ message, "
        "instructions, context })`. setTimeout/random/static text is not AI.",
        "- After implementation: run a clean build, runtime_check the finished home screen "
        "through the signed MAX preview and fix real runtime failures. "
        "Do not retry incompatible generic probe/verify_isolation.",
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
    *,
    portable: bool = False,
) -> str | None:
    """Return a source/product gap independently of runtime proof infrastructure.

    Keeping this separate lets the caller decide whether another model segment
    could materially improve the product.  A failed screenshot/login harness is
    not a source gap and therefore never authorises another paid segment.
    """

    if portable:
        from omnia_api.services.portable_cell_contract import portable_source_gap

        return portable_source_gap(files, requested_max_capabilities(prompt))
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
    product_sources = {path: content for path, content in files.items() if _is_product_source(path)}
    corpus = "\n".join(content.lower() for content in product_sources.values())
    unsafe_product_db_paths = _unsafe_direct_db_paths(files)
    if unsafe_product_db_paths:
        return (
            "Direct project DB access is unavailable until DB-enforced MAX-user row "
            "isolation exists. Unsafe files: "
            + ", ".join(sorted(unsafe_product_db_paths))
            + ". Use createMaxAction/getMaxActions from the managed integration client; "
            "a requireMaxUser/user.id string check is not a security boundary."
        )
    fake_user_data_paths = _fake_user_data_paths(files)
    if fake_user_data_paths:
        return (
            "Product ships demo/test or hardcoded personal user data in: "
            + ", ".join(fake_user_data_paths)
            + ". Remove fabricated history/profile/progress/workouts/meals/metrics, load "
            "user-scoped state with getMaxActions and render an honest empty/onboarding "
            "state. Immutable reference catalogs are allowed only when clearly separate "
            "from the user's activity."
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

    if _PERSISTENCE_PROMPT_RE.search(prompt):
        managed_read = bool(re.search(r"\bgetmaxactions\s*\(", corpus))
        managed_write = bool(re.search(r"\bcreatemaxaction\s*\(", corpus))
        missing_persistence: list[str] = []
        if not managed_read:
            missing_persistence.append("authenticated read")
        if _MUTATION_PROMPT_RE.search(prompt) and not managed_write:
            missing_persistence.append("authenticated write")
        if missing_persistence:
            return (
                "The brief requires user data/history, but the product does not complete "
                "an authenticated persistence loop. Missing: "
                + ", ".join(missing_persistence)
                + ". Use createMaxAction/getMaxActions. Render "
                "loading/empty/error/success states; scaffold definitions do not count."
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
    *,
    portable: bool = False,
) -> str | None:
    """Return the actionable product/runtime gap for the native MAX agent.

    A clean build is still enforced by the native loop's fact gate. The MAX-safe
    evidence is ``runtime_check`` using a signed preview session.
    Generic ``probe`` and ``verify_isolation`` require a normal web login and are
    intentionally not blocking for MAX.
    """

    source_gap = max_source_completion_gap(prompt, files, portable=portable)
    if source_gap:
        return source_gap
    if evidence.get("runtime_check_after_write", 0) < 1:
        return "Run runtime_check on the finished product after the last source write."
    return None


__all__ = [
    "build_max_product_contract",
    "max_completion_gap",
    "max_source_completion_gap",
    "normalize_max_globals_css",
    "requested_max_capabilities",
    "unsafe_max_backend_paths",
]
