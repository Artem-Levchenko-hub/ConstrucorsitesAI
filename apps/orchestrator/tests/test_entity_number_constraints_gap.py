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


def test_field_def_has_numeric_constraint_keys() -> None:
    """FIXED (P-NUMRANGE landed): FieldDef now carries optional `min`/`max`/`step`
    members for `type: "number"`, so the schema CAN declare a value-range bound
    (e.g. `min: 0` for a price). The bound is opt-in per field — the model must
    declare it — but the vocabulary to say 'price ≥ 0' now exists."""
    block = _field_def_block()
    assert "type" in block and "required" in block
    # The numeric-constraint vocabulary the writer now uses to bound a field.
    assert "min?: number" in block
    assert "max?: number" in block
    assert "step?: number" in block


def test_zod_number_is_constrained_when_declared() -> None:
    """FIXED (P-NUMRANGE landed): zodForField(number) is no longer a bare
    `z.number()` — when the field declares `min`/`max` it threads them into the
    validator (`n.min(f.min)` / `n.max(f.max)`). This is the server-side
    backstop, so a declared `min: 0` now 400s a POST of quantity:-30 instead of
    persisting it. The guard is conditional on the declaration (opt-in), not
    automatic — there is no blanket `.nonnegative()`/`.int()` on every number."""
    arm = _zod_for_field_number_arm()
    assert "z.number()" in arm
    # Range guards are wired in, gated on the field's declared bound.
    assert "f.min !== undefined" in arm
    assert "f.max !== undefined" in arm
    assert ".min(f.min" in arm
    assert ".max(f.max" in arm


def test_number_widget_has_min_when_declared() -> None:
    """FIXED (P-NUMRANGE landed): the number form control is now
    `<Input type="number" min={f.min} max={f.max} step={f.step} ...>`, so when a
    field declares a bound the client guards it too (and the form's own validate()
    rejects `n < f.min` / `n > f.max` before submit). Both layers now close."""
    src = _FORM.read_text(encoding="utf-8")
    assert 'type="number"' in src
    # The number Input arm now wires min/max/step bounds from the field spec.
    m = re.search(r'f\.kind === "number" &&.*?<Input(.*?)/>', src, re.S)
    assert m, "number Input arm not found in entity-form.tsx"
    number_input = m.group(1)
    assert "min={f.min}" in number_input
    assert "max={f.max}" in number_input
    assert "step={f.step}" in number_input


def test_number_fields_should_be_constrainable() -> None:
    """DESIRED (now satisfied): a number field can declare a value-range
    constraint (a `min`/`max`), and the server validator enforces it — so a
    generated inventory/booking/age field can reject an out-of-domain value
    instead of persisting it as valid data. The mechanism is declared-not-
    automatic: the model opts a field in with `min`, and the engine threads that
    into z.number(); there is no implicit nonnegativity on every number."""
    field_def = _field_def_block()
    number_arm = _zod_for_field_number_arm()

    model_has_constraint = "min" in field_def or "max" in field_def
    validator_enforces = ".min(f.min" in number_arm or ".max(f.max" in number_arm

    assert model_has_constraint and validator_enforces


@pytest.mark.xfail(
    strict=False,
    reason="PARTIAL: the value-range constraint (min/max) landed and is enforced "
    "server-side, but there is still no INTEGER constraint — the engine has no "
    "`integer` FieldDef member and no `.int()` in zodForField(number). So a stock "
    "field declared `min: 0` still accepts 2.5; a whole-count can't be enforced as "
    "an integer. Flip to XPASS when FieldDef gains `integer`/`.int()` threading.",
)
def test_number_integer_constraint_should_be_enforceable() -> None:
    """DESIRED (genuinely remaining gap): a number field should also be able to
    declare that it is a whole number (e.g. a stock count, an age), so the server
    rejects 2.5 — not just out-of-range. This part of P-NUMRANGE has NOT landed:
    no `integer` member on FieldDef, no `.int()` in the number validator."""
    field_def = _field_def_block()
    number_arm = _zod_for_field_number_arm()

    model_has_integer = "integer" in field_def
    validator_has_int = ".int(" in number_arm

    assert model_has_integer and validator_has_int
