"""Acceptance-lock for BS-27 (dogfood run #24, 2026-06-17): the entity engine has
NO uniqueness mechanism. A generated app cannot declare that a field must be
unique (client email, product SKU, booking slot, username), and the write path
never dedupes — so adding the SAME record twice silently creates a duplicate with
a fresh id and no 409, no warning.

The sharp contrast: the platform DOES know how to enforce uniqueness — it does it
for `users.email` with a REAL Postgres unique index (db/schema.ts:50
`email: text("email").notNull().unique()`) and a clean 409 in the auth register
route (api/auth/register/route.ts:48). But that machinery is wired ONLY for the
fixed auth table; generated *entity* records get none of it.

LIVE-RUNTIME repro (throwaway omnia-dev-dogfood-uniq-probe, built from the
DEPLOYED base image omnia-template-nextjs-entities:dev; starter Task entity, owner
auth via Auth.js credentials — no LLM, no generation, the bug lives in the base
engine so it is identical in every generated app):

  A CRM operator adds the same walk-in client three times (forgot they're already
  in the book). `title` stands for the natural unique key (name + phone):

  CREATE {title:"Иван Петров | +7 999 123-45-67", priority:"high", notes:"VIP"}  -> 201
  CREATE {title:"Иван Петров | +7 999 123-45-67", priority:"high", notes:"VIP"}  -> 201
  CREATE {title:"Иван Петров | +7 999 123-45-67", priority:"high", notes:"VIP"}  -> 201
  GET    Task?limit=200  -> LIST_TOTAL 3 / IDENTICAL_TITLE_ROWS 3 / DISTINCT_IDS 3
                            ALL_SAME_DATA true  (three byte-identical clients)
  GET    Task?title=<that title>  -> FILTER_EXACT_RETURNS 3  (can't tell them apart)
  =>  UNIQUENESS_ENFORCED NO  /  DUPLICATE_BUG CONFIRMED

Two layers of zero uniqueness, both code-proven below:
  1. The schema has no way to DECLARE it. `FieldDef` (registry.ts) carries
     type/required/default/options/entity — there is no `unique` flag, so an AD
     brief that wants "no duplicate clients / unique SKU" cannot express it.
  2. The write path never CHECKS it. createRecord only zod-parses the body and
     inserts; it never queries for an existing row with the same value
     (engine.ts:213-234). No unique index on `records.data->>field` exists either.

Class wider than CRM: any entity with a natural key — client email/phone,
product SKU/slug, username, booking (room, slot), invoice number. Each silently
accepts duplicates, splitting history across rows that look identical.

Family of silent data-integrity gaps: BS-17 (clear = no-op), BS-19 (delete
orphans children), BS-25 (concurrent edit clobbers). Like those, the failure is
invisible at the moment it happens — the create "succeeds".

Why this is a PROPOSAL, not a blind ship: real uniqueness is multi-surface and a
semantic policy call. It needs (a) a `unique: true` field flag in registry +
SYSTEM_PROMPT so the writer can declare it; (b) enforcement — a partial unique
index on `(entity, data->>field)` per declared field, or a pre-insert existence
check, with the scope decided (global vs per-owner: two different owners adding
the same SKU may be fine, one owner adding it twice is not); (c) a friendly 409
surfaced through the SDK + EntityForm (like the register route already does for
email). All of it is base-rebuild + regen-verify and changes write semantics for
every app. Min one fix per run, no blind template ship. → PROPOSAL P-UNIQUE.

These are deterministic file-content asserts (money-free, no container, no LLM);
the dynamic behaviour above was proven once live on the deployed base image.
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


def test_field_def_has_no_unique_flag_today() -> None:
    """EVIDENCE (green today): the entity field schema cannot declare uniqueness.
    `FieldDef` carries type/required/default/options/entity and nothing else — so
    a brief that wants a unique client email / product SKU cannot express it."""
    src = _REGISTRY.read_text(encoding="utf-8")
    body = src[src.index("export interface FieldDef") : src.index("export interface EntityDef")]
    assert "type:" in body  # sanity: we're reading the right block
    assert "required?" in body
    assert "default?" in body
    assert "options?" in body
    assert "entity?" in body
    # The whole point: no uniqueness flag anywhere in the field definition.
    assert "unique" not in body.lower()
    # normalize() (which builds the runtime FieldDef) also never carries a unique flag.
    norm = _fn_body(src, "function normalize(")
    assert "unique" not in norm.lower()


def test_create_record_never_dedupes_today() -> None:
    """EVIDENCE (green today): createRecord zod-parses the body and inserts. It
    never queries for an existing row with the same field value, so duplicates
    are accepted (live: 3 byte-identical Task rows → 3× 201, 3 distinct ids)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function createRecord(")
    assert ".insert(records)" in body
    # No existence/uniqueness check before the insert: the create path does not
    # SELECT for a matching row, and there is no 409 / conflict path.
    assert ".select()" not in body
    assert "409" not in body
    assert "unique" not in body.lower()
    assert "duplicate" not in body.lower()
    assert "conflict" not in body.lower()


def test_records_table_has_no_uniqueness_constraint_today() -> None:
    """EVIDENCE (green today): the platform enforces uniqueness for the FIXED
    auth table (users.email = a real Postgres unique index surfaced as 409 in the
    register route) — but the generic `records` store has no unique index at all,
    so entity duplicates are physically possible."""
    schema = _DB_SCHEMA.read_text(encoding="utf-8")
    # The auth table DOES have a unique index — uniqueness machinery exists...
    assert ".unique()" in schema
    assert "email" in schema
    # ...and the register route turns the violation into a friendly 409.
    register = _REGISTER.read_text(encoding="utf-8")
    assert "409" in register
    # But the `records` block (the generic entity store) carries no unique index.
    rec_i = schema.index("records")
    records_block = schema[rec_i : rec_i + 1200]
    assert ".unique(" not in records_block


@pytest.mark.xfail(
    strict=False,
    reason="BS-27 / P-UNIQUE not yet landed: the entity engine has no way to "
    "declare or enforce a unique field, so adding the same record twice silently "
    "creates a duplicate (no 409). When a `unique` field flag + enforcement "
    "(partial unique index or pre-insert check) + a 409 conflict path land, flip "
    "this to XPASS.",
)
def test_entity_engine_should_support_a_unique_field() -> None:
    """DESIRED: a generated app must be able to declare a field unique and have
    the engine reject a duplicate with a 409 — the same guarantee the platform
    already gives `users.email`. Until then a duplicate client/SKU/slug is
    accepted silently."""
    registry = _REGISTRY.read_text(encoding="utf-8")
    engine = _ENGINE.read_text(encoding="utf-8")
    field_block = registry[
        registry.index("export interface FieldDef") : registry.index(
            "export interface EntityDef"
        )
    ]
    can_declare_unique = "unique" in field_block.lower()
    create_body = _fn_body(engine, "export async function createRecord(")
    enforces_unique = (
        "unique" in create_body.lower()
        or "409" in create_body
        or "conflict" in create_body.lower()
        or "duplicate" in create_body.lower()
    )
    assert can_declare_unique and enforces_unique, (
        "entity engine still cannot declare OR enforce a unique field — "
        "duplicates are accepted with no 409."
    )
