"""Compile-time DB contract of the trusted, seeded MAX Next substrate."""

from collections.abc import Mapping

from yleum_api.services.max_project_kit import _template_file

DB_INDEX = "src/lib/db/index.ts"
DB_SCHEMA = "src/lib/db/schema.ts"
DB_SHADOWS = frozenset(f"src/lib/db{suffix}" for suffix in (".ts", ".tsx", ".d.ts", ".js", ".jsx"))


def has_trusted_max_db(files: Mapping[str, str]) -> bool:
    # Presence of a Next-looking path/manifest is not attestation. A custom
    # portable stack stays unrestricted; an exact platform module anchors this
    # narrow contract and cannot be removed by an accepted model action.
    return files.get(DB_INDEX) == _template_file(DB_INDEX)


def _preserves_core_schema(source: str) -> bool:
    # Permit the portable seed's leading comments, then preserve executable
    # imports and definitions verbatim. Substring/regex export checks would also
    # accept definitions hidden in comments or a function. New imports/tables can
    # be appended; this is deliberately not a general TypeScript parser.
    remaining = source.lstrip()
    while remaining.startswith("//"):
        _comment, separator, remaining = remaining.partition("\n")
        if not separator:
            return False
        remaining = remaining.lstrip()
    return remaining.startswith(_template_file(DB_SCHEMA).rstrip())


def max_db_compatibility_violations(files: Mapping[str, str]) -> tuple[str, ...]:
    """Called only after the pre-action tree attests the trusted DB substrate."""
    violations = []
    if not has_trusted_max_db(files):
        violations.append(DB_INDEX)
    if not _preserves_core_schema(files.get(DB_SCHEMA, "")):
        violations.append(DB_SCHEMA)
    violations.extend(sorted(DB_SHADOWS.intersection(files)))
    return tuple(violations)


def max_db_compatibility_error(paths: tuple[str, ...]) -> str:
    return (
        "MAX database compatibility: preserve the seeded src/lib/db/index.ts "
        "exports (db, pool, schema, withMaxUser); do not shadow it with src/lib/db.ts "
        "or another module extension. Keep the original schema.ts imports and core "
        "declarations unchanged; append product imports/tables and a new canonical "
        "drizzle/*.sql migration. Rejected paths: " + ", ".join(paths)
    )
