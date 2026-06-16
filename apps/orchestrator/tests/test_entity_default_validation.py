"""Acceptance-lock for BS-36 (dogfood run #36, 2026-06-17): the entity engine
applied a writer-declared `default` value WITHOUT ever validating it against the
field's own type/enum.

THE BLIND SPOT (same family as BS-35 — "the engine mishandles a primitive it
HAS"): createRecord (engine.ts:223-227) runs `createSchema(def).safeParse(body)`
FIRST, then `applyDefaults(def, parsed.data)` SECOND. createSchema makes a
field-with-a-default OPTIONAL in the payload (BS-35 facet-1 fix), so an OMITTED
field is never seen by validation — and applyDefaults then injected `f.default`
unchecked. A default that violates its own field's constraints was stored
silently on every omitted-field create:

  - an enum `default` NOT in `options`  → e.g. status:"pending" with
    options ["new","paid","shipped"] — the EXACT value the engine 400s when it is
    PROVIDED (createSchema's z.enum rejects it), yet it writes it itself.
  - a non-numeric `default` in a `number` field → e.g. qty:"none". The only
    vector to plant a non-numeric into a number column — and a numeric SORT casts
    `(data->>field)::numeric` with NO safe cast (engine.ts:74, unlike date BS-22),
    so one such row 500s the whole list the moment anyone sorts by it.
  - a string `default` in a `boolean` field → inStock:"yes".

LIVE PROOF (run #36 — ran the DEPLOYED image's OWN registry.ts via
`node --experimental-strip-types`, importing the real createSchema/applyDefaults
and replaying engine.createRecord's parse-then-default order; no LLM, no gen):
  image omnia-template-nextjs-entities:dev (869d31ffd20e)
    A enum default 'pending' NOT in options, OMITTED → 201 status:"pending"  ← BUG
    B enum default 'new' IN options, OMITTED            → 201 status:"new"    ← control
    C number default "none", qty OMITTED                → 201 qty:"none"      ← BUG
    D number default 0, qty OMITTED                     → 201 qty:0           ← control
    E boolean default "yes", inStock OMITTED            → 201 inStock:"yes"   ← BUG
    F enum 'pending' PROVIDED                           → 400 Invalid enum    ← control (engine 400s the value it silently writes)
  Evidence: _routine/runs/2026-06-16T22-13Z/probe_output_before.txt + probe_harness.mts.

THE FIX (shipped — maximally safe / purely permissive): run the default through
the field's OWN validator before injecting it; inject only on success.
    if (zodForField(f).safeParse(f.default).success) out[key] = f.default;
A VALID default is injected unchanged (controls B/D keep working); a malformed
default is dropped — the field stays omitted instead of poisoning the row. This
can only turn a silently-stored-invalid into a not-stored; it can never turn a
working create into a failure, and never weakens validation of a PROVIDED value
(control F still 400s). Verified on the REBUILT base image (no bind-mount):
A/C/E flip to 201 WITHOUT the bad field; B/D/F unchanged.

Distinct from the rest of the constraint family: BS-33/P-NUMRANGE = no numeric
BOUND vocabulary; BS-27/P-UNIQUE = no uniqueness; BS-22/P-DATECAST = a date sort
crash; BS-19 = no referential integrity; BS-35 = required+default deadlock +
required-string-emptiness. BS-36 is the engine trusting its OWN default past the
validation every provided value must pass.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

from pathlib import Path

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"


def _registry() -> str:
    return _REGISTRY.read_text(encoding="utf-8")


def _engine() -> str:
    return _ENGINE.read_text(encoding="utf-8")


def test_create_applies_defaults_after_validation() -> None:
    """The order that makes the bypass possible: an omitted field is never seen by
    createSchema, so applyDefaults runs AFTER validation — hence it must validate
    the default itself."""
    src = _engine()
    parse_at = src.index("createSchema(def).safeParse(opts.body)")
    default_at = src.index("applyDefaults(def, parsed.data")
    assert parse_at < default_at


def test_apply_defaults_validates_the_default_before_injecting() -> None:
    """FIX: a default is injected only if it passes the field's own validator."""
    src = _registry()
    assert "zodForField(f).safeParse(f.default).success" in src, (
        "applyDefaults must validate f.default against zodForField(f) before "
        "injecting it; otherwise a bad default (enum-not-in-options, wrong type) "
        "is stored silently on every omitted-field create (BS-36)."
    )


def test_apply_defaults_still_only_fills_omitted_fields() -> None:
    """The fix must not start overwriting provided values — it still only fills a
    field that was omitted (undefined) and that declares a default."""
    src = _registry()
    assert "out[key] !== undefined || f.default === undefined" in src, (
        "applyDefaults must skip a field that already has a value or has no default."
    )


def test_valid_default_is_injected_unchanged() -> None:
    """Regression guard: a VALID default is still assigned (controls B/D). The
    injection line must remain `out[key] = f.default`, gated on the validator."""
    src = _registry()
    assert "out[key] = f.default;" in src


def test_old_unconditional_default_injection_is_gone() -> None:
    """The pre-fix one-liner injected the default with zero validation."""
    src = _registry()
    assert (
        "if (out[key] === undefined && f.default !== undefined) out[key] = f.default;"
        not in src
    ), "the unconditional inject (no validation of the default) must be replaced."


def test_number_sort_uses_unguarded_numeric_cast() -> None:
    """Context for severity: a number sort casts `::numeric` with NO safe wrapper
    (unlike the date path's safe_to_timestamptz), so a non-numeric value in a
    number column 500s the whole list. The default was the only vector to plant
    one — which is why dropping an invalid number default matters."""
    src = _engine()
    assert "(${records.data} ->> ${field})::numeric" in src
    # The date path is crash-proofed; the number path is not — so keeping
    # non-numeric values out of number columns is the engine's only defense.
    assert "safe_to_timestamptz" in src
