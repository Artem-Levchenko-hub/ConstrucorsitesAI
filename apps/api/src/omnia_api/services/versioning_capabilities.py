"""AV06 / AV06.1: an adaptation must not silently drop functions the draft had.

A capability is one (HTTP method, route) pair exported by a Next.js app-router
``route.*`` file. Extraction works on source with comments and string/template
literal bodies blanked out, so ``// export function GET`` or a template string
can neither create nor hide a handler (FV025). ``export { handler as GET }`` and
re-exports count (FV026); route groups and parallel slots are transparent
(FV027); dynamic segment names are normalized (FV028) while catch-all and
optional catch-all stay distinct (FV029). ``export * from`` cannot be resolved
without following modules: it is reported as a coverage gap, never as "no
routes". Platform kit routes are ignored only while their file is byte-identical
before and after; a modified or deleted platform file is owned by the app and
compared like any other.

The orchestrator ships the same rules in ``services/versioning/compatibility.py``;
``tests/fixtures/versioning_v4/routes/corpus.json`` keeps both in step (FV032).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_ROUTE_FILE = re.compile(r"^src/app/(?P<dir>(?:.+/)?)route\.(?:ts|js|tsx|jsx|mjs|cjs)$")
_PLATFORM_PREFIXES = ("/api/omnia/", "/api/max/")
_PLATFORM_ROUTES = frozenset({"/api/health"})

_EXPORT_FUNCTION = re.compile(r"\bexport\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")
_EXPORT_DECLARATION = re.compile(r"\bexport\s+(?:const|let|var)\s+")
_STATEMENT_START = re.compile(
    r"(?:export|import|const|let|var|function|async|class|interface|type|enum|declare|"
    r"if|for|while|do|switch|try|return|throw)\b"
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_EXPORT_LIST = re.compile(r"\bexport\s*\{([^}]*)\}")
_EXPORT_STAR = re.compile(r"\bexport\s*\*\s*from\b")


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def blank_comments_and_strings(text: str) -> str:
    """Replace comment bodies and string/template literal bodies with spaces,
    keeping newlines, so what remains is code structure only."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(_blank(text[i:j]))
            i = j
            continue
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                c = text[j]
                if c == "\\":
                    j += 2
                    continue
                if c == ch or (ch != "`" and c == "\n"):
                    break
                j += 1
            closing = 1 if j < n and text[j] == ch else 0
            out.append(ch + _blank(text[i + 1 : j]) + (ch if closing else ""))
            i = j + closing
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _declaration(code: str, start: int) -> str:
    """The declarator list of one ``export const|let|var`` statement: up to the
    first ``;`` at bracket depth 0, or a line break followed by another statement
    (no-semicolon style)."""
    depth = 0
    i = start
    while i < len(code):
        char = code[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0:
            if char == ";":
                break
            if char == "\n":
                j = i + 1
                while j < len(code) and code[j].isspace():
                    j += 1
                if _STATEMENT_START.match(code, j):
                    break
        i += 1
    return code[start:i]


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _declared_names(declarators: str) -> set[str]:
    """Names bound by ``A = ..., B = ...``, ``{ A, B: local } = ...`` and
    ``[A] = ...`` (comments and strings are already blanked)."""
    names: set[str] = set()
    for part in _split_top_level(declarators):
        part = part.strip()
        if not part:
            continue
        if part[0] in "{[":
            end = part.find("}" if part[0] == "{" else "]")
            for member in part[1 : end if end >= 0 else len(part)].split(","):
                if ":" in member:  # `{ GET: local }` binds `local`, not GET
                    member = member.split(":", 1)[1]
                member = member.split("=", 1)[0].strip()
                if _IDENTIFIER.fullmatch(member):
                    names.add(member)
            continue
        match = _IDENTIFIER.match(part)
        if match:
            names.add(match.group(0))
    return names


def exported_methods(source: str) -> tuple[frozenset[str], bool]:
    """(HTTP methods exported by a route module, whether an unresolvable
    ``export * from`` is present)."""
    code = blank_comments_and_strings(source)
    names: set[str] = set()
    names.update(_EXPORT_FUNCTION.findall(code))
    for match in _EXPORT_DECLARATION.finditer(code):
        names.update(_declared_names(_declaration(code, match.end())))
    for group in _EXPORT_LIST.findall(code):
        for item in group.split(","):
            item = item.strip()
            if not item:
                continue
            item = re.sub(r"^type\s+", "", item)
            parts = item.split()
            if len(parts) == 3 and parts[1] == "as":
                names.add(parts[2])
            elif len(parts) == 1:
                names.add(parts[0])
    # `export * as ns from` never matches _EXPORT_STAR; a bare star is unresolvable.
    unresolved = _EXPORT_STAR.search(code) is not None
    return frozenset(name for name in names if name in METHODS), unresolved


def normalize_route(directory: str) -> str:
    segments: list[str] = []
    for segment in directory.strip("/").split("/"):
        if not segment or segment.startswith("@"):
            continue  # parallel slots are not part of the URL
        if segment.startswith("(") and segment.endswith(")"):
            continue  # route groups are not part of the URL
        if segment.startswith("[[...") and segment.endswith("]]"):
            segments.append("[[...]]")
        elif segment.startswith("[...") and segment.endswith("]"):
            segments.append("[...]")
        elif segment.startswith("[") and segment.endswith("]"):
            segments.append("[*]")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def is_platform_route_path(path: str) -> bool:
    match = _ROUTE_FILE.match(path)
    if not match:
        return False
    route = normalize_route(match["dir"])
    return route.startswith(_PLATFORM_PREFIXES) or route in _PLATFORM_ROUTES


@dataclass(frozen=True)
class RouteManifest:
    capabilities: frozenset[tuple[str, str]]
    unresolved: frozenset[str]

    def routes(self) -> frozenset[str]:
        return frozenset(route for _, route in self.capabilities) | self.unresolved


def route_manifest(
    files: Mapping[str, str], *, ignore: frozenset[str] = frozenset()
) -> RouteManifest:
    capabilities: set[tuple[str, str]] = set()
    unresolved: set[str] = set()
    for path, text in files.items():
        if path in ignore or not isinstance(text, str):
            continue
        match = _ROUTE_FILE.match(path)
        if not match:
            continue
        route = normalize_route(match["dir"])
        methods, star = exported_methods(text)
        capabilities.update((method, route) for method in methods)
        if star:
            unresolved.add(route)
    return RouteManifest(frozenset(capabilities), frozenset(unresolved))


def route_capabilities(files: Mapping[str, str]) -> set[tuple[str, str]]:
    """Owned capabilities of one tree (platform kit routes excluded)."""
    platform = frozenset(path for path in files if is_platform_route_path(path))
    return set(route_manifest(files, ignore=platform).capabilities)


def source_digest(files: Mapping[str, str]) -> str:
    listing = sorted(
        (path, hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest())
        for path, text in files.items()
        if isinstance(text, str)
    )
    return hashlib.sha256(json.dumps(listing).encode()).hexdigest()


@dataclass(frozen=True)
class CapabilityAssessment:
    lost: tuple[tuple[str, str], ...]
    unresolved_before: tuple[str, ...]
    unresolved_after: tuple[str, ...]
    baseline_digest: str
    result_digest: str

    def gap(self) -> str | None:
        lines: list[str] = []
        if self.lost:
            routes = ", ".join(f"{method} {path}" for method, path in self.lost)
            lines.append(
                "Адаптация удалила функции, которые были в черновике до неё: "
                f"{routes}. Данные для них остаются в базе. Верни эти маршруты рабочими "
                "(тот же формат ответа и та же проверка владельца записей), даже если "
                "экраны выбранной версии их не используют."
            )
        if self.unresolved_after:
            lines.append(
                "Маршруты объявлены через `export * from` и не могут быть проверены: "
                + ", ".join(self.unresolved_after)
                + ". Замени на явные `export { GET, POST } from '...'` или "
                "`export async function GET`."
            )
        if not lines:
            return None
        lines.append(
            "ADAPTATION CAPABILITY CHECK: keep every listed route exported and working. "
            f"baseline={self.baseline_digest[:12]} candidate={self.result_digest[:12]}"
        )
        return " ".join(lines)


def assess_capabilities(
    before: Mapping[str, str], after: Mapping[str, str]
) -> CapabilityAssessment:
    """Compare the draft before an adaptation with its result.

    Platform kit routes are skipped only while byte-identical on both sides; a
    baseline route declared with ``export *`` cannot be proven, so the result must
    still carry that route (any resolved method, or the same unresolved file)."""
    untouched_platform = frozenset(
        path
        for path in before
        if is_platform_route_path(path) and after.get(path) == before[path]
    )
    old = route_manifest(before, ignore=untouched_platform)
    new = route_manifest(after, ignore=untouched_platform)
    lost = sorted(old.capabilities - new.capabilities)
    new_routes = new.routes()
    lost.extend(
        ("*", route) for route in sorted(old.unresolved) if route not in new_routes
    )
    return CapabilityAssessment(
        lost=tuple(lost),
        unresolved_before=tuple(sorted(old.unresolved)),
        unresolved_after=tuple(sorted(new.unresolved)),
        baseline_digest=source_digest(before),
        result_digest=source_digest(after),
    )


def lost_capabilities(
    before: Mapping[str, str], after: Mapping[str, str]
) -> list[tuple[str, str]]:
    return list(assess_capabilities(before, after).lost)


def capability_gap(before: Mapping[str, str], after: Mapping[str, str]) -> str | None:
    """Repair feedback for the agent, or None when every function is kept."""
    return assess_capabilities(before, after).gap()
