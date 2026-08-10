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
import posixpath
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
_YOOKASSA_PROMPT_RE = re.compile(r"(?:ю\s*(?:касс|kassa)|yoo\s*kassa)", re.IGNORECASE)
_IIKO_PROMPT_RE = re.compile(r"(?:\biiko\b|\bайко\b)", re.IGNORECASE)
_PERSISTENCE_PROMPT_RE = re.compile(
    r"(?:сохран|истори|профил|трениров|питан|сон|уведом|заказ|запис|бронир|данн)",
    re.IGNORECASE,
)
_ASYNC_STATES_PROMPT_RE = re.compile(
    r"(?:loading|empty|error|retry|загрузк|пуст\w*|ошибк|повтор)",
    re.IGNORECASE,
)
_GENERIC_IDENTITY_FALLBACK_RE = re.compile(
    r"""(?:
        (?:\?\?|\|\|)\s*["'`](?:Пользователь|Гость|User|Guest)(?:\s+MAX)?["'`]
        |>\s*(?:Пользователь|Гость|User|Guest)(?:\s+MAX)?\s*<
        |\b(?:displayName|userName|profileName|firstName|greetingName)\b
            \s*=\s*["'`](?:Пользователь|Гость|User|Guest)(?:\s+MAX)?["'`]
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_UNSAFE_MANAGED_CONFIG_CAST_RE = re.compile(
    r"\bomniaMaxConfig\s+as\s+(?:any|unknown)\b",
    re.IGNORECASE,
)
_UNSAFE_FORMATTED_PRICE_NUMBER_RE = re.compile(
    r"typeof\s+[A-Za-z_$][\w$]*\.price\s*===\s*['\"]string['\"]"
    r"\s*\?\s*Number\s*\(\s*[A-Za-z_$][\w$]*\.price\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_SEEDED_COLLECTION_RE = re.compile(
    r"\b(?:const|let|var)\s+"
    r"(?:(?P<name>[A-Za-z_$][\w$]*)|"
    r"\[\s*(?P<state_name>[A-Za-z_$][\w$]*)\s*,[^\]]+\])"
    r"\s*(?::[^=;\n]+)?=\s*"
    r"(?:useState(?:<[^;\n()]+>)?\s*\(\s*)?"
    r"\[\s*\{(?P<body>[^}]{0,2000})\}",
    re.IGNORECASE | re.DOTALL,
)
_SEEDED_RECORD_FIELD_RE = re.compile(
    r"\b(?:userId|maxUserId|createdAt|completedAt|duration|reps|sets|weight|"
    r"calories|price|bookingId|orderId|workoutId|appointmentId|messageId)\s*:",
    re.IGNORECASE,
)
_SEEDED_USER_RECORD_KEY_RE = re.compile(
    r"(?:userId|maxUserId|createdAt|completedAt|happenedAt|performedAt|finishedAt|"
    r"startedAt|lastCompletedAt|date|status|completed|done|progress|streak|first_?name|"
    r"last_?name|full_?name|email|avatar|photo_?url|username|bookingId|orderId|"
    r"workoutId|appointmentId|messageId)",
    re.IGNORECASE,
)
_SEEDED_LIFECYCLE_KEY_RE = re.compile(
    r"(?:userId|maxUserId|createdAt|completedAt|happenedAt|performedAt|finishedAt|"
    r"startedAt|lastCompletedAt|date|status|completed|done|progress|streak|bookingId|"
    r"orderId|workoutId|appointmentId|messageId)",
    re.IGNORECASE,
)
_SEEDED_PROFILE_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"\s*(?::[^=;\n]+)?=\s*\{[^}]{0,1200}"
    r"(?:first_?name|last_?name|full_?name|email|avatar|photo_?url|username)"
    r"\s*:\s*['\"][^'\"]+['\"]",
    re.IGNORECASE | re.DOTALL,
)
_SEEDED_NAME_PARTS = (
    "appointment",
    "booking",
    "demo",
    "fake",
    "fixture",
    "history",
    "message",
    "metric",
    "mock",
    "order",
    "progress",
    "record",
    "sample",
    "seed",
    "workout",
)
_STATIC_CATALOG_NAME_PARTS = (
    "catalog",
    "class",
    "course",
    "dict",
    "dish",
    "exercise",
    "item",
    "label",
    "library",
    "lookup",
    "mapping",
    "menu",
    "offering",
    "plan",
    "product",
    "program",
    "service",
    "template",
)
_FAKE_COLLECTION_NAME_PARTS = ("demo", "fake", "fixture", "mock", "sample", "seed")
_USER_ACTIVITY_NAME_PARTS = (
    "appointment",
    "booking",
    "history",
    "message",
    "metric",
    "order",
    "progress",
    "record",
    "user",
)

_PRODUCT_SUFFIXES = (".ts", ".tsx", ".css")
_NON_PRODUCT_PATHS = {
    "src/app/layout.tsx",
    "src/app/globals.css",
    "src/lib/omnia/max-config.ts",
    "src/components/MaxAppProvider.tsx",
    "src/components/OmniaCompliance.tsx",
    "src/components/OmniaProductRuntime.tsx",
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
MAX_REQUIRED_PREWRITE_SKILLS = (
    "ui-ux-pro-max",
    "product-flow",
    "art-direction",
    "production-readiness",
)
MAX_REQUIRED_POST_SEE_SKILL = "visual-evaluation"


def _prompt_requires_provider(prompt: str, provider_re: re.Pattern[str]) -> bool:
    """Respect an explicit provider removal in an incremental MAX edit."""

    if provider_re.search(prompt) is None:
        return False
    current = prompt.rsplit("ТЕКУЩАЯ ПРАВКА:", 1)[-1]
    provider = f"(?:{provider_re.pattern})"
    removal = re.compile(
        rf"(?:убер\w*|удал\w*|отключ\w*|исключ\w*|remove\w*|disable\w*|without|без|"
        rf"не\s+нуж\w*)\s+(?:интеграц\w*\s+(?:с\s+)?|оплат\w*\s+через\s+)?{provider}",
        re.IGNORECASE,
    )
    return removal.search(current) is None


def _starts_js_regex(content: str, index: int) -> bool:
    previous = index - 1
    while previous >= 0 and content[previous].isspace():
        previous -= 1
    return previous < 0 or content[previous] in "=(:,[!&|?{};>"


def _strip_js_non_code(content: str, *, keep_strings: bool) -> str:
    """Blank JS/TS comments and, optionally, string literals while keeping offsets."""

    chars = list(content)
    index = 0
    state = "code"
    quote = ""
    while index < len(chars):
        char = chars[index]
        next_char = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "code":
            if char == "/" and next_char == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if char == "/" and _starts_js_regex(content, index):
                state = "regex"
                chars[index] = " "
                index += 1
                continue
            if char in {"'", '"', "`"}:
                quote = char
                state = "string"
                if not keep_strings:
                    chars[index] = " "
            index += 1
            continue
        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                chars[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "code"
            else:
                if char != "\n":
                    chars[index] = " "
                index += 1
            continue
        if state == "regex":
            if char == "\\":
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                index += 2
                continue
            if char == "/":
                chars[index] = " "
                state = "code"
            elif char != "\n":
                chars[index] = " "
            index += 1
            continue
        if char == "\\":
            if not keep_strings:
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
            index += 2
            continue
        if char == quote:
            if not keep_strings:
                chars[index] = " "
            state = "code"
            quote = ""
        elif not keep_strings and char != "\n":
            chars[index] = " "
        index += 1
    return "".join(chars)


def _js_code_mask(content: str) -> list[bool]:
    """Mark offsets parsed as JS/TS code rather than comments or strings."""

    mask = [False] * len(content)
    index = 0
    state = "code"
    quote = ""
    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""
        if state == "code":
            if char == "/" and next_char in {"/", "*"}:
                state = "line_comment" if next_char == "/" else "block_comment"
                index += 2
                continue
            if char in {"'", '"', "`"}:
                quote = char
                state = "string"
                index += 1
                continue
            if char == "/" and _starts_js_regex(content, index):
                state = "regex"
                index += 1
                continue
            mask[index] = True
            index += 1
            continue
        if state == "line_comment":
            if char == "\n":
                mask[index] = True
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                index += 2
                state = "code"
            else:
                index += 1
            continue
        if state == "regex":
            if char == "\\":
                index += 2
                continue
            if char == "/":
                state = "code"
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char == quote:
            state = "code"
            quote = ""
        index += 1
    return mask


def _seeded_collection_literal(content: str, match: re.Match[str]) -> str:
    """Return the matched array without trusting brackets inside JS strings/comments."""

    body_start = match.start("body")
    opening = content.rfind("[", match.start(), body_start)
    if opening < 0:
        return str(match.group(0) or "")
    code_mask = _js_code_mask(content)
    depth = 0
    for index in range(opening, len(content)):
        if not code_mask[index]:
            continue
        char = content[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return content[opening : index + 1]
    return content[opening:]


def _managed_max_content_is_populated(files: Mapping[str, str]) -> bool:
    """Return whether MAX Studio supplied at least one canonical content item."""

    config = str(files.get("src/lib/omnia/max-config.ts") or "")
    match = re.search(r"(?:['\"]content['\"]|\bcontent\b)\s*:\s*\[", config)
    if not match:
        return False
    opening = config.find("[", match.start(), match.end())
    if opening < 0:
        return False
    code_mask = _js_code_mask(config)
    depth = 0
    for index in range(opening, len(config)):
        if not code_mask[index]:
            continue
        char = config[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return bool(config[opening + 1 : index].strip())
    return False


def _populated_fallback_catalog_name(content: str) -> str | None:
    """Find a model-owned fallback catalog that can hide broken managed data."""

    for match in _SEEDED_COLLECTION_RE.finditer(content or ""):
        name = str(match.group("name") or match.group("state_name") or "")
        name_folded = name.casefold()
        if "fallback" not in name_folded:
            continue
        if not any(part in name_folded for part in _STATIC_CATALOG_NAME_PARTS):
            continue
        keys = _js_object_keys(_seeded_collection_literal(content or "", match))
        if {"name", "title"}.intersection(keys) and "price" in keys:
            return name
    return None


_PRODUCT_IMPORT_RE = re.compile(
    r"(?:\bfrom\s*|\bimport\s*)['\"](?P<module>[^'\"]+)['\"]",
    re.IGNORECASE,
)


def _reachable_product_sources(files: Mapping[str, str]) -> dict[str, str]:
    """Follow product imports so stale snapshot files cannot block completion."""

    roots = [
        path
        for path in ("src/components/product/ProductApp.tsx", "src/app/page.tsx")
        if path in files
    ]
    if not roots:
        return {path: content for path, content in files.items() if _is_product_source(path)}

    reachable: dict[str, str] = {}
    pending = list(roots)
    while pending:
        path = pending.pop()
        if path in reachable or path not in files or not _is_product_source(path):
            continue
        content = str(files[path])
        reachable[path] = content
        for match in _PRODUCT_IMPORT_RE.finditer(_strip_js_non_code(content, keep_strings=True)):
            module = str(match.group("module") or "")
            if module.startswith("@/"):
                base = "src/" + module[2:]
            elif module.startswith("."):
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), module))
            else:
                continue
            candidates = (
                base,
                *(base + suffix for suffix in _PRODUCT_SUFFIXES),
                *(posixpath.join(base, "index" + suffix) for suffix in _PRODUCT_SUFFIXES),
            )
            resolved = next((candidate for candidate in candidates if candidate in files), None)
            if resolved is not None and resolved not in reachable:
                pending.append(resolved)
    return reachable


def _js_object_keys(content: str) -> set[str]:
    """Extract bare and quoted JS object keys, ignoring values and comments."""

    keys: set[str] = set()
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        next_char = content[index + 1] if index + 1 < length else ""
        if char == "/" and next_char == "/":
            newline = content.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if char == "/" and next_char == "*":
            closing = content.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if char == "/" and _starts_js_regex(content, index):
            index += 1
            while index < length:
                if content[index] == "\\":
                    index += 2
                    continue
                if content[index] == "/":
                    index += 1
                    while index < length and content[index].isalpha():
                        index += 1
                    break
                index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            token: list[str] = []
            while index < length:
                char = content[index]
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    index += 1
                    break
                token.append(char)
                index += 1
            lookahead = index
            while lookahead < length and content[lookahead].isspace():
                lookahead += 1
            if lookahead < length and content[lookahead] == ":":
                keys.add("".join(token))
            continue
        if char.isalpha() or char in {"_", "$"}:
            start = index
            index += 1
            while index < length and (content[index].isalnum() or content[index] in {"_", "$"}):
                index += 1
            lookahead = index
            while lookahead < length and content[lookahead].isspace():
                lookahead += 1
            if lookahead < length and content[lookahead] == ":":
                keys.add(content[start:index])
            continue
        index += 1
    return keys


def _has_managed_named_import(content: str, symbol: str, module: str) -> bool:
    code_mask = _js_code_mask(content)
    content = _strip_js_non_code(content, keep_strings=True)
    import_re = re.compile(
        rf"import\s*\{{(?P<specifiers>[^}}]*)\}}\s*"
        rf"from\s*['\"]{re.escape(module)}['\"]",
        re.IGNORECASE | re.DOTALL,
    )
    exact_symbol_re = re.compile(re.escape(symbol), re.IGNORECASE)
    for match in import_re.finditer(content):
        if match.start() >= len(code_mask) or not code_mask[match.start()]:
            continue
        specifiers = re.sub(r"/\*.*?\*/", "", match.group("specifiers"), flags=re.DOTALL)
        if any(exact_symbol_re.fullmatch(item.strip()) for item in specifiers.split(",")):
            return True
    return False


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


def max_demo_data_rejection(path: str, content: str) -> str | None:
    """Reject executable seeded user records in model-owned MAX product source.

    Instructional prose remains valid; the gate targets non-empty record arrays
    and literal manufactured profiles rather than merely matching words such as
    ``sample`` in UI copy.
    """

    if not _is_product_source(path):
        return None
    matched_name = ""
    for match in _SEEDED_COLLECTION_RE.finditer(content or ""):
        name = str(match.group("name") or match.group("state_name") or "")
        body = str(match.group("body") or "")
        collection = _seeded_collection_literal(content or "", match)
        collection_keys = _js_object_keys(collection)
        # Product reference content (menus, products, services, exercise/workout
        # libraries, programme templates) is not manufactured user history. It
        # may contain price, duration, sets or reps, but never user identity or
        # activity lifecycle fields.
        # Keeping this distinction avoids forcing a useful fresh app into an
        # empty catalog while still rejecting fake completed records.
        name_folded = name.casefold()
        static_reference = any(part in name_folded for part in _STATIC_CATALOG_NAME_PARTS)
        ui_lookup = any(
            part in name_folded for part in ("dict", "label", "lookup", "mapping")
        )
        user_activity_name = any(part in name_folded for part in _USER_ACTIVITY_NAME_PARTS)
        fake_collection_name = any(part in name_folded for part in _FAKE_COLLECTION_NAME_PARTS)
        commercial_reference = (
            "price" in collection_keys
            and bool({"label", "name", "title"}.intersection(collection_keys))
        )
        if (
            ui_lookup
            and not user_activity_name
            and not fake_collection_name
            and not any(_SEEDED_LIFECYCLE_KEY_RE.fullmatch(key) for key in collection_keys)
        ):
            continue
        if (
            static_reference
            and not user_activity_name
            and not fake_collection_name
            and not any(_SEEDED_USER_RECORD_KEY_RE.fullmatch(key) for key in collection_keys)
        ):
            continue
        # A model commonly names commercial dictionaries by the domain noun
        # (DRINKS, ADDONS, SIZES) rather than CATALOG/MENU. Their display label
        # plus price is product reference data, not manufactured user history.
        # Keep activity/fake names and every identity/lifecycle field blocking,
        # so ORDERS or DEMO_ITEMS cannot hide behind the same shape.
        if (
            commercial_reference
            and not user_activity_name
            and not fake_collection_name
            and not any(_SEEDED_USER_RECORD_KEY_RE.fullmatch(key) for key in collection_keys)
        ):
            continue
        if any(part in name_folded for part in _SEEDED_NAME_PARTS) or (
            _SEEDED_RECORD_FIELD_RE.search(body) is not None
        ):
            matched_name = name
            break
    if not matched_name:
        profile_match = _SEEDED_PROFILE_RE.search(content or "")
        if profile_match:
            name = str(profile_match.group("name") or "")
            matched_name = name
    if not matched_name:
        return None
    return (
        "Hardcoded demo user data is forbidden in a published MAX app "
        f"({matched_name}). Remove seeded records and render a truthful empty "
        "state backed by getMaxActions/createMaxAction."
    )


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
        "- No product UI or visual template exists initially. Replace "
        "src/components/product/ProductApp.tsx and create the product styling, "
        "screens and navigation from scratch. Never edit the locked root page.",
        "- Build a coherent mobile product with real screens/views and actions. Organise "
        "the source however best fits the product: completion is judged by behaviour and "
        "brief coverage, never by an arbitrary number of files. Decorative tabs are not screens.",
        "- Every button must execute a real state change or persisted request. No decorative "
        "controls, fake timers, TODOs, simulated success or claimed integrations.",
        "- Keep the chosen visual system coherent on every screen and state, including cart, "
        "checkout, success, empty/error and profile views. Do not fall back to browser/default "
        "blue buttons, generic panels or a second accent palette outside the chosen art direction.",
        "- Use semantic, accessible controls: every clickable surface is a native button or link; "
        "never nest a button/link inside another button/link or a focusable role=button wrapper. "
        "When a card has a quick action, keep the card container non-interactive and expose the "
        "details action and quick action as sibling controls. Give each screen a real h1/h2 and "
        "give icon-only/navigation buttons stable aria-label text that excludes badges/counts.",
        "- Real accounts come from validated MAX initData: the managed session creates or "
        "refreshes max_users on first open. Use useMaxApp for identity; never add password "
        "login or manufacture a profile.",
        "- Ship no hardcoded demo user data, history, metrics, orders, bookings or completed "
        "workouts. Static business menus, product/service catalogs, exercise/workout "
        "libraries and programme templates are allowed when they are clearly reference "
        "content, never user activity. "
        "A new account starts with truthful empty states and creates real persisted data "
        "through the managed client. Prefer business catalog content from omniaMaxConfig "
        "when supplied; when it is empty and the brief requires a catalog, create a compact, "
        "internally consistent starter reference catalog so the primary scenario works on "
        "first open.",
        "- omniaMaxConfig is typed and authoritative. Never cast it to any/unknown or stringify "
        "nested config objects. When omniaMaxConfig.content is populated, do not mask mapping "
        "bugs with a populated fallback catalog: render those managed items, parse formatted "
        "prices such as `149 ₽` safely, and render support fields individually.",
        "- Use createMaxAction for persisted MAX user activity. Never store a provider key "
        "in source code or expose it to the browser.",
        "- Never import @/lib/db or drizzle-orm and never create parallel /api/max or "
        "/api/omnia routes. Use the managed integration client for tenant-safe reads, "
        "writes, AI, consent and events.",
        "- If the brief asks for AI, call requestOmniaAI from "
        "@/lib/omnia/integration-client. It reaches the managed model server-side; "
        "the exact shape is `const { answer } = await requestOmniaAI({ message, "
        "instructions, context })`. setTimeout/random/static text is not AI. Ask for a "
        "concise structured answer and render it as scannable sections, steps or bullets; "
        "never dump a long unbroken AI paragraph into a generic card.",
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

    # Exact duplicate imports are easy for a repair model to create when it
    # moves a late Google Fonts line to the top. Besides an extra request, the
    # duplicate makes the next exact edit ambiguous and can trap the native
    # loop in `search text must occur exactly once` forever.
    unique_imports = list(dict.fromkeys(imports))
    ordered_imports = sorted(
        unique_imports,
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
    require_design_spec: bool = True,
) -> str | None:
    """Return a source/product gap independently of runtime proof infrastructure.

    Keeping this separate lets the caller decide whether another model segment
    could materially improve the product.  A failed screenshot/login harness is
    not a source gap and therefore never authorises another paid segment.
    """

    # Legacy snapshots (kit <=14) still carry their product in page.tsx. They
    # are migrated behind the browser-only runtime before execution, but the
    # source contract remains able to assess them during that transition.
    entry = files.get("src/components/product/ProductApp.tsx", "") or files.get(
        "src/app/page.tsx", ""
    )
    if not entry:
        return (
            "MAX product has no product entry. Create "
            "src/components/product/ProductApp.tsx with the actual "
            "requested product before done."
        )
    if "max-generation-canvas" in entry:
        return (
            "MAX product still contains the retired generation canvas. Replace "
            "src/components/product/ProductApp.tsx with the actual requested product "
            "before done."
        )

    if require_design_spec:
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

        required_text = (
            "product_promise",
            "primary_action",
            "chosen_direction",
            "chosen_rationale",
        )
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
    reachable_product_files = _reachable_product_sources(files)
    product_sources = [content for path, content in files.items() if _is_product_source(path)]
    product_source_blob = "\n".join(product_sources)
    reachable_product_source_blob = "\n".join(reachable_product_files.values())
    corpus = product_source_blob.lower()
    product_source_views = [
        (source, _strip_js_non_code(source, keep_strings=False)) for source in product_sources
    ]
    for path, content in files.items():
        demo_rejection = max_demo_data_rejection(path, content)
        if demo_rejection:
            return demo_rejection
    managed_config_gaps: list[str] = []
    if _managed_max_content_is_populated(files):
        for content in reachable_product_files.values():
            fallback_name = _populated_fallback_catalog_name(content)
            if fallback_name:
                managed_config_gaps.append(
                    "MAX Studio already supplied canonical catalog content, but product source "
                    f"still contains populated {fallback_name}. Remove the populated fallback "
                    "and render omniaMaxConfig.content so mapping failures stay visible."
                )
        if _UNSAFE_FORMATTED_PRICE_NUMBER_RE.search(reachable_product_source_blob):
            managed_config_gaps.append(
                "Managed catalog prices are formatted strings such as `149 ₽`, but product "
                "source converts a string price with Number(...). Normalize the currency text "
                "before conversion (or use parseFloat safely) so managed items are not dropped."
            )
    if _UNSAFE_MANAGED_CONFIG_CAST_RE.search(reachable_product_source_blob):
        managed_config_gaps.append(
            "Product source casts omniaMaxConfig to any/unknown, which hid an invalid nested "
            "config render. Use the exported OmniaMaxConfig types and render support fields "
            "individually; never stringify the support object."
        )
    if managed_config_gaps:
        return "Managed MAX config contract failed: " + " ".join(managed_config_gaps)
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
        managed_ai_call = any(
            _has_managed_named_import(source, "requestOmniaAI", "@/lib/omnia/integration-client")
            and re.search(r"\bawait\s+requestomniaai\s*\(", code, re.IGNORECASE)
            for source, code in product_source_views
        )
        if not managed_ai_call:
            return (
                "The brief requests real AI, but no product module imports and awaits "
                "requestOmniaAI from @/lib/omnia/integration-client. Use the managed "
                "server-side managed AI primitive; do not simulate analysis."
            )
        if re.search(r"settimeout\s*\([^)]*(?:analy|анализ|coach|тренер)", corpus, re.DOTALL):
            return "A timer is still simulating AI work. Replace it with requestOmniaAI."

    integration_status_call = any(
        _has_managed_named_import(source, "getOmniaIntegrations", "@/lib/omnia/integration-client")
        and re.search(r"\bawait\s+getomniaintegrations\s*\(", code, re.IGNORECASE)
        for source, code in product_source_views
    )
    yookassa_required = _prompt_requires_provider(prompt, _YOOKASSA_PROMPT_RE)
    iiko_required = _prompt_requires_provider(prompt, _IIKO_PROMPT_RE)
    if (yookassa_required or iiko_required) and not integration_status_call:
        return (
            "The brief names an external integration, but the UI never checks which tenant "
            "providers are connected. Import and await getOmniaIntegrations from "
            "@/lib/omnia/integration-client, then show connected and unavailable states honestly."
        )

    if yookassa_required:
        managed_payment_call = any(
            _has_managed_named_import(
                source,
                "createOmniaPayment",
                "@/lib/omnia/integration-client",
            )
            and re.search(r"\bawait\s+createomniapayment\s*\(", code, re.IGNORECASE)
            for source, code in product_source_views
        )
        if not managed_payment_call:
            return (
                "The brief requires YooKassa, but checkout never imports and awaits "
                "createOmniaPayment from @/lib/omnia/integration-client. A local order action "
                "must not simulate successful online payment."
            )
        if "confirmation_url" not in corpus:
            return (
                "The YooKassa flow ignores confirmation_url. Use the managed payment result "
                "to open or redirect to the real provider confirmation; do not render payment "
                "success immediately after a local order write."
            )

    if iiko_required:
        managed_iiko_catalog_call = any(
            _has_managed_named_import(source, "getOmniaCatalog", "@/lib/omnia/integration-client")
            and re.search(r"\bawait\s+getomniacatalog\s*\(", code, re.IGNORECASE)
            for source, code in product_source_views
        )
        if not managed_iiko_catalog_call:
            return (
                "The brief requires iiko, but the product never imports and awaits "
                "getOmniaCatalog from @/lib/omnia/integration-client. Load the connected "
                "restaurant catalog and render an honest fallback/error when it is unavailable."
            )

    managed_identity_call = any(
        _has_managed_named_import(source, "useMaxApp", "@/components/MaxAppProvider")
        and re.search(
            r"\b(?:const|let)\s+(?:\{[^}]+\}|[A-Za-z_$][\w$]*)\s*=\s*usemaxapp\s*\(",
            code,
            re.IGNORECASE,
        )
        for source, code in product_source_views
    )
    if not managed_identity_call:
        return (
            "The product does not consume the verified MAX account. Import useMaxApp "
            "from @/components/MaxAppProvider and call useMaxApp() in the product UI."
        )
    source_with_strings = _strip_js_non_code(product_source_blob, keep_strings=True)
    if _GENERIC_IDENTITY_FALLBACK_RE.search(source_with_strings):
        return (
            "The product renders a generic identity fallback such as Пользователь/User/Guest. "
            "Use the verified MAX first_name only when present; otherwise render neutral "
            "non-personal copy instead of inventing a profile name."
        )

    persistence_required = _PERSISTENCE_PROMPT_RE.search(prompt) is not None
    managed_create_call = any(
        _has_managed_named_import(source, "createMaxAction", "@/lib/omnia/integration-client")
        and re.search(r"\bawait\s+createmaxaction\s*\(", code, re.IGNORECASE)
        for source, code in product_source_views
    )
    managed_restore_call = any(
        _has_managed_named_import(source, "getMaxActions", "@/lib/omnia/integration-client")
        and re.search(
            r"\buseeffect\s*\(.{0,1600}?\bawait\s+getmaxactions\s*\(",
            code,
            re.IGNORECASE | re.DOTALL,
        )
        for source, code in product_source_views
    )
    if persistence_required and not managed_create_call:
        return (
            "The brief requires user data/history, but no persisted MAX action flow exists. "
            "Import createMaxAction from @/lib/omnia/integration-client and await it "
            "from a real user action."
        )
    if persistence_required and not managed_restore_call:
        return (
            "The product writes user data but does not restore it after reload. Import and "
            "await getMaxActions() from a mounted useEffect loading path."
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
    missing_skills = [
        skill for skill in MAX_REQUIRED_PREWRITE_SKILLS if evidence.get(f"skill:{skill}", 0) < 1
    ]
    if missing_skills:
        return "Read required MAX capability packs: " + ", ".join(missing_skills) + "."
    if evidence.get("runtime_check_after_write", 0) < 1:
        return "Run runtime_check on the finished product after the last source write."
    if evidence.get("see_after_write", 0) < 1:
        return "Run see once through the signed MAX preview after the last source write."
    if evidence.get("visual_evaluation_after_see", 0) < 1:
        return (
            "Read visual-evaluation after the first rendered see and apply its critique "
            "before done."
        )
    return None


__all__ = [
    "MAX_REQUIRED_POST_SEE_SKILL",
    "MAX_REQUIRED_PREWRITE_SKILLS",
    "build_max_product_contract",
    "max_completion_gap",
    "max_demo_data_rejection",
    "max_source_completion_gap",
    "normalize_max_globals_css",
    "requested_max_capabilities",
]
