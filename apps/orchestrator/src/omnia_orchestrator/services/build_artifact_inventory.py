"""Fail-closed MAX production artifact inventory checks.

The live development container owns generated SQL migrations.  The platform
owns the production migration runner.  A production image is eligible for a
swap only when those exact files survive source overlay and image assembly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from omnia_orchestrator.core.errors import OrchestratorError

MAX_REQUIRED_MIGRATIONS = frozenset(
    {
        "drizzle/0000_max_core.sql",
        "drizzle/0001_business_core.sql",
    }
)
MAX_RUNNER_PATH = "scripts/apply-migrations.mjs"
_EXCLUDED_PARTS = frozenset({".git", ".next", "node_modules"})
_SENSITIVE_NAMES = frozenset({".env", ".env.local", ".env.production", "secrets"})


def _fail(detail: str) -> OrchestratorError:
    return OrchestratorError(
        code="container_failure",
        message=f"MAX artifact inventory mismatch: {detail}",
        status_code=500,
    )


def _normalize_path(raw: str) -> str | None:
    value = raw.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise _fail("invalid artifact path")
    if any(part in _EXCLUDED_PARTS or part in _SENSITIVE_NAMES for part in path.parts):
        return None
    return path.as_posix()


def filtered_inventory(files: Mapping[str, str]) -> dict[str, str]:
    """Normalize a value-free digest map and drop generated or sensitive paths."""
    result: dict[str, str] = {}
    for raw_path, digest in files.items():
        path = _normalize_path(raw_path)
        if path is None:
            continue
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise _fail(f"invalid digest for {path}")
        result[path] = digest
    return result


def _file_digest(path: Path, relative: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise _fail(f"missing regular file {relative}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def max_context_inventory(root: Path) -> dict[str, str]:
    drizzle = root / "drizzle"
    sql: dict[str, str] = {}
    if drizzle.is_dir() and not drizzle.is_symlink():
        for path in sorted(drizzle.rglob("*.sql")):
            relative = path.relative_to(root).as_posix()
            sql[relative] = _file_digest(path, relative)
    runner = root / MAX_RUNNER_PATH
    sql[MAX_RUNNER_PATH] = _file_digest(runner, MAX_RUNNER_PATH)
    return sql


def verify_max_source_context(
    source_files: Mapping[str, str],
    *,
    context_root: Path,
    template_root: Path,
) -> dict[str, str]:
    """Return the trusted context manifest or raise before the image build."""
    source = {
        path: digest
        for path, digest in filtered_inventory(source_files).items()
        if path.startswith("drizzle/") and path.endswith(".sql")
    }
    missing = sorted(MAX_REQUIRED_MIGRATIONS - set(source))
    if missing:
        raise _fail("source missing canonical migrations: " + ", ".join(missing))

    context = max_context_inventory(context_root)
    context_sql = {path: digest for path, digest in context.items() if path.endswith(".sql")}
    if set(context_sql) != set(source):
        missing_from_context = sorted(set(source) - set(context_sql))
        unexpected = sorted(set(context_sql) - set(source))
        detail = []
        if missing_from_context:
            detail.append("context missing " + ", ".join(missing_from_context))
        if unexpected:
            detail.append("context has untrusted seed " + ", ".join(unexpected))
        raise _fail("; ".join(detail))
    mismatched = sorted(path for path in source if source[path] != context_sql[path])
    if mismatched:
        raise _fail("source/context digest mismatch: " + ", ".join(mismatched))

    template_runner = _file_digest(
        template_root / MAX_RUNNER_PATH,
        MAX_RUNNER_PATH,
    )
    if context[MAX_RUNNER_PATH] != template_runner:
        raise _fail(f"template-owned runner mismatch: {MAX_RUNNER_PATH}")
    return context


def verify_max_image_inventory(
    expected: Mapping[str, str],
    image_files: Mapping[str, str],
) -> None:
    """Require the built runtime image to contain the exact verified manifest."""
    actual_all = filtered_inventory(image_files)
    actual = {
        path: digest
        for path, digest in actual_all.items()
        if (path.startswith("drizzle/") and path.endswith(".sql"))
        or path == MAX_RUNNER_PATH
    }
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        detail = []
        if missing:
            detail.append("image missing " + ", ".join(missing))
        if unexpected:
            detail.append("image has unexpected " + ", ".join(unexpected))
        raise _fail("; ".join(detail))
    mismatched = sorted(path for path in expected if expected[path] != actual[path])
    if mismatched:
        raise _fail("context/image digest mismatch: " + ", ".join(mismatched))
