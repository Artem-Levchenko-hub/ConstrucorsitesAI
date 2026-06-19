"""Acceptance-lock for BS-17 (dogfood run #14, 2026-06-16): in a generated
entity app you can CHANGE a field on edit, but you can NEVER CLEAR an optional
field — clearing silently no-ops while the UI toasts "Изменения сохранены".

LIVE RUNTIME PROOF (deployed binary, not just source — no LLM/generation, the
bug lives in the base engine that every entity app bakes in):
  app  = omnia-dev-dogfood-barber-crm-25cc9e   (starter Task entity:
         notes/due/priority are optional)
  auth = register + credentials login over public HTTPS (AuthJS sets the
         __Secure session cookie only over TLS)

  CREATE Task  notes="перезвонить клиенту в 18:00"  due="2026-07-01"   -> 200
  PUT    {title, priority}   (the EXACT payload the edit form sends when the
         user clears notes+due — the form omits empty optional fields)   -> 200
         updated_at advanced 15:05:51 -> 15:05:55, so the update DID run
  GET    after the clear-attempt:
         notes == "перезвонить клиенту в 18:00"   (SURVIVED)
         due   == "2026-07-01"                    (SURVIVED)     -> CLEAR_BUG
  CONTROL PUT {title:"Стрижка Пётр", notes:"новая заметка", priority:"low"}
         -> applies fine.  Editing to a NEW non-empty value works; only
         UN-SETTING an optional field is the silent no-op.

Root cause (code-proven, three surfaces):
  1. entity-form.tsx:158-164  validate(): an empty non-required field hits
     `continue` BEFORE `out[f.name] = …`, so a cleared field is dropped from
     the payload entirely — the form never tells the server "make this empty".
  2. registry.ts:176-182      updateSchema(): every field is
     `zodForField(f).optional()` — NOT `.nullable()`. So even if the form sent
     `notes: null` to force a clear, zod would reject it and 400 the whole
     update. A clear is INEXPRESSIBLE in the wire contract.
  3. engine.ts:286            merge = {...existing, ...parsed.data} — a field
     absent from the payload keeps its old value.

So omit-on-empty (1) + merge-keeps-absent (3) = the clear vanishes, and even a
deliberate null (a naive form fix alone) would be rejected by (2).

Class wider than the barber CRM: every entity app, every optional field, the
clear/un-set operation (remove a phone, blank a note, drop a due date, unset a
status). Fits the recurring "false confirmation" family — BS-11 (orphaned
anchors shipped as if fixed), BS-15 (lead form that toasts success and drops
the lead).

Why this is a PROPOSAL, not a blind ship (P-CLEAR): the correct fix is
multi-surface and changes write-validation SEMANTICS for every app —
updateSchema must accept null for optional fields (`.nullable().optional()`),
the form must emit an explicit clear in EDIT mode (diff against `initial`,
sending null for fields that had a value and are now empty), and number/date/
reference fields each need their own null handling. It is a TEMPLATE change
(base-image rebuild on prod) and needs regen-verify across niches. One fix per
run, no blind template ship.

These are deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_FORM = _ENTITIES / "src" / "components" / "omnia" / "entity-form.tsx"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"


def _update_schema_body() -> str:
    """The body of updateSchema(def) { … } in registry.ts."""
    src = _REGISTRY.read_text(encoding="utf-8")
    m = re.search(
        r"export function updateSchema\(def: EntityDef\) \{(.*?)\n\}", src, re.S
    )
    assert m, "updateSchema(def) not found in registry.ts"
    return m.group(1)


def test_form_omits_empty_optional_fields_today() -> None:
    """EVIDENCE (green today): the edit form drops an empty optional field from
    the payload — it hits `continue` before the `out[f.name] = …` assignment, so
    a cleared field is never sent to the server at all."""
    src = _FORM.read_text(encoding="utf-8")
    assert 'const empty = raw === "" || raw == null;' in src
    empty_at = src.index('const empty = raw === "" || raw == null;')
    assign_at = src.index('out[f.name] = f.kind === "number" ? Number(raw) : raw;')
    # The empty branch ends in `continue;`, BEFORE the payload assignment — so a
    # cleared (empty) field skips `out[f.name] = …` and is never sent.
    assert empty_at < assign_at
    continue_at = src.index("continue;", empty_at)
    assert empty_at < continue_at < assign_at


def test_update_schema_makes_a_clear_inexpressible_today() -> None:
    """EVIDENCE (green today): updateSchema marks every field merely `.optional()`
    — never `.nullable()`/`.nullish()`. So a clear cannot even be expressed on
    the wire: sending `null` to blank a field would fail zod and 400 the update.
    The only payload the form CAN send (omit the field) is the one the merge
    ignores."""
    body = _update_schema_body()
    assert "zodForField(f).optional();" in body
    assert "nullable" not in body
    assert "nullish" not in body


def test_engine_update_is_a_shallow_merge_that_keeps_absent_fields_today() -> None:
    """EVIDENCE (green today): updateRecord shallow-merges the payload over the
    stored row, so any field absent from the payload keeps its previous value —
    exactly the field the form just dropped."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert (
        "const merged = { ...(existing[0].data as Record<string, unknown>), ...parsed.data };"
        in src
    )


@pytest.mark.xfail(
    strict=False,
    reason="BS-17 / P-CLEAR not yet landed: an edit cannot clear an optional "
    "field — the form omits it, updateSchema can't accept a null to force the "
    "clear, and the engine merge keeps the old value. When updateSchema accepts "
    "null for optional fields AND the form emits an explicit clear in edit mode, "
    "flip this to XPASS.",
)
def test_clearing_an_optional_field_should_persist_the_clear() -> None:
    """DESIRED: clearing an optional field on edit must stick. Two surfaces have
    to change together — the wire contract must ALLOW a clear (updateSchema
    accepts null for optional fields) AND the form must SEND one in edit mode
    (an emptied field that had an initial value is sent as null, not omitted)."""
    update_body = _update_schema_body()
    form = _FORM.read_text(encoding="utf-8")

    # Wire contract allows a clear: optional fields accept null.
    schema_allows_clear = "nullable" in update_body or "nullish" in update_body

    # Form sends an explicit clear in edit mode: an emptied field that was set in
    # `initial` is sent as null rather than silently omitted.
    form_emits_clear = bool(
        re.search(r"initial", form)
        and re.search(r"empty", form)
        and re.search(r"=\s*null\b|:\s*null\b", form)
    )

    assert schema_allows_clear and form_emits_clear
