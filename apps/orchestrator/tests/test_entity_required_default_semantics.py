"""Acceptance-lock for BS-35 (dogfood run #35, 2026-06-17): the entity engine's
`required` / `default` semantics are weaker than they look — two facets:

  FACET 1 (FIXED this run — the required+default DEADLOCK):
    A field declared BOTH `"required": true` AND `"default": <x>` was
    UNSATISFIABLE on omit. createRecord (engine.ts:223-227) runs
    `createSchema(def).safeParse(body)` FIRST and `applyDefaults(def, parsed.data)`
    SECOND. createSchema made every required field non-optional in the payload, so
    omitting a required+default field failed zod with "Required" and 400'd —
    BEFORE applyDefaults could ever fill the default. The default was DEAD: the
    one thing it exists for (covering an omission) is the one thing that 400s.
    A natural writer schema like
        "status": {"type":"enum","options":["new","paid","shipped"],
                   "required": true, "default": "new"}
    means "every order must have a status; if the form omits it, use 'new'". The
    engine instead rejected the quick-create that relied on the default.

  FACET 2 (NOT fixed — proposal, semantics-changing): a `required` string/text is
    presence-only, not non-empty. `zodForField` returns a bare `z.string()`, so a
    POST with name:"" or name:"   " (whitespace) passes and a blank-looking row is
    stored. Tightening to `.trim().min(1)` turns previously-201 writes into 400s
    across every generated app — a write-validation semantics change (cf. BS-33
    P-NUMRANGE, BS-22 P-DATECAST write half) → not shipped blind.

LIVE PROOF (run #35 — ran the DEPLOYED image's OWN registry.ts via
`node --experimental-strip-types`, importing the real createSchema/applyDefaults
and replaying engine.createRecord's parse-then-default order; no LLM, no gen):
  image omnia-template-nextjs-entities:dev (03694596fbc7)
    A  required+default field, status OMITTED          → 400 "status: Required"   ← THE BUG
    B  same field but required:false (default only), omitted → 201, status:"new"  ← control: default works only when NOT required
    C  required string, name:""                         → 201, name:""            ← facet 2
    D  required string, name:"   "                      → 201, name:"   "          ← facet 2
    E  required+default field, status PROVIDED          → 201, status:"paid"       ← control
  Evidence: _routine/runs/2026-06-17T00-53Z/probe_output.txt + probe_harness.mts.

Distinct from the data-integrity ledger: BS-33/P-NUMRANGE is no numeric BOUND
vocabulary; BS-27/P-UNIQUE is no uniqueness; BS-22/P-DATECAST is a date cast crash;
BS-19 is no referential integrity. BS-35 is the engine mishandling the two
constraint primitives it DOES have (`required`, `default`) — the default is dead
on the very omission it covers, and "required" tolerates empty.

THE FIX (facet 1, shipped — maximally safe / purely permissive): a field that
carries a `default` is always satisfiable (applyDefaults fills it), so it is
required IN THE PAYLOAD only when it has NO default to fall back on:
    const requiredInPayload = f.required && f.default === undefined;
    shape[key] = requiredInPayload ? base : base.optional();
This only turns previously-400 omissions into 201-with-default-applied; it cannot
400 anything that used to 201, so no generated app can regress. A provided invalid
value is still rejected (zod `.optional()` validates when present). required
WITHOUT a default is still hard-required. Verified by rebuilding the base image and
re-running the probe: CASE A flips 400 → 201 with status:"new".

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"


def _registry() -> str:
    return _REGISTRY.read_text(encoding="utf-8")


def _engine() -> str:
    return _ENGINE.read_text(encoding="utf-8")


def test_create_record_applies_defaults_after_validation() -> None:
    """The order that makes facet 1 possible: parse first, defaults second."""
    src = _engine()
    assert "createSchema(def).safeParse(opts.body)" in src
    parse_at = src.index("createSchema(def).safeParse(opts.body)")
    default_at = src.index("applyDefaults(def, parsed.data")
    assert parse_at < default_at, (
        "applyDefaults must run after safeParse (it does) — which is exactly why a "
        "hard-required field can never be rescued by its default; the fix lives in "
        "createSchema, not by reordering this."
    )


def test_apply_defaults_only_fills_omitted_fields() -> None:
    """applyDefaults fills a default ONLY when the value is undefined (omitted)."""
    src = _registry()
    assert "if (out[key] === undefined && f.default !== undefined) out[key] = f.default;" in src


def test_required_with_default_is_optional_in_create_payload() -> None:
    """FIX (facet 1): a field with a `default` is not hard-required in the payload,
    so omitting it no longer 400s before applyDefaults can fill the default."""
    src = _registry()
    assert "f.required && f.default === undefined" in src, (
        "createSchema must treat a required field as required-in-payload only when "
        "it has NO default; otherwise the default is dead on omission (BS-35)."
    )
    # The old unconditional form must be gone.
    assert "shape[key] = f.required ? base : base.optional();" not in src, (
        "the unconditional `f.required ? ...` made required+default unsatisfiable."
    )


def test_required_without_default_stays_hard_required() -> None:
    """The fix must NOT relax fields that have no default — those stay required."""
    src = _registry()
    # createSchema still keys off f.required; a default-less required field has
    # requiredInPayload === true and keeps the non-optional base schema.
    assert "requiredInPayload ? base : base.optional()" in src


def test_reference_field_validator_is_presence_only_today() -> None:
    """Context for the family: a reference is `z.string().min(1)` — non-empty
    string, no existence check (BS-19). Documents why 'required' is shallow."""
    src = _registry()
    assert 'case "reference":' in src
    assert "z.string().min(1)" in src


@pytest.mark.xfail(
    reason="BS-35 facet 2 (proposal): a required string/text accepts ''/whitespace. "
    "Tightening zodForField(string|text) to reject empty/blank for required fields "
    "is a write-validation semantics change across every generated app (cf. BS-33 "
    "P-NUMRANGE write half) → not shipped blind. XPASS when a non-empty guard ships.",
    strict=True,
)
def test_required_string_should_reject_empty_and_whitespace() -> None:
    src = _registry()
    # A shipped facet-2 fix would make required string/text non-empty after trim,
    # e.g. a `.trim().min(1)` (or equivalent) applied for required string fields.
    assert "trim().min(1)" in src or ".min(1, " in src, (
        "no non-empty guard for required string/text yet — '' and '   ' still 201."
    )
