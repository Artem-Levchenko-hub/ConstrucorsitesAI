"""Парсер AI-ответа в формате <file path="...">...</file> + санитизация путей.

Поддерживает ДВА формата:
* ``<file path="...">{full content}</file>`` — для новых файлов или полных
  пересборок. Body заменяет содержимое целиком.
* ``<edit path="...">`` с одной или несколькими SEARCH/REPLACE-секциями
  внутри (aider-style). Body парсится, каждая секция применяется к
  существующему содержимому файла. Намного дешевле по токенам когда
  правка маленькая (точечная замена кнопки, цвета, текста), потому что
  модель не переписывает весь файл.

Парсер этого модуля чисто-функциональный: на входе текст ответа AI, на
выходе словарь "что записать". Чтение текущих файлов для apply_edits
делает caller (см. ``routers/messages.py``).
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

from omnia_api.services.lucide_icon_names import is_valid_lucide_name

log = logging.getLogger(__name__)

_FILE_BLOCK = re.compile(
    r'<file\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</file>',
    re.DOTALL,
)

# `<edit path="src/index.html">...</edit>`. Body содержит ≥1 SEARCH/REPLACE.
_EDIT_BLOCK = re.compile(
    r'<edit\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</edit>',
    re.DOTALL,
)

# aider-style маркеры внутри <edit>. Допускаются 7-символьные `<<<<<<<` и
# `>>>>>>>` (классика), а также любая длина 6-9 — некоторые модели сбиваются.
# `=======` обязателен ровно 7-символьный (это безопасно — модель сама редко
# отклоняется).
_SR_BLOCK = re.compile(
    r"<{6,9}\s*SEARCH\s*\n(?P<search>.*?)\n={7}\s*\n(?P<replace>.*?)\n>{6,9}\s*REPLACE",
    re.DOTALL,
)

_FORBIDDEN_PREFIXES = ("/", "~", ".git/", ".git\\")
_FORBIDDEN_SUBSTRINGS = ("..",)
MAX_FILES = 100
MAX_FILE_BYTES = 2 * 1024 * 1024

# The app writer reliably invents a marketing palette of CSS custom properties
# for landing/auth pages — var(--bg)/var(--fg)/var(--muted)/var(--accent)/
# var(--bg-alt) — and NEVER emits a :root block defining them. --bg/--fg/--bg-alt
# then resolve to nothing; worse, --muted/--accent COLLIDE with the kit's shadcn
# surface tokens in globals.css (oklch≈0.97, near-white), so text-[var(--muted)]/
# text-[var(--accent)] paint near-white text on a white page = invisible. A
# negative brief rule doesn't dislodge this strong prior, so we rewrite the
# arbitrary-value USAGES to the real kit tokens deterministically. Only usages
# (`var(--X)`) are touched, never a `--X:` DEFINITION, and only in generated
# source — the kit components use Tailwind utility classes (`bg-accent`), not
# `var(--accent)`, so they're left untouched. Order matters: rewrite --muted/
# --accent BEFORE re-introducing var(--muted) via --bg-alt so the alt background
# stays a light surface, not muted-foreground text colour.
_PALETTE_VAR_FIXES: tuple[tuple[str, str], ...] = (
    ("var(--muted)", "var(--muted-foreground)"),
    ("var(--accent)", "var(--primary)"),
    ("var(--bg-alt)", "var(--muted)"),
    ("var(--bg)", "var(--background)"),
    ("var(--fg)", "var(--foreground)"),
)
_PALETTE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts", ".css")


def _fix_invented_palette_vars(path: str, body: str) -> str:
    """Rewrite the writer's invented landing-palette CSS vars to real kit tokens.

    See ``_PALETTE_VAR_FIXES``. No-op unless ``path`` is a generated source file
    that actually contains an invented usage, so correctly-authored files pass
    through byte-identical (R-10 fail-soft)."""
    if not path.endswith(_PALETTE_FIX_SUFFIXES):
        return body
    for invented, token in _PALETTE_VAR_FIXES:
        if invented in body:
            body = body.replace(invented, token)
    return body


# The writer hallucinates lucide-react imports that the package does NOT export.
# Two flavours, both fatal: brand/social logos (`Telegram`, lucide ships generic
# glyphs not company logos) AND abbreviated/wrong forms of real icons (`Trend`
# for `TrendingUp`, `Dashboard` for `LayoutDashboard`, `Cart` for `ShoppingCart`).
# Any one of them breaks the Turbopack build outright — the dev container never
# serves 200, so the WHOLE generated app is dead. A negative brief rule doesn't
# stop the model reaching for `<Trend/>`, so we repair the IMPORT deterministically:
# every imported specifier is validated against the canonical lucide export set
# (`is_valid_lucide_name`); an unknown name is aliased to a valid glyph — a
# visually-adjacent brand fallback when we have one, else a neutral `Circle` — while
# its `<Name/>` usages keep their original local name via the alias. Valid names
# (including the `<Base>Icon` / `Lucide<Base>` alias forms) pass through unchanged.
#
# The brand table below is the *nicer-looking* fallback layer (Telegram→Send beats
# Telegram→Circle); keyed by lower-cased imported name so casing variants all map.
# Keys are confirmed ABSENT and values confirmed PRESENT in the template's lucide.
_LUCIDE_GENERIC_FALLBACK = "Circle"  # always a valid lucide export; neutral glyph
_LUCIDE_INVALID_ICON_FALLBACKS: dict[str, str] = {
    "telegram": "Send",
    "whatsapp": "MessageCircle",
    "vk": "Share2",
    "vkontakte": "Share2",
    "tiktok": "Music2",
    "discord": "MessageSquare",
    "pinterest": "Image",
    "reddit": "MessageCircle",
    "behance": "Palette",
    "snapchat": "Camera",
    "threads": "AtSign",
    "spotify": "Music2",
    "tumblr": "Hash",
    "skype": "Phone",
    "wechat": "MessageCircle",
    "viber": "Phone",
    "medium": "BookOpen",
    "google": "Globe",
    "paypal": "CreditCard",
    "visa": "CreditCard",
    "mastercard": "CreditCard",
}
_LUCIDE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts")

# Named-import block from the bare "lucide-react" module: captures the brace body
# so we can rewrite individual specifiers. `[^{}]` spans newlines (multiline
# import lists) but stops at a nested brace (there are none in an import list).
_LUCIDE_IMPORT_BLOCK = re.compile(
    r'import\s+\{(?P<names>[^{}]*)\}\s+from\s+(?P<q>["\'])lucide-react(?P=q)',
    re.DOTALL,
)
_AS_SPLIT = re.compile(r"\s+as\s+")


def _fix_invalid_lucide_imports(path: str, body: str) -> str:
    """Alias every invalid lucide import to a real glyph so the build survives.

    Each imported specifier is checked against the canonical lucide export set
    (``is_valid_lucide_name``). A valid name — including ``<Base>Icon`` /
    ``Lucide<Base>`` alias forms — passes through byte-identical. An unknown name is
    aliased to a valid glyph: a visually-adjacent brand fallback when one is known,
    else ``Circle``. No-op unless ``path`` is a generated source file importing from
    ``lucide-react`` (R-10 fail-soft). Only the import block is rewritten — usages
    keep their original local name via the alias."""
    if not path.endswith(_LUCIDE_FIX_SUFFIXES) or "lucide-react" not in body:
        return body

    def _rewrite(match: re.Match[str]) -> str:
        changed = False
        specs: list[str] = []
        for raw in match.group("names").split(","):
            spec = raw.strip()
            if not spec:
                continue
            parts = _AS_SPLIT.split(spec, 1)
            imported = parts[0].strip()
            local = parts[1].strip() if len(parts) > 1 else imported
            if is_valid_lucide_name(imported):
                specs.append(spec)
                continue
            fallback = _LUCIDE_INVALID_ICON_FALLBACKS.get(
                imported.lower(), _LUCIDE_GENERIC_FALLBACK
            )
            specs.append(f"{fallback} as {local}")
            changed = True
        if not changed:
            return match.group(0)
        return match.group(0).replace(
            match.group("names"), " " + ", ".join(specs) + " "
        )

    return _LUCIDE_IMPORT_BLOCK.sub(_rewrite, body)


# The writer formats prices/dates with a bare `.toLocaleString()` (no locale arg)
# on server-rendered landing/page components. The locale then defaults to the
# RUNTIME's locale, which differs between the SSR pass (the dev container's Node,
# ru-leaning -> "4 500" with a non-breaking space) and the client (the visitor's
# browser, e.g. en-US -> "4,500"). React sees server text != client text and
# throws a hydration-mismatch error, regenerating the whole tree on the client
# (visible flicker + console error). Pinning an explicit locale makes both passes
# emit the identical string regardless of runtime locale. We only touch the
# EMPTY-parens form — `toLocaleString('ru-RU')` / `toLocaleString(undefined, {…})`
# already pass a first arg and are left byte-identical.
_LOCALE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts")
_BARE_LOCALE_CALL = re.compile(
    r"\.toLocale(?P<kind>String|DateString|TimeString)\(\s*\)"
)


def _fix_bare_locale_string(path: str, body: str) -> str:
    """Pin an explicit locale on bare ``toLocale*String()`` calls.

    A locale-less call resolves to the runtime locale, which differs between the
    SSR (container Node) and client (browser) passes -> hydration mismatch. Adding
    ``'ru-RU'`` makes both passes deterministic. No-op unless ``path`` is a
    generated source file with a bare call (R-10 fail-soft); calls that already
    pass an argument are untouched."""
    if not path.endswith(_LOCALE_FIX_SUFFIXES):
        return body
    return _BARE_LOCALE_CALL.sub(r".toLocale\g<kind>('ru-RU')", body)


# Models occasionally violate the "unchanged files: don't mention" contract
# and return a <file> block whose body is a human-language placeholder like
# "(код без изменений)". Writing that into the file produces a TypeScript /
# Python / HTML parse error and breaks the dev container. We detect such
# stubs and silently skip them — the prior file content stays intact.
_PLACEHOLDER_SIGNATURES = (
    "код без изменений",
    "без изменений",
    "не изменён",
    "не изменен",
    "unchanged",
    "no changes",
    "no change",
    "same as before",
    "as is",
    "оставить как есть",
)


class UnsafePathError(ValueError):
    pass


def is_safe_path(path: str) -> bool:
    if not path or path.startswith(_FORBIDDEN_PREFIXES):
        return False
    if any(s in path for s in _FORBIDDEN_SUBSTRINGS):
        return False
    if "\x00" in path:
        return False
    try:
        normalized = PurePosixPath(path)
    except (ValueError, TypeError):
        return False
    if normalized.is_absolute() or normalized.anchor:
        return False
    return True


_CODE_SYNTAX_CHARS = set("{};=()<>[]")


def _looks_like_unchanged_stub(body: str) -> bool:
    """True when the body is a placeholder ("no changes") instead of real content.

    Heuristic:
    * short body (<= 80 chars after strip),
    * contains a known placeholder signature,
    * AND has essentially no code syntax — `{`, `}`, `(` (other than wrapping the
      phrase), `;`, `=`, `<`, `>`, `[`, `]`. Real code of any length normally
      has at least a couple of these; "(код без изменений)" has only `(` and `)`.

    Keeping the threshold tight (<=80) prevents false-positives on real code
    that happens to mention "unchanged" in a comment or identifier.
    """
    stripped = body.strip()
    if not stripped or len(stripped) > 80:
        return False
    low = stripped.lower()
    if not any(sig in low for sig in _PLACEHOLDER_SIGNATURES):
        return False
    # Strip the most common wrappers: ()/<!--/-->/// and whitespace, then count
    # remaining code-syntax chars. Wrapper parens around a Russian phrase are
    # not code; semicolons/braces/equals/angle brackets in a 80-char body are.
    significant_syntax = sum(1 for ch in stripped if ch in {"{", "}", ";", "=", "[", "]"})
    if significant_syntax > 0:
        return False
    return True


def extract_files(answer: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(answer):
        raw_path = match.group("path").strip()
        body = match.group("body")
        # A whitespace-only body (incl. a lone "\n") is the model's way of asking
        # to DROP a file — the app writer empties the starter src/app/page.tsx
        # exactly so, to hand "/" to (app)/page.tsx. Normalise to "" so the
        # downstream git-commit (unlink) and orchestrator hot_reload (rm) both
        # treat it as delete-intent; a 1-byte "\n" would otherwise be written as
        # a broken module and crash the dev server ("default export is not a
        # React Component").
        if body.strip() == "":
            body = ""
        if not is_safe_path(raw_path):
            raise UnsafePathError(f"unsafe file path: {raw_path!r}")
        if _looks_like_unchanged_stub(body):
            log.warning(
                "extract_files: skipping placeholder stub for %r (body=%r)",
                raw_path,
                body.strip()[:80],
            )
            continue
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"file {raw_path} exceeds {MAX_FILE_BYTES} bytes")
        body = _fix_invented_palette_vars(raw_path, body)
        body = _fix_invalid_lucide_imports(raw_path, body)
        body = _fix_bare_locale_string(raw_path, body)
        files[raw_path] = body
        if len(files) > MAX_FILES:
            raise ValueError(f"too many files in answer: {len(files)} > {MAX_FILES}")
    return files


class EditConflict(ValueError):
    """SEARCH-блок не нашёлся или нашёлся несколько раз — не можем
    однозначно применить замену."""


def extract_edits(answer: str) -> dict[str, list[tuple[str, str]]]:
    """Парсит `<edit path="...">` блоки в `{path: [(search, replace), ...]}`.

    Каждый <edit>-блок может содержать несколько SEARCH/REPLACE секций; они
    применяются в порядке появления (`apply_edits`). Здесь только парсинг —
    проверка матчинга и применение делает `apply_edits`.

    Пустой словарь — нормально (модель просто не использовала формат).
    Невалидный path / огромный body — поднимаем те же исключения, что и
    `extract_files`, чтобы caller обрабатывал единообразно.
    """
    edits: dict[str, list[tuple[str, str]]] = {}
    for match in _EDIT_BLOCK.finditer(answer):
        raw_path = match.group("path").strip()
        body = match.group("body")
        if not is_safe_path(raw_path):
            raise UnsafePathError(f"unsafe edit path: {raw_path!r}")
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"edit {raw_path} exceeds {MAX_FILE_BYTES} bytes")

        pairs: list[tuple[str, str]] = []
        for sr in _SR_BLOCK.finditer(body):
            search = sr.group("search")
            replace = sr.group("replace")
            pairs.append((search, replace))
        if not pairs:
            log.warning(
                "extract_edits: <edit> for %r had no SEARCH/REPLACE blocks "
                "— skipping (model likely forgot the markers)",
                raw_path,
            )
            continue
        edits.setdefault(raw_path, []).extend(pairs)
        if len(edits) > MAX_FILES:
            raise ValueError(f"too many edited files: {len(edits)} > {MAX_FILES}")
    return edits


def _match_span(content: str, search: str) -> tuple[int, int] | None:
    """Locate ``search`` inside ``content`` and return its ``(start, end)`` char
    span, or ``None`` if it's missing OR ambiguous (>1 place).

    Two passes:
    1. **Exact** — the byte-for-byte ``str.find`` the SEARCH contract asks for.
       Unique hit wins; multiple hits are ambiguous → ``None``.
    2. **Indent-tolerant** — compare WHOLE lines stripped of leading/trailing
       whitespace. A cheap model often reproduces the right lines but with
       different indentation (the #1 reason a perfectly-correct edit fails to
       apply). Leading/trailing blank lines in the SEARCH are ignored. Only a
       UNIQUE line-window match is accepted — never guess between two.

    The fallback only forgives WHITESPACE, never content: a SEARCH that names a
    different tag/attribute than the file (e.g. a hallucinated ``data-omnia-photo``
    where the committed file has a resolved ``<img src>``) still won't match — that
    is the prompt's job to get right, not something we should fuzz over.
    """
    n_exact = content.count(search)
    if n_exact == 1:
        i = content.index(search)
        return (i, i + len(search))
    if n_exact > 1:
        return None

    s_lines = search.split("\n")
    while s_lines and not s_lines[0].strip():
        s_lines.pop(0)
    while s_lines and not s_lines[-1].strip():
        s_lines.pop()
    if not s_lines:
        return None
    s_norm = [ln.strip() for ln in s_lines]

    c_lines = content.split("\n")
    # Char offset where each content line begins (line i + its trailing "\n").
    offsets: list[int] = []
    pos = 0
    for ln in c_lines:
        offsets.append(pos)
        pos += len(ln) + 1

    window = len(s_norm)
    found: list[tuple[int, int]] = []
    for i in range(len(c_lines) - window + 1):
        if all(c_lines[i + k].strip() == s_norm[k] for k in range(window)):
            last = i + window - 1
            found.append((offsets[i], offsets[last] + len(c_lines[last])))
            if len(found) > 1:
                return None  # ambiguous — refuse to guess
    return found[0] if len(found) == 1 else None


def apply_edits(
    edits: dict[str, list[tuple[str, str]]],
    base_files: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Применить SEARCH/REPLACE-замены к копиям файлов из ``base_files``.

    Возвращает ``(updated, conflicts)``:
    * ``updated`` — только изменённые файлы (готовы для commit), путь→новое
      содержимое. Файлы из ``edits`` которых нет в ``base_files`` или где
      SEARCH не нашёлся (или нашёлся >1 раза) — попадают в ``conflicts``
      и в ``updated`` не входят.
    * ``conflicts`` — человекочитаемые описания, для логов / телеметрии.

    Этот частичный успех — by design (R-10 fail-soft): один сбойный edit
    не должен ломать остальные правильные правки в том же ответе. Caller
    может решить fallback: попросить модель прислать <file> для конфликтных
    файлов, или просто принять что они не изменились.
    """
    updated: dict[str, str] = {}
    conflicts: list[str] = []
    for path, pairs in edits.items():
        if path not in base_files:
            conflicts.append(
                f"{path}: edit для несуществующего файла — нужен <file> блок"
            )
            continue
        content = base_files[path]
        applied = 0
        for i, (search, replace) in enumerate(pairs, 1):
            span = _match_span(content, search)
            if span is None:
                exact = content.count(search)
                if exact > 1:
                    conflicts.append(
                        f"{path} #{i}: SEARCH-блок неоднозначен ({exact} вхождений), "
                        f"нужен больший контекст"
                    )
                else:
                    conflicts.append(
                        f"{path} #{i}: SEARCH-блок не найден уникально "
                        f"(первые 60 chars: {search[:60]!r})"
                    )
                # Пропускаем ТОЛЬКО эту пару, остальные применяем. Одна
                # непопавшая пара не должна ронять весь edit: напр. свап фоновой
                # картинки + осветление тёмного оверлея — если якорь оверлея
                # промахнулся, картинка всё равно обязана замениться. Зависимая
                # пара (её SEARCH = результат предыдущего REPLACE) просто не
                # найдётся и тоже пропустится — корректная деградация.
                continue
            start, end = span
            content = content[:start] + replace + content[end:]
            applied += 1
        if applied:
            updated[path] = content
    return updated, conflicts
