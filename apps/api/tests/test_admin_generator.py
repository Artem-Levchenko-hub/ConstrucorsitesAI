"""Tests for `services.admin_generator` — Drizzle schema.ts → admin CRUD.

Phase 1.2 foundation. The generator is pure (string in, dict out) so
all coverage is sync unit tests — no DB, no orchestrator, no IO.
A future commit wires the output into the dev container via the
existing hot-reload pipeline.
"""

from __future__ import annotations

import pytest

from omnia_api.services.admin_generator import (
    Column,
    Table,
    emit_next_admin_index,
    emit_next_admin_page,
    generate_next_admin_files,
    parse_schema,
)

# ─── Parser fixtures ──────────────────────────────────────────────────────

EXAMPLE_SCHEMA = """\
import { sql } from "drizzle-orm";
import { pgTable, text, timestamp, uuid, integer, boolean } from "drizzle-orm/pg-core";

export const examples = pgTable("examples", {
  id: uuid("id").primaryKey().defaultRandom(),
  title: text("title").notNull(),
  body: text("body"),
  views: integer("views").notNull().default(0),
  isPublished: boolean("is_published").notNull().default(false),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const orders = pgTable("orders", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  amount: integer("amount").notNull(),
});
"""


# ─── parse_schema ─────────────────────────────────────────────────────────


def test_parse_finds_every_table() -> None:
    tables = parse_schema(EXAMPLE_SCHEMA)
    assert {t.sql_name for t in tables} == {"examples", "orders"}


def test_parse_preserves_column_order() -> None:
    tables = parse_schema(EXAMPLE_SCHEMA)
    examples = next(t for t in tables if t.sql_name == "examples")
    order = [c.name for c in examples.columns]
    assert order == ["id", "title", "body", "views", "isPublished", "createdAt", "updatedAt"]


def test_parse_captures_column_types() -> None:
    tables = parse_schema(EXAMPLE_SCHEMA)
    cols = {c.name: c.type for c in tables[0].columns}
    assert cols["id"] == "uuid"
    assert cols["title"] == "text"
    assert cols["views"] == "integer"
    assert cols["isPublished"] == "boolean"
    assert cols["createdAt"] == "timestamp"


def test_parse_captures_constraints() -> None:
    tables = parse_schema(EXAMPLE_SCHEMA)
    cols = {c.name: c for c in tables[0].columns}
    assert cols["id"].is_primary is True
    assert cols["title"].nullable is False
    assert cols["body"].nullable is True
    assert cols["views"].has_default is True
    assert cols["createdAt"].has_default is True


def test_parse_captures_foreign_keys() -> None:
    tables = parse_schema(EXAMPLE_SCHEMA)
    orders = next(t for t in tables if t.sql_name == "orders")
    user_id = next(c for c in orders.columns if c.name == "userId")
    assert user_id.references == "users.id"
    other = next(c for c in orders.columns if c.name == "amount")
    assert other.references is None


def test_parse_unknown_type_is_skipped() -> None:
    """Drizzle has many column types we don't (yet) generate UI for —
    point, interval, custom types, etc. Parser silently skips them
    rather than crashing the whole admin generation."""
    src = """
    import { pgTable, text, point } from "drizzle-orm/pg-core";
    export const places = pgTable("places", {
      title: text("title").notNull(),
      coords: point("coords"),
    });
    """
    tables = parse_schema(src)
    assert len(tables) == 1
    assert {c.name for c in tables[0].columns} == {"title"}


def test_parse_handles_composite_key_syntax() -> None:
    """Drizzle's composite-key form passes a second-arg lambda
    `(t) => ({ pk: primaryKey({columns:[...]}) })`. The table regex must
    still close on the outer `})` and not bail on the inner one."""
    src = """
    import { pgTable, text, primaryKey } from "drizzle-orm/pg-core";
    export const accounts = pgTable(
      "accounts",
      {
        provider: text("provider").notNull(),
        providerAccountId: text("provider_account_id").notNull(),
      },
      (account) => ({
        pk: primaryKey({ columns: [account.provider, account.providerAccountId] }),
      }),
    );
    """
    tables = parse_schema(src)
    assert len(tables) == 1
    assert tables[0].sql_name == "accounts"
    assert {c.name for c in tables[0].columns} == {"provider", "providerAccountId"}


# ─── emitter ──────────────────────────────────────────────────────────────


def test_emit_includes_protected_role_admin() -> None:
    """Generated page MUST gate on role=admin so non-admins can't reach
    CRUD. Forgetting this gate is the kind of bug that ends up on the
    public internet, so we lock it in with a test."""
    table = Table(
        js_name="orders",
        sql_name="orders",
        columns=(Column("id", "id", "uuid", False, True, True),),
    )
    out = emit_next_admin_page(table)
    assert '<Protected role="admin">' in out


def test_emit_writes_table_and_form_sections() -> None:
    table = Table(
        js_name="examples",
        sql_name="examples",
        columns=(
            Column("id", "id", "uuid", False, True, True),
            Column("title", "title", "text", False, False, False),
            Column("body", "body", "text", True, False, False),
        ),
    )
    out = emit_next_admin_page(table)
    assert "<table" in out
    assert '<form action={createRow}' in out
    # `id` is system-managed — form must NOT have an `<input name="id">`
    assert 'name="id"' not in out
    # Real fields render
    assert 'name="title"' in out
    assert 'name="body"' in out


def test_emit_uses_correct_drizzle_import() -> None:
    """Generated file must import the table by its JS name from
    `@/lib/db/schema` — not by its SQL name, which doesn't exist as
    an export."""
    table = Table(
        js_name="examples",
        sql_name="examples",
        columns=(Column("title", "title", "text", False, False, False),),
    )
    out = emit_next_admin_page(table)
    assert 'import { examples } from "@/lib/db/schema"' in out


def test_emit_route_uses_dashed_slug() -> None:
    """Underscores in SQL table names become dashes in the URL —
    consistent with Next's app router convention."""
    table = Table(
        js_name="orderItems",
        sql_name="order_items",
        columns=(Column("title", "title", "text", False, False, False),),
    )
    assert table.safe_route == "order-items"


def test_emit_index_links_to_every_table() -> None:
    tables = (
        Table("a", "users_table", (Column("title", "title", "text", False, False, False),)),
        Table("b", "products", (Column("title", "title", "text", False, False, False),)),
    )
    out = emit_next_admin_index(tables)
    assert "/admin/users-table" in out
    assert "/admin/products" in out


# ─── end-to-end ───────────────────────────────────────────────────────────


def test_generate_emits_index_and_per_table_pages() -> None:
    files = generate_next_admin_files(EXAMPLE_SCHEMA)
    assert "src/app/admin/page.tsx" in files
    assert "src/app/admin/examples/page.tsx" in files
    assert "src/app/admin/orders/page.tsx" in files


def test_generate_skips_auth_system_tables() -> None:
    """Auth.js tables (users/accounts/sessions/verification_tokens) are
    system-managed — generic admin CRUD would let admins corrupt active
    sessions or hand-edit password hashes. Skip them; dedicated user-
    management UI is a follow-up commit."""
    src = """
    import { pgTable, uuid, text } from "drizzle-orm/pg-core";
    export const users = pgTable("users", {
      id: uuid("id").primaryKey().defaultRandom(),
      email: text("email").notNull(),
    });
    export const sessions = pgTable("sessions", {
      sessionToken: text("session_token").notNull(),
      userId: uuid("user_id").notNull(),
    });
    export const orders = pgTable("orders", {
      id: uuid("id").primaryKey().defaultRandom(),
      total: text("total").notNull(),
    });
    """
    files = generate_next_admin_files(src)
    paths = set(files.keys())
    assert "src/app/admin/users/page.tsx" not in paths
    assert "src/app/admin/sessions/page.tsx" not in paths
    assert "src/app/admin/orders/page.tsx" in paths


def test_generate_is_deterministic() -> None:
    """Same input MUST produce byte-identical output — without this,
    repeated regenerations would dirty git on every prompt even when
    the schema didn't change."""
    a = generate_next_admin_files(EXAMPLE_SCHEMA)
    b = generate_next_admin_files(EXAMPLE_SCHEMA)
    assert a == b


def test_generate_empty_schema_emits_nothing() -> None:
    """No tables → no files. Caller (orchestrator) treats empty dict
    as no-op."""
    assert generate_next_admin_files("// just imports, no tables") == {}


@pytest.mark.parametrize(
    "col_type",
    ["uuid", "text", "integer", "numeric", "timestamp", "boolean", "jsonb", "serial"],
)
def test_every_supported_type_renders_a_form_field(col_type: str) -> None:
    """Smoke check: each supported column type must produce a form
    field in the emitted page (or be system-managed and intentionally
    hidden)."""
    table = Table(
        js_name="things",
        sql_name="things",
        columns=(Column("value", "value", col_type, True, False, False),),
    )
    out = emit_next_admin_page(table)
    assert 'name="value"' in out
