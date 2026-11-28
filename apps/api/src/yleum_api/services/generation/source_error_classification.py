"""Pure bounded compiler classification; callers supply owning terminal evidence."""
from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from yleum_api.services.agent_progress import redact_sensitive_text
from yleum_api.services.promotion_permit import workspace_revision_digest


@dataclass(frozen=True, slots=True)
class TapAssertionEvidence:
    header: str
    values: str
    stack: str

    def excerpt(self, *, max_bytes: int) -> str | None:
        required = f"{self.header}\n{self.stack}"
        required_bytes = len(required.encode())
        if required_bytes > max_bytes:
            return None
        if self.values and required_bytes + len(self.values.encode()) + 1 <= max_bytes:
            return f"{self.header}\n{self.values}\n{self.stack}"
        return required


@dataclass(frozen=True, slots=True)
class SourceFailure:
    kind: Literal[
        "relative_import", "workspace_alias", "type_error", "react_effect", "test_assertion",
        "infrastructure", "external_package", "unknown",
    ] = "unknown"
    importer: str | None = None
    allowed_paths: tuple[str, ...] = ()
    first_use_stage: str | None = None
    tap_evidence: TapAssertionEvidence | None = field(default=None, repr=False, compare=False)


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
    return infrastructure_diagnostic_hint(detail) is not None


def infrastructure_diagnostic_hint(detail: str) -> str | None:
    # Error messages can quote a source-level string such as "connection refused".
    # Only mask literals inside compiler diagnostics; an independent worker error wins.
    text = _TYPE.sub(lambda match: _QUOTED_LITERAL.sub("", match[0]), detail)
    match = _INFRA.search(text)
    return match[0] if match else None


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
_TAP_TEST_STACK = re.compile(r"(?m)^  stack: \|-\r?\n(?P<frames>(?:    [^\r\n]*\r?\n)+)")
_TAP_TEST_CODE_FAILURE = "  failureType: 'testCodeFailure'"
_TAP_ASSERTION_CODE = "  code: 'ERR_ASSERTION'"
_TAP_ASSERTION_NAME = "  name: 'AssertionError'"
_FIRST_USE_STAGE = re.compile(
    r"^    First use: [^\r\n]{1,180} / "
    r"(?P<stage>before owner setup|owner setup|first action|reopen)$"
)
_FIRST_USE_FRAME = re.compile(
    r"^    (?:at )?(?:async )?(?:visit|verifyResult|exerciseFirstUse) "
    r"\(file:///workspace/scripts/first-use\.mjs:[1-9]\d{0,6}:[1-9]\d{0,6}\)$"
)
_NODE_STRIP_CODE = "  code: 'ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX'"
_NODE_TYPESCRIPT_FRAME = re.compile(
    r"(?m)^    (?:at )?[A-Za-z_$][A-Za-z0-9_.$]* "
    r"\(node:internal/modules/typescript:[1-9]\d{0,6}:[1-9]\d{0,6}\)\r?$"
)
_LOCATOR_TIMEOUT = re.compile(
    r"(?m)^    locator\.[A-Za-z]+: Timeout [1-9]\d{0,8}ms exceeded\.\r?\n"
    r"    Call log:\r?\n      - waiting for [^\r\n]+"
)
_TEST_CONTEXT_FRAME = re.compile(
    r"(?m)^    (?:at )?(?:async )?TestContext\.<anonymous> "
    rf"\((?:file://)?/workspace/(?P<file>{_TEST_PATH})"
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


def _tap_assertion_values(fields: list[str]) -> list[str]:
    """Node prints scalar values inline and object/array values as indented YAML."""
    values: dict[str, str] = {}
    for index, line in enumerate(fields):
        if not line.startswith(("  expected:", "  actual:")):
            continue
        label = line.partition(":")[0]
        if label in values:
            return []
        lines = [line]
        if line.endswith(":"):
            for offset in range(index + 1, len(fields)):
                child = fields[offset]
                if not child.startswith("    "):
                    break
                lines.append(child)
        # Equal budgets for both sides, so a large expected object cannot hide the actual one.
        values[label] = "\n".join(lines).encode()[:512].decode(errors="ignore")
    return list(values.values()) if len(values) == 2 else []


def _tap_test_code_failures(
    detail: str,
) -> Iterator[tuple[re.Match[str], re.Match[str], list[str]]]:
    for failed in _TAP_FAILED_TEST.finditer(detail):
        body = failed["body"]
        location = _TAP_TEST_LOCATION.search(body)
        if location:
            fields = body.splitlines()
            if _TAP_TEST_CODE_FAILURE in fields:
                yield failed, location, fields


def first_use_tap_evidence(detail: str) -> tuple[str, int, str, str] | None:
    """Keep original reporter fields together, including when the enclosing build log is clipped."""
    if "First use: " not in detail or "ERR_ASSERTION" not in detail:
        return None
    for failed, location, fields in _tap_test_code_failures(detail):
        stage = next((m for line in fields if (m := _FIRST_USE_STAGE.fullmatch(line))), None)
        frame = next((line for line in fields if _FIRST_USE_FRAME.fullmatch(line)), None)
        if not (stage and frame and _TAP_TEST_END.match(detail, failed.end())):
            continue
        required = (_TAP_ASSERTION_CODE, _TAP_ASSERTION_NAME, _TAP_TEST_CODE_FAILURE)
        if (any(fields.count(s) != 1 for s in required)
                or sum(line.startswith("  location:") for line in fields) != 1):
            continue
        values = _tap_assertion_values(fields)
        if not values:
            continue
        identity = [line for line in fields if line.startswith(
            ("  location:", "  failureType:", "  code:", "  name:"),
        )]
        lines = [failed[0].splitlines()[0], *identity, stage[0], frame, *values, "  ..."]
        excerpt = "\n".join(line.encode()[:512].decode(errors="ignore") for line in lines)
        return location["file"], int(location["line"]), stage["stage"], excerpt
    return None


def _tap_assertion_frame(
    detail: str, failed: re.Match[str], location: re.Match[str], fields: list[str],
) -> re.Match[str] | None:
    keys = ("  failureType:", "  code:", "  location:", "  name:", "  stack:")
    if (
        _TAP_ASSERTION_CODE not in fields or _TAP_ASSERTION_NAME not in fields
        or fields[0] != "  ---" or not _TAP_TEST_END.match(detail, failed.end())
        or any(field and not field.startswith("  ") for field in fields)
        or sum(field.startswith(keys) for field in fields) != len(keys)
    ):
        return None
    # The Node TAP reporter omits "at " from its stack frames.
    stack = _TAP_TEST_STACK.search(failed["body"])
    frame = _TEST_CONTEXT_FRAME.search(stack["frames"]) if stack else None
    return frame if frame and frame["file"] == location["file"] else None


def _tap_assertion_evidence(
    failed: re.Match[str], location: re.Match[str], frame: re.Match[str],
) -> TapAssertionEvidence:
    headline = redact_sensitive_text(failed[0].partition("\n")[0].removesuffix("\r"))
    safe_fields = redact_sensitive_text(failed["body"]).splitlines()
    return TapAssertionEvidence(
        header=redact_sensitive_text("\n".join((
            headline.encode()[:256].decode(errors="ignore"), "  ---",
            location[0].removesuffix("\r"), _TAP_TEST_CODE_FAILURE,
            _TAP_ASSERTION_CODE, _TAP_ASSERTION_NAME,
        ))),
        values="\n".join(_tap_assertion_values(safe_fields)),
        stack="\n".join((
            "  stack: |-", redact_sensitive_text(frame[0].removesuffix("\r")), "  ...",
        )),
    )


def _failed_product_test(detail: str, files: Mapping[str, str]) -> SourceFailure | None:
    """Bind a runner diagnostic to an owned failed test without granting edit paths."""
    if not any(hint in detail for hint in ("locator.", _NODE_STRIP_CODE, _TAP_ASSERTION_CODE)):
        return None
    for failed, location, fields in _tap_test_code_failures(detail):
        body = failed["body"]
        path = _importer(location["file"], files)
        if path is None:
            continue
        lines = len(files[path].splitlines())
        if "  code: 'ERR_TEST_FAILURE'" in fields and _LOCATOR_TIMEOUT.search(body):
            frame = _TEST_CONTEXT_FRAME.search(body)
            if frame and frame["file"] == location["file"] and int(frame["line"]) <= lines:
                return SourceFailure("test_assertion", importer=path)
        elif int(location["line"]) <= lines:
            frame = _tap_assertion_frame(detail, failed, location, fields)
            if frame and int(frame["line"]) <= lines:
                return SourceFailure(
                    "test_assertion", importer=path,
                    tap_evidence=_tap_assertion_evidence(failed, location, frame),
                )
        if (
            _NODE_STRIP_CODE in fields and _TAP_TEST_END.match(detail, failed.end())
            and _NODE_TYPESCRIPT_FRAME.search(body) and int(location["line"]) <= lines
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
    if evidence := first_use_tap_evidence(detail):
        raw, line, stage, _ = evidence
        path = _importer(raw, files)
        if (path and line <= len(files[path].splitlines()) and "scripts/first-use.mjs" in files):
            return SourceFailure("test_assertion", importer=path, first_use_stage=stage)
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
