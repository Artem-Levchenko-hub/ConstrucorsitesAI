"""Private bounded evidence for exact legacy schema rejection, never an admission proof."""

from __future__ import annotations

import re
from copy import deepcopy
from itertools import zip_longest
from typing import Any, cast

from yleum_orchestrator.services.project_database_schema_proof import (
    _statements,
    legacy_schema_digest,
)
from yleum_orchestrator.services.publication_migration_pg import (
    MAX_OUTPUT,
    MigrationIOError,
    require,
    schema_command,
)

STAGES = frozenset({"backup_restore", "trial_migration", "live_recheck", "source_recheck"})
KINDS = frozenset({
    "empty", "psql_connect", "psql_control", "sql_set", "sql_create", "sql_alter", "sql_other",
})
ERROR_CODE = "migration_full_schema_mismatch"
_COUNTS = {"line_count", "leading_blank_lines", "trailing_blank_lines"}
_EVIDENCE = {
    "stage", "observed_sha256", "expected_sha256", "observed", "expected", "first_difference",
    "same_nonblank_lines", "same_statement_stream",
}


class SchemaMismatchError(MigrationIOError):
    """Only validated summary fields are retained; no raw dumps in args or attributes."""

    def __init__(self, evidence: dict[str, Any]) -> None:
        super().__init__(ERROR_CODE)
        self.evidence = evidence


def read_schema_dump(pg: Any) -> str:
    """Exactly the existing full_schema RPC, retaining its bounded stdout for comparison."""
    data = pg.command(schema_command())
    require(type(data) is bytes and len(data) <= MAX_OUTPUT, "migration_schema_diagnostic_invalid")
    return cast(bytes, data).decode("utf-8")


def _lines(dump: str) -> list[str]:
    # Match legacy_schema_digest exactly, including blank lines. No SQL parsing,
    # whitespace normalization or literal rewriting can authorize this comparison.
    return [line for line in dump.splitlines()
            if not line.startswith(("\\restrict ", "\\unrestrict ", "--"))]


def _counts(lines: list[str]) -> dict[str, int]:
    leading = trailing = 0
    for line in lines:
        if line.strip():
            break
        leading += 1
    for line in reversed(lines):
        if line.strip():
            break
        trailing += 1
    return {"line_count": len(lines), "leading_blank_lines": leading,
            "trailing_blank_lines": trailing}


def _kind(line: str) -> str:
    """Lexical prefix, even inside a body; this is never a parsed-statement claim."""
    text = line.lstrip()
    if not text:
        return "empty"
    if re.match(r"\\connect(?:\s|$)", text):
        return "psql_connect"
    if text.startswith("\\"):
        return "psql_control"
    for word in ("SET", "CREATE", "ALTER"):
        if re.match(word + r"(?:\s|$)", text, re.IGNORECASE):
            return "sql_" + word.lower()
    return "sql_other"


def _same_statement_stream(observed: str, expected: str) -> bool | None:
    """Diagnostic splitter equality; retain all roles, connection order and literal bytes."""
    try:
        return _statements(observed) == _statements(expected)
    except Exception:
        # Unsupported/incomplete input is unknown, never an equivalent-schema assertion.
        return None


def require_schema_dump(observed: str, expected: str, *, stage: str) -> None:
    require(type(stage) is str and stage in STAGES, "migration_schema_diagnostic_invalid")
    require(
        type(observed) is str and type(expected) is str
        and len(observed.encode("utf-8")) <= MAX_OUTPUT
        and len(expected.encode("utf-8")) <= MAX_OUTPUT,
        "migration_schema_diagnostic_invalid",
    )
    observed_hash, expected_hash = legacy_schema_digest(observed), legacy_schema_digest(expected)
    if observed_hash == expected_hash:
        return
    actual_lines, expected_lines = _lines(observed), _lines(expected)
    # Missing lines use the fixed empty kind; counts distinguish EOF from a blank line.
    line, actual, wanted = next(
        (index, actual, wanted)
        for index, (actual, wanted) in enumerate(
            zip_longest(actual_lines, expected_lines, fillvalue=""), start=1,
        )
        if actual != wanted or index > min(len(actual_lines), len(expected_lines))
    )
    raise SchemaMismatchError({
        "stage": stage, "observed_sha256": observed_hash, "expected_sha256": expected_hash,
        "observed": _counts(actual_lines), "expected": _counts(expected_lines),
        # Filtering blank lines can hide literal differences; this flag is diagnostic only.
        "same_nonblank_lines": ([v for v in actual_lines if v.strip()]
                                == [v for v in expected_lines if v.strip()]),
        "same_statement_stream": _same_statement_stream(observed, expected),
        "first_difference": {"line": line, "observed_kind": _kind(actual),
                             "expected_kind": _kind(wanted)},
    })


def schema_failure_evidence(exc: BaseException) -> dict[str, Any] | None:
    """Revalidate exact owned exception data at the durable private journal boundary."""
    if (type(exc) is not SchemaMismatchError or len(exc.args) != 1
            or type(exc.args[0]) is not str or exc.args[0] != ERROR_CODE):
        return None
    value = vars(exc).get("evidence")
    if (type(value) is not dict or any(type(k) is not str for k in value)
            or set(value) != _EVIDENCE):
        return None
    if type(value["stage"]) is not str or value["stage"] not in STAGES:
        return None
    if type(value["same_nonblank_lines"]) is not bool:
        return None
    if (value["same_statement_stream"] is not None
            and type(value["same_statement_stream"]) is not bool):
        return None
    for key in ("observed_sha256", "expected_sha256"):
        if type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            return None
    if value["observed_sha256"] == value["expected_sha256"]:
        return None
    for key in ("observed", "expected"):
        counts = value[key]
        if (type(counts) is not dict or any(type(k) is not str for k in counts)
                or set(counts) != _COUNTS):
            return None
        if any(type(n) is not int or not 0 <= n <= MAX_OUTPUT + 1 for n in counts.values()):
            return None
        if any(counts[k] > counts["line_count"] for k in _COUNTS - {"line_count"}):
            return None
    first = value["first_difference"]
    if (type(first) is not dict or any(type(k) is not str for k in first)
            or set(first) != {"line", "observed_kind", "expected_kind"}):
        return None
    if (type(first["line"]) is not int or not 1 <= first["line"] <= max(
            value["observed"]["line_count"], value["expected"]["line_count"])):
        return None
    if any(type(first[k]) is not str or first[k] not in KINDS
           for k in ("observed_kind", "expected_kind")):
        return None
    return deepcopy(value)
