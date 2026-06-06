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
                # Прекращаем дальнейшие правки этого файла — последующие
                # SEARCH могут зависеть от предыдущего REPLACE, который не
                # применился, и тогда будут ещё больше промахов.
                break
            start, end = span
            content = content[:start] + replace + content[end:]
        else:
            # for-else: цикл прошёл без break → все пары применились
            updated[path] = content
    return updated, conflicts
