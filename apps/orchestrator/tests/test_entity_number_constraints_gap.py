"""Acceptance-lock for BS-33 (dogfood run #32, 2026-06-17): the entity field
system has NO numeric constraint vocabulary. A `number` field accepts ANY IEEE
double — negative, fractional, extreme — and there is no way for the writer to
declare "this number must be ≥ 0" or "this is a nonnegative integer". So an
inventory app silently accepts a price of -50000 ₽ and a stock quantity of -30
(or 2.5), the very data the app exists to keep correct.

LIVE PROOF (real product generation, not a hand-built fixture):
  gen = dogfood-sklad-uchet-a086b4  (prompt: "Складской учёт … товары — название,
        артикул, цена в рублях, количество на складе … цена и количество
        числовые", skip_clarify=true → BS-4 escalation fired → real
        nextjs_entities app, Product entity).
  The writer correctly marked price & quantity as required `number` fields. Then,
  authed as the owner (Auth.js v5), a POST to /api/entities/Product with
        {price: -50000, quantity: -30}     → 201 Created, persisted
        {price: 1999.999, quantity: 2.5}   → 201 Created, persisted
  and GET ?sort=quantity&order=asc returns the -30 row at the TOP — the server
  treats impossible stock as ordinary valid data. Evidence + raw writer output:
  _routine/runs/2026-06-16T21-00Z/EVIDENCE.md + 03_writer_raw.html.

Downstream the headline dashboard KPI corrupts: dashboard/page.tsx computes
`totalValue += price*quantity`, so a single (-50000)×(-30) entry INFLATES
«Стоимость склада» by +1 500 000 ₽; criticalCount counts -30 as low stock; the
list renders -30 with a «Мало» badge, never an error.

Root cause (code-proven, two template surfaces + the writer prompt):
  1. registry.ts  FieldDef = { type, required?, default?, options?, entity? } —
     there is NO `min`/`max`/`integer`/`nonnegative` member, so the data model
     cannot declare a numeric bound. The writer is mute on the constraint even
     when it obviously applies (price, stock count).
  2. registry.ts  zodForField(number) returns a bare `z.number()` — no
     `.min(0)`, no `.int()`. The server-side validator (createSchema → safeParse
     in engine.createRecord) is the system's integrity backstop, and it waves
     every double through. This is what makes the -30 land as a clean 201.
  3. entity-form.tsx  the `kind === "number"` widget is `<Input type="number">`
     with no `min`/`step`, so the client doesn't guard either.

Distinct from the rest of the data-integrity ledger: BS-22/P-DATECAST is a
JS-valid-but-PG-invalid date STRING crashing a sort (a cast robustness bug);
BS-27/P-UNIQUE is the absence of a uniqueness constraint (a duplicate slips in);
this is the absence of a *value-range* constraint (an out-of-domain number slips
in). Same recurring family as BS-15/BS-17 — "the form promises something it
can't enforce": here the field is typed `number` but any nonsense number sticks.

Why this is a PROPOSAL (P-NUMRANGE), not a blind ship: the fix is multi-surface
and TEMPLATE-level (base-image rebuild on prod) — extend FieldDef with optional
`min`/`max`/`integer` (registry), thread them into zodForField
(`z.number().min(..).int()`) AND the `<Input>` (min/step), AND — the
quality-sensitive part — steer the writer prompt to emit `min: 0` / `integer`
for the fields where it belongs (price, stock, age, count) WITHOUT over-emitting
it where negatives/fractions are legitimate (account balance, delta, temperature,
coordinate, rating). It then needs regen-verify across niches. One fix per run;
no blind template ship.

Deterministic file-content asserts (money-free, no container, no LLM)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_FORM = _ENTITIES / "src" / "components" / "omnia" / "entity-form.tsx"


def _field_def_block() -> str:
    """The body of `export interface FieldDef { … }` in registry.ts."""
    src = _REGISTRY.read_text(encoding="utf-8")
    m = re.search(r"export interface FieldDef\s*\{(.*?)\}", src, re.S)
    assert m, "FieldDef interface not found in registry.ts"
    return m.group(1)


def _zod_for_field_number_arm() -> str:
    """The `case "number":` arm of zodForField in registry.ts."""
    src = _REGISTRY.read_text(encoding="utf-8")
    m = re.search(r'case "number":\s*(.*?)\n\s*case ', src, re.S)
    assert m, "number arm of zodForField not found in registry.ts"
    return m.group(1)


def test_field_def_has_no_numeric_constraint_keys_today() -> None:
    """EVIDENCE (green today): FieldDef carries type/required/default/options/
    entity but NO min/max/integer/nonnegative — the schema cannot declare a
    numeric bound, so the writer can't say 'price ≥ 0' even when it must."""
    block = _field_def_block()
    assert "type" in block and "required" in block
    assert "min" not in block
    assert "max" not in block
    assert "integer" not in block
    assert "nonnegative" not in block


def test_zod_number_is_unconstrained_today() -> None:
    """EVIDENCE (green today): zodForField(number) is a bare `z.number()` — no
    `.min(...)`, no `.int()`. This is the server-side backstop, and it accepts
    every double, which is why a POST of quantity:-30 returns a clean 201."""
    arm = _zod_for_field_number_arm()
    assert "z.number()" in arm
    assert ".min(" not in arm
    assert ".int(" not in arm
    assert ".nonnegative(" not in arm


def test_number_widget_has_no_min_today() -> None:
    """EVIDENCE (green today): the number form control is `<Input type="number">`
    with no `min`/`step`, so the client doesn't guard against negatives or
    fractions either — both layers are open."""
    src = _FORM.read_text(encoding="utf-8")
    assert 'type="number"' in src
    # The number Input arm renders without a min/step bound today.
    m = re.search(r'f\.kind === "number" &&.*?<Input(.*?)/>', src, re.S)
    assert m, "number Input arm not found in entity-form.tsx"
    number_input = m.group(1)
    assert "min=" not in number_input
    assert "step=" not in number_input


@pytest.mark.xfail(
    strict=False,
    reason="BS-33 / P-NUMRANGE not yet landed: a number field has no way to "
    "declare a value-range constraint, so an inventory app accepts negative "
    "price/stock. When FieldDef carries an optional numeric bound AND "
    "zodForField threads it into z.number() (e.g. .min/.int), flip to XPASS.",
)
def test_number_fields_should_be_constrainable() -> None:
    """DESIRED: a number field must be able to declare a value-range constraint
    (at minimum a `min`, ideally `integer`), and the server validator must
    enforce it — so a generated inventory/booking/age field can reject an
    out-of-domain value instead of persisting it as valid data."""
    field_def = _field_def_block()
    number_arm = _zod_for_field_number_arm()

    model_has_constraint = (
        "min" in field_def or "integer" in field_def or "nonnegative" in field_def
    )
    validator_enforces = (
        ".min(" in number_arm or ".int(" in number_arm or ".nonnegative(" in number_arm
    )

    assert model_has_constraint and validator_enforces
