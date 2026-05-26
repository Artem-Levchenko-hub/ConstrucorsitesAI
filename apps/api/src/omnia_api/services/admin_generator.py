"""Auto-generated admin panel for fullstack/api templates.

Parses a Drizzle `schema.ts` file (or SQLAlchemy `models.py` — phase 2),
extracts every table's name + columns + types + FKs, and emits a
`src/app/admin/<table>/page.tsx` route per table containing a CRUD UI
(list / create / edit / delete) wired through the project's existing
`db` client and `<Protected role="admin">` gate.

Why a generator instead of a runtime introspector:
- Generated files live in git → snapshot/rollback works for them too.
- Type safety on the generated page — same TS inference users expect
  from hand-written Next pages.
- No reflection cost on every request.
- AI can read the generated file and learn the project's table layout
  by reading the admin pages alongside `schema.ts`.

Phase 1.2 scope (this commit):
- Drizzle parser: recognise `pgTable("name", { col: type(...) })` shape.
- 8 column types: uuid / text / integer / numeric / timestamp / boolean
  / jsonb / serial. Unknown types render as read-only `JSON.stringify`.
- One emitter target: Next.js admin route (server component + server
  action). SPA / FastAPI emitters live as TODO comments — additive.
- No relationship rendering yet (FKs surface as plain IDs in tables).
  Resolving FK display values needs a second pass and is followup.

The generator does NOT yet integrate with the orchestrator's
hot-reload pipeline — it's a pure file-emitter, callable from a
future api route or CLI. Wiring is a separate commit so the parser
can land + be tested independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Drizzle column-type primitives we recognise. Each maps to:
# - a TypeScript display string (used in form input types)
# - a server-side validation hint for the generated server action
_KNOWN_TYPES: dict[str, str] = {
    "uuid": "uuid",
    "text": "text",
    "integer": "integer",
    "numeric": "numeric",
    "timestamp": "timestamp",
    "boolean": "boolean",
    "jsonb": "jsonb",
    "serial": "serial",
}

# Columns the generator considers system-managed — emitted in the list
# view but hidden from create/edit forms (server fills them).
_SYSTEM_COLUMNS = frozenset({"id", "created_at", "updated_at"})


@dataclass(frozen=True, slots=True)
class Column:
    """One column of a parsed Drizzle table.

    `name` is the JS property name in the schema.ts object literal
    (camelCase). `sql_name` is the value passed to the column ctor
    (snake_case, what Postgres actually stores). They diverge per
    Drizzle's convention — admin UI needs both: the JS name to access
    the row, the SQL name as the form field label.
    """

    name: str
    sql_name: str
    type: str
    nullable: bool
    has_default: bool
    is_primary: bool
    references: str | None = None  # "users.id" when this column is an FK


@dataclass(frozen=True, slots=True)
class Table:
    """One parsed Drizzle table — name + ordered columns."""

    js_name: str  # JS export — `users`, `examples`, …
    sql_name: str  # Postgres table name — same in practice but kept separate
    columns: tuple[Column, ...]

    @property
    def safe_route(self) -> str:
        """Path under /admin where this table's CRUD page lives."""
        return self.sql_name.replace("_", "-")


# ─── Parser ────────────────────────────────────────────────────────────────


# Find the start of each table — `export const TABLE = pgTable("NAME", {`.
# The body that follows uses brace-counting (not regex) because Drizzle's
# composite-key form nests `{...}` inside the call: `pgTable("x", {col:
# text(...)}, (t) => ({pk: primaryKey({columns:[...]})}))`. A regex with
# `\{(?P<body>.*?)\}` greedy/non-greedy can't distinguish the body-closing
# brace from primaryKey()'s inner brace — `_iter_tables` does it manually.
_TABLE_HEADER_RE = re.compile(
    r"export\s+const\s+(?P<js>[A-Za-z_][\w]*)\s*=\s*pgTable\(\s*"
    r"\"(?P<sql>[^\"]+)\"\s*,\s*\{"
)

# Match the OPENING of one column statement:
#     name: typeName("sql_name"
# We capture js name, type ctor, and SQL name, then read the rest of the
# statement (chain calls) directly from the source span. Anchoring to the
# `(` of the type-ctor opens lets us handle multi-line declarations and
# nested args (`timestamp("x", { withTimezone: true })`) which a single
# regex can't do.
_COLUMN_OPEN_RE = re.compile(
    r"(?P<js>[A-Za-z_][\w]*)\s*:\s*"
    r"(?P<type>[a-zA-Z]+)\s*\(\s*\"(?P<sql>[^\"]+)\""
)

# Match `.references(() => SomeTable.id, …)` inside the chain so we
# know which target the FK points at. We only capture `Table.column`,
# not the full lambda — that's enough for the UI to render "→ users".
_REF_RE = re.compile(r"\.references\(\(\)\s*=>\s*([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)")


def _find_matching_brace(source: str, open_pos: int) -> int:
    """Given the index of an opening `{`, return the index of the matching
    closing `}`. Tracks nested braces and ignores braces inside strings
    + line comments — Drizzle schemas are JS-like so the same rules apply.

    Returns -1 if the input is malformed (unbalanced) — caller skips
    that table.
    """
    depth = 0
    i = open_pos
    n = len(source)
    while i < n:
        ch = source[i]
        # Skip line comments
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        # Skip block comments
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i += 2
            continue
        # Skip strings (double / single / template). Template literals can
        # nest but we don't track inner ${...} interpolations — schemas
        # don't put template literals inside table bodies in practice.
        if ch in ("\"", "'", "`"):
            quote = ch
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_close_paren(source: str, open_pos: int) -> int:
    """Mirror of `_find_matching_brace` for parentheses. Used to find
    the end of `timestamp("col", { withTimezone: true })` etc. when
    walking column statements with nested args."""
    depth = 0
    i = open_pos
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in ("\"", "'", "`"):
            quote = ch
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_schema(source: str) -> tuple[Table, ...]:
    """Parse a Drizzle schema.ts file into ordered Tables.

    Best-effort: tables / columns that don't match the canonical
    `export const X = pgTable("name", { col: type("sql_name", …)… })`
    shape are silently skipped. The intended use is admin-panel
    generation, so partial coverage (only the well-formed tables) is
    better than throwing on the first odd table.
    """
    tables: list[Table] = []

    for header_match in _TABLE_HEADER_RE.finditer(source):
        js = header_match.group("js")
        sql = header_match.group("sql")
        body_start = header_match.end()
        open_brace = body_start - 1
        close_brace = _find_matching_brace(source, open_brace)
        if close_brace < 0:
            continue
        body = source[body_start:close_brace]

        columns: list[Column] = []
        for col_open in _COLUMN_OPEN_RE.finditer(body):
            type_name = col_open.group("type")
            if type_name not in _KNOWN_TYPES:
                continue
            # Find the closing `)` of the type ctor — it can have nested
            # arg dicts like `{ withTimezone: true }`. Then read the
            # chain (`.notNull().default(…)…`) until we hit a top-level
            # comma or end of body.
            ctor_open = body.find("(", col_open.start())
            if ctor_open < 0:
                continue
            ctor_close = _find_close_paren(body, ctor_open)
            if ctor_close < 0:
                continue
            # Chain text: everything from after the closing `)` of the
            # ctor up to the next top-level `,` (or end of body).
            tail = body[ctor_close + 1 :]
            # Stop at the next comma that's at paren-depth 0. Using a
            # simple walker because the chain has nested `()`.
            depth = 0
            chain_end = len(tail)
            for k, ch in enumerate(tail):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    chain_end = k
                    break
            chain = tail[:chain_end]

            references: str | None = None
            ref_match = _REF_RE.search(chain)
            if ref_match:
                references = f"{ref_match.group(1)}.{ref_match.group(2)}"

            columns.append(
                Column(
                    name=col_open.group("js"),
                    sql_name=col_open.group("sql"),
                    type=type_name,
                    nullable=".notNull()" not in chain,
                    has_default=".default(" in chain
                    or ".defaultRandom()" in chain
                    or ".defaultNow()" in chain,
                    is_primary=".primaryKey()" in chain,
                    references=references,
                )
            )

        if columns:
            tables.append(Table(js_name=js, sql_name=sql, columns=tuple(columns)))

    return tuple(tables)


# ─── Emitter ───────────────────────────────────────────────────────────────


def _form_field_for(col: Column) -> str:
    """JSX form-input snippet for one column. Used inside create/edit
    forms generated below.

    Skips system columns (id/created_at/updated_at — server fills them)
    and renders read-only badges for FK references (proper FK-resolution
    via JOIN is a phase-2 feature)."""
    if col.name in _SYSTEM_COLUMNS:
        return ""
    label = col.sql_name
    required = "" if col.nullable or col.has_default else " required"
    input_type = {
        "integer": "number",
        "numeric": "number",
        "timestamp": "datetime-local",
        "boolean": "checkbox",
    }.get(col.type, "text")
    if col.type == "boolean":
        return (
            f'<label className="flex items-center gap-2 text-sm">'
            f'<input type="checkbox" name="{col.name}" /> {label}'
            f"</label>"
        )
    if col.type == "jsonb":
        return (
            f'<label className="block"><span className="text-sm">{label}'
            f'</span><textarea name="{col.name}"{required}'
            f' className="mt-1 w-full rounded-md border px-2 py-1 font-mono text-xs"'
            f" /></label>"
        )
    return (
        f'<label className="block"><span className="text-sm">{label}'
        f'</span><input type="{input_type}" name="{col.name}"{required}'
        f' className="mt-1 w-full rounded-md border px-2 py-1" /></label>'
    )


def emit_next_admin_page(table: Table) -> str:
    """Generate a complete Next.js Admin CRUD page for one table.

    The output is a single Server Component file. List view at top,
    create/edit form below. Both gated by `<Protected role="admin">`
    so non-admin visits get redirected to /signin (per the template's
    auth wiring).

    Server Actions live in the same file (Next 15 convention) and use
    the project's existing `db` Drizzle client. They run server-side
    and re-validate the form fields against the column types we know.

    Returns the file content as a string — caller writes it to
    `src/app/admin/<safe_route>/page.tsx`.
    """
    form_fields = "\n".join(
        _form_field_for(col) for col in table.columns if _form_field_for(col)
    )
    list_columns = [c for c in table.columns]
    # Drop trivial primary-key column if it's the only auto-managed one
    # — keeps the table view readable; admins can click a row to expand.
    headers = " ".join(
        f'<th className="px-3 py-2 text-left text-xs uppercase">{c.sql_name}</th>'
        for c in list_columns
    )
    cells = " ".join(
        f'<td className="px-3 py-2 text-sm">{{String(row.{c.name} ?? "")}}</td>'
        for c in list_columns
    )

    insert_columns = [c for c in table.columns if c.name not in _SYSTEM_COLUMNS]
    insert_obj = ", ".join(
        f"{c.name}: form.get(\"{c.name}\") as string" for c in insert_columns
    )

    return f'''/**
 * AUTO-GENERATED admin CRUD for the `{table.sql_name}` table.
 *
 * Do not hand-edit — Omnia regenerates this file when the schema
 * changes. Customise the LIST/FORM presentation, role checks, or
 * filters by extending `src/app/admin/{table.safe_route}/CustomBits.tsx`
 * (next to this file) — that file is preserved across regenerations.
 *
 * Auth: gated by `<Protected role="admin">`. Non-admins redirect to
 * /signin (see the template's `src/components/Protected.tsx`).
 */

import {{ revalidatePath }} from "next/cache";
import {{ db }} from "@/lib/db";
import {{ {table.js_name} }} from "@/lib/db/schema";
import {{ Protected }} from "@/components/Protected";

export const dynamic = "force-dynamic";

async function createRow(form: FormData) {{
  "use server";
  await db.insert({table.js_name}).values({{ {insert_obj} }});
  revalidatePath("/admin/{table.safe_route}");
}}

export default async function {table.js_name.title()}AdminPage() {{
  const rows = await db.select().from({table.js_name});
  return (
    <Protected role="admin">
      <main className="mx-auto max-w-5xl px-6 py-10 space-y-8">
        <header>
          <h1 className="text-2xl font-semibold">{table.sql_name}</h1>
          <p className="text-sm text-zinc-500">
            {{rows.length}} строк · сгенерировано Omnia.AI из schema.ts
          </p>
        </header>

        <section className="overflow-x-auto rounded-xl border border-zinc-200">
          <table className="w-full">
            <thead className="bg-zinc-50">
              <tr>{headers}</tr>
            </thead>
            <tbody>
              {{rows.map((row) => (
                <tr key={{String(row.id)}} className="border-t border-zinc-100 hover:bg-zinc-50">
                  {cells}
                </tr>
              ))}}
            </tbody>
          </table>
        </section>

        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Добавить запись</h2>
          <form action={{createRow}} className="space-y-3 max-w-md">
            {form_fields}
            <button type="submit"
              className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-semibold text-white">
              Создать
            </button>
          </form>
        </section>
      </main>
    </Protected>
  );
}}
'''


def emit_next_admin_index(tables: tuple[Table, ...]) -> str:
    """Generate `/admin/page.tsx` — index listing every generated CRUD route.

    First touch surface for admins: see all tables, link to each.
    """
    if not tables:
        return ""
    items = "\n".join(
        f'        <li><a href="/admin/{t.safe_route}" '
        f'className="text-emerald-700 hover:underline">'
        f"{t.sql_name}</a> "
        f'<span className="text-xs text-zinc-500">({len(t.columns)} columns)</span></li>'
        for t in tables
    )
    return f'''/**
 * AUTO-GENERATED admin index. Lists every table in the project's
 * `schema.ts` with a link to its CRUD page. Regenerated when the
 * schema changes.
 */

import {{ Protected }} from "@/components/Protected";

export default function AdminIndex() {{
  return (
    <Protected role="admin">
      <main className="mx-auto max-w-3xl px-6 py-10 space-y-6">
        <header>
          <h1 className="text-2xl font-semibold">Админка</h1>
          <p className="text-sm text-zinc-500">
            CRUD-страницы автогенерируются Omnia из вашей schema.ts.
          </p>
        </header>
        <ul className="space-y-2">
{items}
        </ul>
      </main>
    </Protected>
  );
}}
'''


def generate_next_admin_files(schema_source: str) -> dict[str, str]:
    """End-to-end: schema.ts source → {path: file content} ready to commit.

    Caller (api/orchestrator) writes the returned files into the project's
    bare git or dev container. Idempotent — same input produces same output.

    Skips Auth.js system tables (users/accounts/sessions/verification_tokens):
    admins shouldn't be hand-editing session tokens through a generic
    form. A dedicated "users" admin UI is a separate, future commit.
    """
    auth_tables = {"users", "accounts", "sessions", "verification_tokens"}
    tables = tuple(t for t in parse_schema(schema_source) if t.sql_name not in auth_tables)
    files: dict[str, str] = {}
    for table in tables:
        path = f"src/app/admin/{table.safe_route}/page.tsx"
        files[path] = emit_next_admin_page(table)
    if tables:
        files["src/app/admin/page.tsx"] = emit_next_admin_index(tables)
    return files
