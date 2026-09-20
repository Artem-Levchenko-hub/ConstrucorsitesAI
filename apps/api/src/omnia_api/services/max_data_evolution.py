"""Shared MAX data-evolution guidance and its deterministic migration contract."""

import json
import posixpath
from collections.abc import Mapping

from omnia_api.services.portable_cell_contract import (
    PortableGuideExecutor,
    machine_stack_guide_from_executor,
)

MAX_DATA_EVOLUTION_POLICY = """\
MAX DATA EVOLUTION POLICY v1 — preserve current business data across code versions.
These engineering instructions do not change the requested UI/response language.
They apply to initial builds, edits, continuation and repair. They do not grant
new database permissions or certify that rollback is implemented or safe.

BEFORE CHANGING DATA
Treat the existing project database as persistent, including a draft or preview.
Inspect the actual affected schema, applied migrations and existing reads/writes.
Use only the storage granted by the selected provider: managed APIs stay managed;
own SQL is allowed only in the project's supplied dedicated database/sandbox.
Never access the managed platform database or another project's storage.
For a schema or data-semantics change, first write .omnia/data-evolution.md:
affected objects, old/new meaning, forward migration, old-client read/write/delete
behavior, backfill/concurrency strategy, verification and unresolved risks.
Keep this note short; it is a plan, not platform approval or compatibility proof.
No DB change is needed for a label, layout or style-only request.

MIGRATION RULES
MAX mini-app migrations have one durable format: append ordered SQL files directly
under drizzle/*.sql. They are applied only by the platform-owned
scripts/apply-migrations.mjs runner. Never create migrations/, src/lib/db/migrations/,
a custom migration runner, or treat drizzle-kit push as migration evidence.
Use forward, additive migrations. Keep stable table/field identifiers and the
old representation while supported code can still depend on it. UI removal
does not authorize physical removal. Do not DROP/TRUNCATE/reset/recreate storage,
overwrite it with an old backup, or use destructive schema-push/force-reset flags
to fix a build, repair an app, change its design or restore an earlier feature.
Do not rewrite an applied migration: append a new ordered migration. IF NOT
EXISTS does not verify an existing object's definition; inspect the actual result.
New fields should allow omitted values unless an honest domain default and all
supported writers are verified. NOT NULL, UNIQUE, CHECK, enum changes, triggers,
foreign keys/cascades, units and meanings need compatibility review, not just types.
Use bounded, restartable backfills; validate conversions, avoid long locks and
never invent replacement values for invalid data. Preserve the original values.
Do not add migrations, seed/reset or data rewrites to dependency lifecycle hooks,
build commands, service startup or request handlers. Use an explicit migration
step in the authorized development environment. Do not alter platform-owned setup,
credentials or roles to bypass a failure; report missing migration support.
Never claim production migration approval from a successful development command.

WRITE AND ACCESS RULES
Use the current trusted MAX identity; authorize every read, update and delete.
Never take ownership from the request body. Write explicit columns/allowed fields;
patch known fields, preserve unknown fields/JSON keys, and distinguish omitted
values from an intentional null. Avoid SELECT * assumptions and positional inserts.
Use the existing concurrency contract, or introduce a verified one for every writer;
handle conflicts instead of blindly overwriting a newer row. Do not reset ownership,
permissions, new fields or dependent records when applying an older form's payload.

TARGETED EXAMPLES (adapt names to the actual schema; never execute blindly)
1. v1 has customers(id, owner_id, name); v2 needs an optional surname:
   ALTER TABLE customers ADD COLUMN surname text;
   Keep v1 INSERT INTO customers (id, owner_id, name) VALUES ($1, $2, $3).
   A v1 name update must SET name only, leaving v2 surname untouched. Returning to
   v1 hides surname in the UI; it must not DROP the column or clear its values.
   New v1 customers have surname NULL, not a fabricated surname.
2. Rename the label "Surname" to "Family name": change the label, keep surname.
   A required physical rename needs a new representation plus a tested transition;
   do not ALTER COLUMN RENAME under old readers/writers.
3. Change price from decimal major units to integer minor units: add price_minor,
   preserve price, define currency/rounding/range and validate each conversion.
   Declare the authoritative value and synchronize both representations on EVERY
   supported write. A one-time price * 100 backfill is insufficient while old code
   still changes price. If meaning or conversion is ambiguous, preserve current
   behavior and report the exact decision needed; do not silently reinterpret it.
4. Given an established row_version contract maintained by ALL writers:
   UPDATE customers SET name=$1, row_version=row_version+1
   WHERE id=$2 AND owner_id=$3 AND row_version=$4 RETURNING id;
   Zero rows means missing/unauthorized/conflict, never a successful save. Resolve
   it without disclosing another owner's data. For object-valued JSON, patch only
   validated keys (e.g. jsonb_set on the current object), never replace the whole
   document with an old form. A nested object replacement can still erase new keys.
5. v2 adds invoices to a customer: v1's DELETE may cascade into those invoices.
   Inspect dependencies and preserve them with an explicit verified deletion policy;
   do not assume hiding invoices or using the same schema makes v1 deletion safe.
   Likewise an unknown new status must not be rewritten to an old default status.

VERIFY AND REPORT
Use isolated test data with external payments/messages/jobs disabled, never reset
the working database for tests. Test real create/read/update, reload persistence,
cross-user read/write denial, old writes preserving new fields, new records created
by old code and a return to new code. Re-read affected values and actual schema.
Keep exact dependencies/lockfiles. Report applied migrations, tests actually run,
compatibility limits and unavailable checks in the user's language. Green build,
runtime health, a screenshot or this note alone do not prove data safety.
Code restoration preserves current business data; it does not undo payments,
messages or intentional user deletions. Do not auto-publish or claim restore ready.
""".strip()


_CANONICAL_RUNNER = "scripts/apply-migrations.mjs"
_SCHEMA_PATH = "src/lib/db/schema.ts"


def _normalized_path(path: str) -> str:
    raw = path.strip().replace("\\", "/")
    if (
        not raw
        or raw.startswith(("/", "~"))
        or "\x00" in raw
        or ".." in raw.split("/")
    ):
        raise ValueError(f"unsafe path: {path!r}")
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."} or normalized.startswith("../"):
        raise ValueError(f"unsafe path: {path!r}")
    return normalized


def _canonical_migration(path: str) -> bool:
    return (
        path.startswith("drizzle/")
        and path.count("/") == 1
        and path.endswith(".sql")
    )


def _normalized_source(
    source: Mapping[str, str], *, label: str,
) -> tuple[dict[str, str], tuple[str, ...]]:
    normalized: dict[str, str] = {}
    aliases: dict[str, str] = {}
    errors: list[str] = []
    for raw_path, content in source.items():
        try:
            path = _normalized_path(raw_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        previous = aliases.get(path)
        if previous is not None and previous != raw_path:
            errors.append(
                f"{label} source has conflicting path aliases {previous!r} and {raw_path!r}"
            )
            continue
        aliases[path] = raw_path
        normalized[path] = content
    return normalized, tuple(errors)


def max_migration_contract_errors(
    before: Mapping[str, str], after: Mapping[str, str],
) -> tuple[str, ...]:
    """Return fail-closed MAX migration violations for one candidate diff.

    The finalization caller supplies the accepted baseline and complete candidate
    source maps.  This function is intentionally pure so generation can reject an
    unsafe candidate before it is promoted or described as compatible.
    """

    normalized_before, before_errors = _normalized_source(before, label="baseline")
    normalized_after, after_errors = _normalized_source(after, label="candidate")
    normalization_errors = (*before_errors, *after_errors)
    if normalization_errors:
        return normalization_errors
    changed = sorted(
        path
        for path in set(normalized_before) | set(normalized_after)
        if normalized_before.get(path) != normalized_after.get(path)
    )
    new_canonical = [
        path
        for path in changed
        if _canonical_migration(path)
        and path not in normalized_before
        and bool(normalized_after.get(path, "").strip())
    ]
    errors: list[str] = []
    existing_canonical = sorted(
        path for path in normalized_before if _canonical_migration(path)
    )
    if existing_canonical:
        last_existing = existing_canonical[-1]
        for path in sorted(new_canonical):
            if path <= last_existing:
                errors.append(
                    f"{path} must append after existing canonical migration {last_existing}"
                )
    for path in changed:
        removed = path in normalized_before and path not in normalized_after
        if _canonical_migration(path) and path in normalized_before:
            errors.append(
                f"{path} is an existing canonical migration and must remain immutable"
            )
        elif (
            path.startswith("drizzle/")
            and path.endswith(".sql")
            and not _canonical_migration(path)
        ) and not removed:
            errors.append(f"{path} is not canonical; use direct drizzle/*.sql files")
        elif (
            path.startswith("migrations/")
            or path.startswith("src/lib/db/migrations/")
        ) and not removed:
            errors.append(f"{path} is not canonical; use drizzle/*.sql")
        elif (
            path.startswith("scripts/")
            and path != _CANONICAL_RUNNER
            and "migrat" in path.rsplit("/", 1)[-1].lower()
        ) and not removed:
            errors.append(f"{path} is a custom migration runner; use drizzle/*.sql")
        elif path == "package.json":
            try:
                before_scripts = json.loads(normalized_before.get(path, "{}")).get("scripts", {})
                after_scripts = json.loads(normalized_after.get(path, "{}")).get("scripts", {})
            except (AttributeError, TypeError, ValueError):
                before_scripts, after_scripts = {}, {}
            if isinstance(before_scripts, dict) and isinstance(after_scripts, dict):
                introduced = any(
                    before_scripts.get(name) != command
                    and isinstance(command, str)
                    and (
                        "drizzle-kit push" in command
                        or (
                            "migrat" in (str(name) + " " + command).lower()
                            and command.strip() != "node scripts/apply-migrations.mjs"
                        )
                    )
                    for name, command in after_scripts.items()
                )
                if introduced:
                    errors.append(
                        "package.json introduces a custom migration command; use drizzle/*.sql"
                    )
    if _CANONICAL_RUNNER in changed:
        errors.append(f"{_CANONICAL_RUNNER} is platform-owned and must not be changed")
    if _SCHEMA_PATH in changed and not new_canonical:
        errors.append(
            f"{_SCHEMA_PATH} changed without a new canonical drizzle/*.sql migration"
        )
    return tuple(errors)


async def build_max_agent_guide(
    legacy: str, executor: PortableGuideExecutor | None = None,
) -> str:
    """Apply policy after provider selection, which may replace the entire guide."""
    guide = (
        await machine_stack_guide_from_executor(legacy, executor)
        if executor is not None else legacy
    )
    return f"{guide}\n\n{MAX_DATA_EVOLUTION_POLICY}".strip()
