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
        "relative_import", "workspace_alias", "type_error", "react_effect", "test_assertion",
        "infrastructure", "external_package", "unknown",
    ] = "unknown"
    importer: str | None = None
    allowed_paths: tuple[str, ...] = ()


def source_check_is_repairable(
    dimension: str, outcome: str, detail: str, source_failure: SourceFailure | None = None,
) -> bool:
    """Only trusted fast-check compiler/assertion diagnostics permit this repair."""
    if dimension != "fast_check" or outcome != "red" or (
        source_failure is not None and source_failure.kind == "infrastructure"
    ):
        return False
    compiler = source_failure is not None and bool(source_failure.allowed_paths)
    test = (
        "[targeted-test]" in detail
        and re.search(r"(?m)^not ok \d+ - ", detail)
        and "failureType: 'testCodeFailure'" in detail
        and "code: 'ERR_ASSERTION'" in detail
        and re.search(r"location: '/workspace/tests/[^'\r\n]+:\d+:\d+'", detail)
    )
    return bool(compiler or test)


_INFRA = re.compile(
    r"(?i)\b(?:SIGKILL|SIGTERM|ENOSPC|ENOMEM|ECONNRESET|ECONNREFUSED|ETIMEDOUT|"
    r"EAI_AGAIN|ENOTFOUND|EACCES|EPERM|worker_lost)\b|out of memory|heap limit|"
    r"command timed out|connection refused|network (?:error|unreachable)|fetch failed"
)


def source_diagnostic_has_infrastructure_error(detail: str) -> bool:
    # Error messages can quote a source-level string such as "connection refused".
    # Only mask literals inside compiler diagnostics; an independent worker error wins.
    text = _TYPE.sub(lambda match: _QUOTED_LITERAL.sub("", match[0]), detail)
    return _INFRA.search(text) is not None


_IMPORT = re.compile(
    r"(?m)^(?P<file>[^\r\n]+)\r?\nModule not found: Can't resolve "
    r"(?P<quote>['\"])(?P<target>[^'\"\r\n]+)(?P=quote)\r?$"
)
_TYPE = re.compile(
    r"(?m)^(?P<file>[^\r\n]+?)(?:\(\d+,\d+\): error (?P<code>TS\d{4}):|"
    r"(?::\d+(?::\d+)?)?\r?\n(?:Type error|Syntax error):)(?P<reason>[^\r\n]*)"
)
_QUOTED_LITERAL = re.compile(r'''"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`''')
_REACT_EFFECT = re.compile(r"(?m)^(?P<file>[^:\r\n]+):\d+: self-cancelling React effect:")
_ASSERTION = re.compile(
    r"(?m)^AssertionError \[ERR_ASSERTION\](?:[^\r\n]*)$"
    r"|^\s*code: ['\"]ERR_ASSERTION['\"],?\s*$"
)
_TEST_PATH = r"tests/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:mjs|cjs|js|ts|tsx)"
_ASSERTION_LOCATION = re.compile(
    r"(?m)^\s+at (?:[^()\r\n]+ \()?file:///workspace/"
    rf"(?P<file>{_TEST_PATH})"
    r":(?P<line>[1-9]\d{0,6}):[1-9]\d{0,6}\)?[ \t]*$"
)
_TAP_FAILED_TEST = re.compile(
    r"(?ms)^not ok \d+ - [^\r\n]*\r?\n(?P<body>.*?)"
    r"(?=^(?:not ok |ok |# Subtest:|1\.\.|  \.\.\.)|\Z)"
)
_TAP_TEST_LOCATION = re.compile(
    rf"(?m)^  location: '/workspace/(?P<file>{_TEST_PATH})"
    r":(?P<line>[1-9]\d{0,6}):[1-9]\d{0,6}'\r?$"
)
_TAP_TEST_END = re.compile(r"  \.\.\.(?:\r?\n|\Z)")
_NODE_STRIP_CODE = "  code: 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX'"
_NODE_TYPESCRIPT_FRAME = re.compile(
    r"(?m)^    (?:at )?[A-Za-z_$][A-Za-z0-9_.$]* "
    r"\(node:internal/modules/typescript:[1-9]\d{0,6}:[1-9]\d{0,6}\)\r?$"
)
_LOCATOR_TIMEOUT = re.compile(
    r"(?m)^    locator\.[A-Za-z]+: Timeout [1-9]\d{0,8}ms exceeded\.\r?\n"
    r"    Call log:\r?\n      - waiting for [^\r\n]+"
)
_PLAYWRIGHT_TEST_FRAME = re.compile(
    rf"(?m)^    (?:at )?TestContext\.<anonymous> \((?:file://)?/workspace/(?P<file>{_TEST_PATH})"
    r":(?P<line>[1-9]\d{0,6}):[1-9]\d{0,6}\)\r?$"
)
_TRUNCATED_ASSERTION_LOCATION = re.compile(
    r"(?m)^\s+at (?:[^()\r\n]+ \()?file:///workspace/"
    r"(?P<prefix>tests/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*$"
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


def has_truncated_test_assertion(detail: str, files: Mapping[str, str]) -> bool:
    """Identify only a partial owned-test hint, never invent its missing path."""
    if not _ASSERTION.search(detail) or source_diagnostic_has_infrastructure_error(detail):
        return False
    frames = [line for line in detail.splitlines() if re.match(r"^\s+at .*file://", line)]
    if any(_TRUNCATED_ASSERTION_LOCATION.fullmatch(line) is None for line in frames):
        return False
    for match in _TRUNCATED_ASSERTION_LOCATION.finditer(detail):
        prefix = match["prefix"]
        if len(prefix) <= 1024 and any(
            path.startswith(prefix) and path != prefix and _importer(path, files) == path
            for path in files
        ):
            return True
    return False


def _failed_product_test(detail: str, files: Mapping[str, str]) -> SourceFailure | None:
    """Bind a runner diagnostic to an owned failed test without granting edit paths."""
    if "locator." not in detail and _NODE_STRIP_CODE not in detail:
        return None
    for failed in _TAP_FAILED_TEST.finditer(detail):
        body = failed["body"]
        fields = body.splitlines()
        if "  failureType: 'testCodeFailure'" not in fields:
            continue
        location = _TAP_TEST_LOCATION.search(body)
        path = _importer(location["file"], files) if location else None
        if location is None or path is None:
            continue
        if "  code: 'ERR_TEST_FAILURE'" in fields and _LOCATOR_TIMEOUT.search(body):
            frame = _PLAYWRIGHT_TEST_FRAME.search(body)
            if (frame and frame["file"] == location["file"]
                    and int(frame["line"]) <= len(files[path].splitlines())):
                return SourceFailure("test_assertion", importer=path)
        if (
            _NODE_STRIP_CODE in fields and _TAP_TEST_END.match(detail, failed.end())
            and _NODE_TYPESCRIPT_FRAME.search(body)
            and int(location["line"]) <= len(files[path].splitlines())
            and sum(
                field.startswith(("  failureType:", "  code:", "  location:")) for field in fields
            ) == 3
        ):
            # The runner located the test declaration, not the imported TypeScript error.
            return SourceFailure("test_assertion")
    return None


def _owned_full_build_phase(
    detail: str, files: Mapping[str, str], phase: str | None,
) -> bool:
    """Phase is supplied only after authenticated terminal-response replay."""
    if (type(phase) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", phase) is None
            or re.search(r"(?m)^\[" + re.escape(phase) + r"\](?:[ \t]|$)", detail) is None):
        return False
    try:
        manifest = json.loads(files.get(".omnia/cell.json", ""))
    except (ValueError, TypeError):
        return False
    if (type(manifest) is not dict or type(manifest.get("version")) is not int
            or manifest["version"] != 1 or type(manifest.get("tasks")) is not list):
        return False
    tasks = manifest["tasks"]
    if any(type(task) is not dict or type(task.get("name")) is not str for task in tasks):
        return False
    names = [task["name"] for task in tasks]
    if len(names) != len(set(names)):
        return False
    selected = [task for task in tasks if task["name"] == phase]
    if len(selected) != 1:
        return False
    task = selected[0]
    argv, cwd = task.get("argv"), task.get("cwd", ".")
    return bool(
        task.get("role") == "full_build" and type(argv) is list and argv
        and all(type(arg) is str and arg and "\x00" not in arg for arg in argv)
        and type(cwd) is str and cwd and not cwd.startswith("/")
        and not any(char in cwd for char in "\\:\x00") and ".." not in cwd.split("/")
    )


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
    frozen_source_sha256: str, authenticated_task_phase: str | None = None,
) -> SourceFailure:
    """Never infer completion/ownership from logs; never mutate diagnostic or source."""
    if not command_finished or workspace_revision_digest(files) != frozen_source_sha256:
        return SourceFailure()
    if source_diagnostic_has_infrastructure_error(detail):
        return SourceFailure("infrastructure")
    for pattern in (_IMPORT, _TS_MISSING, _NEXT_MISSING):
        for match in pattern.finditer(detail):
            importer = _importer(match["file"], files)
            if importer:
                return _missing_failure(importer, match["target"], files)
    for match in _TYPE.finditer(detail):
        if match["code"] in _MODULE_ERRORS or _MODULE_REASON.search(match["reason"]):
            return SourceFailure()
        importer = _importer(match["file"], files)
        if importer:
            return SourceFailure("type_error", importer, (importer,))
    for match in _REACT_EFFECT.finditer(detail):
        importer = _importer(match["file"], files)
        if importer:
            return SourceFailure("react_effect", importer, (importer,))
    if _ASSERTION.search(detail):
        for match in _ASSERTION_LOCATION.finditer(detail):
            importer = _importer(match["file"], files)
            if importer and int(match["line"]) <= len(files[importer].splitlines()):
                # This identifies an owned failing test, not permission to
                # weaken it or a claim that the application already works.
                return SourceFailure("test_assertion", importer, (importer,))
        if (has_truncated_test_assertion(detail, files)
                and _owned_full_build_phase(detail, files, authenticated_task_phase)):
            # The exact frame is unavailable. Recognize the owned failed task
            # without guessing an importer or granting a source write path.
            return SourceFailure("test_assertion")
    return _failed_product_test(detail, files) or SourceFailure()
