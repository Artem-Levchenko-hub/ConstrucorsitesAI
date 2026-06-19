"""Acceptance-lock for BS-19 (dogfood run #16, 2026-06-16): the entity engine
has NO referential integrity. A record may reference a parent that does not
exist, deleting a parent silently leaves every child pointing at a dead id, and
the list view resolves that dead reference to `null` with no error or warning —
so "whose record is this?" becomes permanently unanswerable.

LIVE-RUNTIME repro (throwaway omnia-dev-dogfood-refdel-probe-41de56, built from
the DEPLOYED base image omnia-template-nextjs-entities:dev; two entities injected
via docker cp — Client{name,phone} and Note{text, clientId:reference->Client};
owner-auth via Auth.js credentials — no LLM, no generation):

  CREATE Client "Ирина Петрова"                                   -> 200, id b966bf5a…
  CREATE Note  {text, clientId: b966bf5a…}                        -> 200 (resolves)
  CREATE Note  {text, clientId: "00000000-0000-…-000000000000"}   -> 201  ← bogus ref accepted
  DELETE Client b966bf5a…                                         -> 200  ← no restrict / no warn
  GET    Client list                                              -> []   (parent gone)
  GET    Note?expand=clientId  AFTER the delete:
     note 7e34e1d6 clientId=b966bf5a… (the DELETED client) _expanded.clientId=null  ← ORPHAN
     note db1826df clientId=00000000…  (never existed)      _expanded.clientId=null  ← ORPHAN

Three layers of zero referential integrity, all code-proven below:
  1. WRITE accepts a reference to a non-existent record. createRecord only
     zod-parses the body (a reference is just `z.string()`); it never loads the
     target entity to check the referenced row exists (engine.ts:217-226).
  2. DELETE is unconditional: deleteRecord removes the row by id and returns
     {deleted:true}; it never scans for children that reference it, never
     restricts, never cascades, never reports an affected-children count
     (engine.ts:295-315).
  3. EXPAND silently nulls a dead reference: expandRecords sets
     `_expanded[field] = map.get(refId) ?? null` — a deleted/never-existed parent
     is indistinguishable from "no relation set". The orphaned child renders the
     relation column as "—" with no signal that its parent was destroyed
     (engine.ts:143-148).

Class wider than CRM: any parent→child relation (order→customer, booking→patient,
task→project, comment→post, invoice→client). Deleting the parent quietly corrupts
every child that pointed at it.

Why this is a PROPOSAL, not a blind ship: referential integrity is a SEMANTIC
policy decision that changes write/delete behaviour for EVERY generated app, and
the right policy is genuinely ambiguous (RESTRICT = block delete while children
exist → 409; CASCADE = delete the children too → destructive; or SOFT/WARN =
return the affected-children count and require confirm → cross-zone apps/web +
CrudResource UI). All variants are multi-surface, base-rebuild + regen-verify,
and data-integrity sensitive. Min one fix per run, no blind template ship.
→ PROPOSAL P-REFINTEGRITY.

These are deterministic file-content asserts (money-free, no container, no LLM);
the dynamic behaviour above was proven once live and is fixed in the base engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"


def _fn_body(src: str, start_marker: str) -> str:
    """Return source from `start_marker` up to the next top-level `export`."""
    i = src.index(start_marker)
    nxt = src.find("\nexport ", i + len(start_marker))
    return src[i : nxt if nxt != -1 else len(src)]


def test_create_does_not_validate_reference_targets_today() -> None:
    """EVIDENCE (green today): the write path only zod-parses the body and
    inserts — it never loads the referenced entity to confirm the target row
    exists. A reference to a non-existent id is accepted (live: bogus clientId
    "00000000-…" → 201)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function createRecord(")
    assert ".insert(records)" in body
    assert ".values({ entity: def.name, data, createdBy: owner.id })" in body
    # No existence check: createRecord never loads the target entity or queries
    # for the referenced row before inserting.
    assert "loadEntity" not in body
    assert "referenceFields" not in body


def test_delete_is_unconditional_with_no_child_scan_today() -> None:
    """EVIDENCE (green today): deleteRecord deletes the row by id and returns
    {deleted:true}. It never scans other entities for children that reference
    this row, never restricts, never cascades, never reports a child count
    (live: DELETE Client with a referencing Note → 200, the Note survives)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function deleteRecord(")
    assert ".delete(records)" in body
    assert "deleted: true" in body
    # No referential awareness anywhere in the delete path.
    assert "referenceFields" not in body
    assert "loadEntity" not in body
    assert "cascade" not in body.lower()
    assert "restrict" not in body.lower()


def test_expand_silently_nulls_a_dead_reference_today() -> None:
    """EVIDENCE (green today): a dead reference (deleted or never-existed parent)
    is coerced to null in `_expanded` — indistinguishable from "no relation set".
    The orphaned child shows the relation as "—" with no signal its parent is
    gone (live: both orphaned notes → _expanded.clientId=null)."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert (
        "exp[field] = typeof refId === \"string\" ? map.get(refId) ?? null : null;"
        in src
    )


@pytest.mark.xfail(
    strict=False,
    reason="BS-19 / P-REFINTEGRITY not yet landed: deleting a record that other "
    "records reference silently orphans them (dead ref → null, no error). When "
    "the engine restricts the delete while children exist (or cascades, or "
    "returns an affected-children signal), flip this to XPASS.",
)
def test_deleting_a_referenced_record_should_not_silently_orphan_children() -> None:
    """DESIRED: deleting a referenced parent must not silently corrupt its
    children. The engine should be referentially aware on delete — either
    RESTRICT (refuse while children exist), CASCADE (delete the children too), or
    return the affected-children count for a confirm step. Until then deleteRecord
    removes the parent unconditionally and leaves every child pointing at a dead
    id."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function deleteRecord(")
    # The delete path must consult the relation graph in some form.
    is_referentially_aware = (
        "referenceFields" in body
        or "cascade" in body.lower()
        or "restrict" in body.lower()
        or "_children" in body.lower()
    )
    assert is_referentially_aware, (
        "deleteRecord is still unconditional — it deletes the parent without "
        "scanning for or signalling referencing children."
    )
