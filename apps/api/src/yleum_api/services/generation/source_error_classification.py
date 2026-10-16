"""Pure bounded compiler classification; callers supply owning terminal evidence."""
from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from yleum_api.services.promotion_permit import workspace_revision_digest


@dataclass(frozen=True, slots=True)
class SourceFailure:
    kind: Literal[
        "relative_import", "workspace_alias", "type_error", "infrastructure",
        "external_package", "unknown",
    ] = "unknown"
    importer: str | None = None
    allowed_paths: tuple[str, ...] = ()


_INFRA = re.compile(
    r"(?i)\b(?:SIGKILL|SIGTERM|ENOSPC|ENOMEM|ECONNRESET|ECONNREFUSED|ETIMEDOUT|"
    r"EAI_AGAIN|ENOTFOUND|EACCES|EPERM|worker_lost)\b|out of memory|heap limit|"
    r"command timed out|connection refused|network (?:error|unreachable)|fetch failed"
)
_IMPORT = re.compile(
    r"(?m)^(?P<file>[^\r\n]+)\r?\nModule not found: Can't resolve "
    r"(?P<quote>['\"])(?P<target>[^'\"\r\n]+)(?P=quote)\r?$"
)
_TYPE = re.compile(
    r"(?m)^(?P<file>[^\r\n]+?)(?:\(\d+,\d+\): error (?P<code>TS\d{4}):|"
    r"(?::\d+(?::\d+)?)?\r?\n(?:Type error|Syntax error):)"
)
_TS_MISSING = re.compile(
    r"(?m)^(?P<file>[^\r\n]+?)\(\d+,\d+\): error TS2307: Cannot find module "
    r"(?P<quote>['\"])(?P<target>[^'\"\r\n]+)(?P=quote)"
)
_NEXT_MISSING = re.compile(
    r"(?m)^(?P<file>[^\r\n]+?)(?::\d+(?::\d+)?)?\r?\nType error: Cannot find module "
    r"(?P<quote>['\"])(?P<target>[^'\"\r\n]+)(?P=quote)"
)
_MODULE_REASON = re.compile(
    r"Cannot find module\b|Could not find a declaration file for module\b|"
    r"Cannot find type definition file\b|File .+ is not a module\b"
)
_MODULE_ERRORS = {"TS2307", "TS2306", "TS2688", "TS2792", "TS7016"}
_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_TARGET_SUFFIXES = _SOURCE_SUFFIXES | {".css", ".scss", ".sass", ".less", ".svg"}
_FORBIDDEN = {".git", ".ssh", "node_modules", ".next", "secrets", "secrets.json"}


def _safe_path(path: str) -> str | None:
    if not path or len(path) > 1024 or any(c in path for c in "\\\x00?#:"):
        return None
    if path.startswith("/") or any(part == ".." for part in path.split("/")):
        return None
    normalized = posixpath.normpath(path)
    parts = PurePosixPath(normalized).parts
    if not parts or any(p.lower() in _FORBIDDEN or p.lower().startswith(".env") for p in parts):
        return None
    if PurePosixPath(normalized).suffix.lower() not in _TARGET_SUFFIXES:
        # Extensionless imports may only repair their existing importer.
        if PurePosixPath(normalized).suffix or not re.fullmatch(r"[A-Za-z0-9_-]+", parts[-1]):
            return None
    if ".config." in parts[-1]:
        return None
    return normalized


def _importer(raw: str, files: Mapping[str, str]) -> str | None:
    path = _safe_path(raw.removeprefix("/workspace/").removeprefix("./"))
    return path if path is not None and path in files and (
        PurePosixPath(path).suffix in _SOURCE_SUFFIXES
    ) else None


def _relative_target(importer: str, target: str) -> str | None:
    # Resolve .. before validating; normpath preserves an escape above workspace root.
    if any(c in target for c in "\\\x00?#:"):
        return None
    return _safe_path(posixpath.normpath(posixpath.join(posixpath.dirname(importer), target)))


def _alias_target(target: str, files: Mapping[str, str]) -> tuple[str | None, bool]:
    """(resolved, uncertain): strict root JSON, one pattern and one local substitution."""
    raw = files.get("tsconfig.json")
    if raw is None:
        return None, False
    try:
        config = json.loads(raw)
        if not isinstance(config, dict) or "extends" in config:
            return None, True
        options = config.get("compilerOptions", {})
        if not isinstance(options, dict) or any(
            key in options for key in ("rootDirs", "moduleSuffixes")
        ) or options.get("baseUrl", ".") not in {".", "./"}:
            return None, True
        paths = options.get("paths", {})
        if not isinstance(paths, dict):
            return None, True
        matches = []
        for pattern, values in paths.items():
            if not isinstance(pattern, str) or pattern.count("*") > 1:
                return None, True
            expression = re.escape(pattern).replace(r"\*", "(.*)")
            match = re.fullmatch(expression, target)
            if match is not None:
                matches.append((pattern, values, match))
        if not matches:
            return None, False
        if len(matches) != 1:
            return None, True
        pattern, values, match = matches[0]
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
            return None, True
        value = values[0]
        if value.count("*") != pattern.count("*"):
            return None, True
        resolved = value.replace("*", match.group(1)) if "*" in pattern else value
        return _safe_path(resolved.removeprefix("./")), True
    except (ValueError, TypeError):
        return None, True


def _paths(importer: str, target: str) -> tuple[str, ...]:
    if target == importer or not PurePosixPath(target).suffix:
        return (importer,)
    return (importer, target)


def _missing_failure(importer: str, target: str, files: Mapping[str, str]) -> SourceFailure:
    if target.startswith(("./", "../")):
        resolved = _relative_target(importer, target)
        return SourceFailure("relative_import", importer, _paths(importer, resolved)) \
            if resolved else SourceFailure()
    resolved, uncertain = _alias_target(target, files)
    if resolved:
        return SourceFailure("workspace_alias", importer, _paths(importer, resolved))
    if uncertain:
        return SourceFailure()
    if re.fullmatch(r"(?:@[A-Za-z0-9_-]+/)?[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*", target):
        return SourceFailure("external_package", importer)
    return SourceFailure()


def classify_source_failure(
    detail: str, *, files: Mapping[str, str], command_finished: bool,
    frozen_source_sha256: str,
) -> SourceFailure:
    """Never infer completion/ownership from logs; never mutate diagnostic or source."""
    if not command_finished or workspace_revision_digest(files) != frozen_source_sha256:
        return SourceFailure()
    if _INFRA.search(detail):
        return SourceFailure("infrastructure")
    for pattern in (_IMPORT, _TS_MISSING, _NEXT_MISSING):
        for match in pattern.finditer(detail):
            importer = _importer(match["file"], files)
            if importer:
                return _missing_failure(importer, match["target"], files)
    for match in _TYPE.finditer(detail):
        reason = detail[match.end():].splitlines()[0] if detail[match.end():] else ""
        if match["code"] in _MODULE_ERRORS or _MODULE_REASON.search(reason):
            return SourceFailure()
        importer = _importer(match["file"], files)
        if importer:
            return SourceFailure("type_error", importer, (importer,))
    return SourceFailure()
