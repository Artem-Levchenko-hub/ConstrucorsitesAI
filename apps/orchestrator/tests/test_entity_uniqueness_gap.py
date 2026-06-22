"""BS-27 / P-UNIQUE — uniqueness for entity records. ORIGINAL GAP (dogfood run #24,
2026-06-17): the entity engine had NO uniqueness mechanism. A generated app could
not declare that a field must be unique (client email, product SKU, booking slot,
username), and the write path never deduped — so adding the SAME record twice
silently created a duplicate with a fresh id and no 409, no warning.

The sharp contrast back then: the platform DID know how to enforce uniqueness — it
does it for `users.email` with a REAL Postgres unique index (db/schema.ts:50
`email: text("email").notNull().unique()`) and a clean 409 in the auth register
route (api/auth/register/route.ts). But that machinery was wired ONLY for the fixed
auth table; generated *entity* records got none of it.

NOW LANDED (this file asserts the engine's CURRENT behaviour):
  1. DECLARE — `FieldDef` (registry.ts) carries a `unique?: boolean` flag, and
     `normalize()` materialises it onto the runtime field, so a brief that wants
     "no duplicate clients / unique SKU" can express it.
  2. ENFORCE — both write paths run a shared `assertUnique` helper (engine.ts)
     BEFORE the write: it SELECTs for a row with the same (case-insensitive) value
     for any `unique` field — owner-scoped for `owner` entities, global for
     public/admin — and throws `EngineError(409, …)` on a hit. `createRecord`
     calls `assertUnique(def, owner, data)`; `updateRecord` calls
     `assertUnique(def, owner, merged, id)` with an `excludeId` so re-saving the
     SAME row unchanged is not a false duplicate.

REMAINING (honest) limitation, still TRUE and asserted below: enforcement is
APP-LEVEL only — there is NO db-level unique constraint on `records.data->>field`.
The `assertUnique` helper itself documents the TOCTOU window (two concurrent
creates can race past the SELECT before either INSERT lands). The `records` table
still carries only its btree index, no `.unique(...)`. Good enough for the
single-owner CRUD this serves, but not a hard guarantee — see
`test_records_uniqueness_is_app_level_no_db_constraint` (passing evidence) and the
tight xfail at the bottom for the db-constraint that is genuinely NOT there.

These are deterministic file-content asserts (money-free, no container, no LLM);
the original dynamic duplicate-bug was proven once live on the deployed base image.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_DB_SCHEMA = _ENTITIES / "src" / "lib" / "db" / "schema.ts"
_REGISTER = _ENTITIES / "src" / "app" / "api" / "auth" / "register" / "route.ts"


def _fn_body(src: str, start_marker: str) -> str:
    """Return source from `start_marker` up to the next function declaration
    (exported or not) — so a helper that follows the target function is not
    swept into its body."""
    i = src.index(start_marker)
    after = i + len(start_marker)
    ends = [
        src.find(m, after)
        for m in ("\nexport ", "\nasync function ", "\nfunction ")
    ]
    ends = [e for e in ends if e != -1]
    nxt = min(ends) if ends else -1
    return src[i : nxt if nxt != -1 else len(src)]


def test_field_def_has_unique_flag_is_declared() -> None:
    """ENGINE NOW: the entity field schema CAN declare uniqueness. `FieldDef`
    carries a `unique?: boolean` flag alongside type/required/default/options/
    entity, and `normalize()` (which builds the runtime FieldDef from loose JSON)
    materialises it — so a brief that wants a unique client email / product SKU
    can express it and the engine sees it at runtime."""
    src = _REGISTRY.read_text(encoding="utf-8")
    body = src[src.index("export interface FieldDef") : src.index("export interface EntityDef")]
    assert "type:" in body  # sanity: we're reading the right block
    assert "required?" in body
    assert "default?" in body
    assert "options?" in body
    assert "entity?" in body
    # The fix: a uniqueness flag now lives on the field definition.
    assert "unique?" in body
    assert "unique?: boolean" in body
    # normalize() (which builds the runtime FieldDef) now carries the flag through.
    norm = _fn_body(src, "function normalize(")
    assert "unique:" in norm
    assert "Boolean(f?.unique)" in norm


def test_create_record_dedupes_via_assert_unique_is_enforced() -> None:
    """ENGINE NOW: createRecord runs the shared `assertUnique` guard BEFORE the
    insert, so a duplicate `unique` value is rejected rather than silently stored.
    The guard itself SELECTs for an existing row with the same value and throws a
    409 on a hit — so the live "3 byte-identical Task rows → 3× 201" bug is closed
    on the create path."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function createRecord(")
    # Still inserts...
    assert ".insert(records)" in body
    # ...but now behind the integrity guard: createRecord calls assertUnique
    # (and it is awaited BEFORE the insert).
    assert "await assertUnique(def, owner, data)" in body
    assert body.index("assertUnique") < body.index(".insert(records)")

    # The shared helper is the real enforcement: it SELECTs a same-value row
    # (case-insensitive for strings) and throws a 409 EngineError on a hit.
    helper = _fn_body(src, "async function assertUnique(")
    assert "if (!f.unique) continue" in helper  # only acts on declared-unique fields
    assert ".select(" in helper  # existence check before the write
    assert "lower(" in helper  # case-insensitive string compare
    assert "EngineError(409" in helper  # friendly conflict, not a silent dupe


def test_update_record_also_enforces_unique_with_excludeid_is_enforced() -> None:
    """ENGINE NOW (this session's addition): the UPDATE path enforces uniqueness
    too, not just create. updateRecord runs the same `assertUnique` guard on the
    MERGED values, passing the row's own id as `excludeId` so re-saving the row
    unchanged is not flagged as a duplicate of itself — but changing a `unique`
    field to collide with ANOTHER row's value is still rejected with a 409."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    # Update enforces uniqueness on the merged values, excluding THIS row.
    assert "await assertUnique(def, owner, merged, id)" in body
    # The guard runs before the UPDATE write lands.
    assert body.index("assertUnique") < body.index(".update(records)")

    # The shared helper actually honours `excludeId` (skips the row being updated).
    helper = _fn_body(src, "async function assertUnique(")
    assert "excludeId?: string" in helper
    assert "if (excludeId)" in helper
    assert "<> ${excludeId}" in helper


def test_records_uniqueness_is_app_level_no_db_constraint() -> None:
    """HONEST PARTIAL: enforcement is APP-LEVEL only. The platform DOES back the
    FIXED auth table with a real Postgres unique index (users.email) surfaced as a
    409 in the register route — but the generic `records` store still has NO unique
    index on `data->>field`. The `assertUnique` SELECT-then-INSERT therefore has a
    TOCTOU window (two concurrent creates can both pass the check). This test pins
    the gap that is genuinely still open so a future db-level fix is noticed."""
    schema = _DB_SCHEMA.read_text(encoding="utf-8")
    # The auth table DOES have a unique index — the db-level machinery exists...
    assert ".unique()" in schema
    assert "email" in schema
    # ...and the register route turns the violation into a friendly 409.
    register = _REGISTER.read_text(encoding="utf-8")
    assert "409" in register
    # But the `records` block (the generic entity store) STILL carries no unique
    # index — so entity uniqueness is app-level only (TOCTOU), not a hard
    # constraint. This is the documented remaining limitation, not fake-green.
    rec_i = schema.index("records")
    records_block = schema[rec_i : rec_i + 1200]
    assert ".unique(" not in records_block
    # The engine's assertUnique doc openly acknowledges the app-level /
    # not-a-DB-constraint scope (the TOCTOU race) — it never claims a hard guarantee.
    engine = _ENGINE.read_text(encoding="utf-8")
    assert "assertUnique" in engine
    assert "TOCTOU" in engine and "not a DB constraint" in engine


def test_entity_engine_supports_a_unique_field() -> None:
    """LANDED (was DESIRED/xfail): a generated app can now declare a field unique
    AND have the engine reject a duplicate with a 409 — the same guarantee the
    platform already gives `users.email`. A brief that needs a unique client / SKU
    / slug can express it (`unique?` on FieldDef) and the write path enforces it
    (createRecord → assertUnique → 409)."""
    registry = _REGISTRY.read_text(encoding="utf-8")
    engine = _ENGINE.read_text(encoding="utf-8")
    field_block = registry[
        registry.index("export interface FieldDef") : registry.index(
            "export interface EntityDef"
        )
    ]
    can_declare_unique = "unique" in field_block.lower()
    # Enforcement now lives in the shared assertUnique helper that createRecord
    # awaits; the create body references it by name even though the 409 is thrown
    # inside the helper.
    create_body = _fn_body(engine, "export async function createRecord(")
    helper = _fn_body(engine, "async function assertUnique(")
    enforces_unique = "assertUnique" in create_body and "EngineError(409" in helper
    assert can_declare_unique and enforces_unique, (
        "entity engine must be able to declare AND enforce a unique field — "
        "duplicates should be rejected with a 409."
    )


@pytest.mark.xfail(
    strict=False,
    reason="BS-27 closed at the APP level (FieldDef.unique + assertUnique SELECT "
    "+ 409 on both create and update), but there is still NO db-level unique "
    "constraint on records.data->>field: the SELECT-then-INSERT in assertUnique "
    "has a TOCTOU window, so two concurrent creates can still both pass the check "
    "and produce a duplicate. Flip to XPASS only when a partial unique index on "
    "(entity, lower(data->>field)) per declared field lands in db/schema.ts.",
)
def test_records_should_have_db_level_unique_constraint() -> None:
    """DESIRED (genuinely NOT done): app-level uniqueness is racy. A real guarantee
    needs a db-level partial unique index on the generic `records` store (scoped
    per entity + declared field, like users.email is for auth) so concurrent
    creates can't both win. Until then `assertUnique` is best-effort only."""
    schema = _DB_SCHEMA.read_text(encoding="utf-8")
    rec_i = schema.index("records")
    records_block = schema[rec_i : rec_i + 1200]
    # A real db-level guard on the records store would surface as a unique index
    # in the `records` table block (analogous to users.email's `.unique()`).
    assert ".unique(" in records_block or "uniqueIndex(" in records_block, (
        "records store still has no db-level unique constraint — entity "
        "uniqueness is app-level (TOCTOU-racy) only."
    )
