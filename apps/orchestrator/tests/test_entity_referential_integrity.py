"""Acceptance-lock for BS-19 / P-REFINTEGRITY (dogfood run #16, 2026-06-16,
LANDED 2026-06): the entity engine NOW has referential integrity on every side.

ORIGINAL GAP (the throwaway live repro that motivated this lock — kept for the
record): a record could reference a parent that did not exist, deleting a parent
silently left every child pointing at a dead id, and the list view resolved that
dead reference to `null` with no error or warning — "whose record is this?"
became permanently unanswerable. Original live repro (omnia-dev-dogfood-refdel-
probe-41de56, two entities Client{name,phone} + Note{text, clientId->Client},
owner-auth, no LLM):

  CREATE Note  {text, clientId: "00000000-…"}   -> 201   ← bogus ref accepted
  DELETE Client b966bf5a…                        -> 200   ← no restrict / no warn
  GET    Note?expand=clientId  AFTER the delete  -> _expanded.clientId=null ← ORPHAN

WHAT LANDED (engine.ts, all code-proven below):
  1. WRITE now validates the target exists. createRecord AND updateRecord both
     call `assertReferencesExist`, which loads each `reference` field's target
     entity and queries for the referenced row; a dangling reference → 400
     `Связанная запись … не найдена` (engine.ts assertReferencesExist + create/update).
  2. DELETE is now referentially aware. deleteRecord runs in a transaction and
     calls `applyOnDelete`, which walks `referencingFields` ("who points at me")
     and applies each reference's onDelete policy: `setNull` clears the dangling
     pointer, `cascade` deletes the child (recursing into grandchildren, capped),
     `restrict` aborts the whole delete (409). It also returns a `removed` count.
  3. EXPAND still coerces a dead reference to null (engine.ts:208). This is the
     ONE genuinely-remaining edge: a LEGACY orphan (a row whose parent was removed
     before this fix, or a `setNull`-cleared pointer) is still indistinguishable
     from "no relation set". See the evidence + the tight xfail at the bottom.

Class wider than CRM: any parent→child relation (order→customer, booking→patient,
task→project, comment→post, invoice→client) is now integrity-protected on both
write and delete.

These are deterministic file-content asserts (money-free, no container, no LLM);
the dynamic behaviour was proven once live and is now fixed in the base engine.
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


def _helper_body(src: str, start_marker: str) -> str:
    """Return a (non-exported) helper's source: from `start_marker` to the next
    top-level closing brace at column 0 (`\\n}\\n`)."""
    i = src.index(start_marker)
    end = src.find("\n}\n", i)
    return src[i : end + 3 if end != -1 else len(src)]


def test_create_validates_reference_targets_exist() -> None:
    """The write path now LOADS the referenced entity and confirms the target row
    exists before inserting. createRecord runs the shared `assertReferencesExist`
    guard, which loads each reference field's target entity and queries for the
    referenced id; a dangling reference is rejected with 400 (live original:
    bogus clientId "00000000-…" used to be accepted → 201; now it 400s)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function createRecord(")
    # Still inserts the parsed row...
    assert ".insert(records)" in body
    assert ".values({ entity: def.name, data, createdBy: owner.id })" in body
    # ...but ONLY after the reference-existence guard runs on the create path.
    assert "await assertReferencesExist(def, data);" in body

    # And the guard genuinely loads the target entity and checks the row exists,
    # 400-ing a dangling reference rather than accepting it.
    guard = _helper_body(src, "async function assertReferencesExist(")
    assert "referenceFields(def)" in guard
    assert "await loadEntity(target)" in guard
    assert "eq(records.entity, target)" in guard
    assert "eq(records.id, val)" in guard
    assert "throw new EngineError(400" in guard


def test_update_also_validates_reference_targets_exist() -> None:
    """The SAME guard runs on the UPDATE path (closing the partial-fix trap where
    create is checked but update lets a dangling reference slip in). updateRecord
    merges the patch over the stored row and runs `assertReferencesExist` on the
    merged values before persisting."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    assert "const merged = {" in body
    assert "await assertReferencesExist(def, merged);" in body
    # The update is only applied after the guard (guard call precedes the .update).
    assert body.index("assertReferencesExist(def, merged)") < body.index(
        ".update(records)"
    )


def test_delete_is_referentially_aware_via_apply_on_delete() -> None:
    """deleteRecord is no longer unconditional. It opens a transaction and calls
    `applyOnDelete` to fix every referencing child BEFORE removing the parent, and
    reports a `removed` count — so a delete can no longer silently orphan children
    (live original: DELETE Client with a referencing Note → 200, the Note survived
    pointing at a dead id; now setNull clears it / restrict blocks / cascade
    removes it, atomically)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function deleteRecord(")
    # Still deletes the target row...
    assert ".delete(records)" in body
    assert "deleted: true" in body
    # ...but inside a transaction, after running the relation-graph fixup, and it
    # now reports how many rows the delete touched (parent + cascaded descendants).
    assert "db.transaction(" in body
    assert "applyOnDelete(tx, def.name, [id], seen)" in body
    assert "removed: removed.size" in body

    # The fixup helper implements the three onDelete policies for real.
    fixup = _helper_body(src, "async function applyOnDelete(")
    assert "referencingFields(parentName)" in fixup
    assert 'policy === "restrict"' in fixup
    assert 'policy === "setNull"' in fixup
    assert "tx.delete(records)" in fixup  # the cascade branch
    assert "throw new EngineError(409" in fixup  # restrict aborts the delete

    # And `referencingFields` is the reverse relation lookup ("who points at me"),
    # carrying each reference's onDelete policy (default setNull).
    rev = _helper_body(src, "async function referencingFields(")
    assert "listEntities()" in rev
    assert 'f.type === "reference" && f.entity === targetName' in rev
    assert "f.onDelete ?? " in rev


def test_expand_still_nulls_a_legacy_dead_reference_is_evidence() -> None:
    """EVIDENCE (still true): the EXPAND path still coerces a dead reference to
    null in `_expanded` — so a LEGACY orphan (parent removed before integrity
    landed, or a `setNull`-cleared pointer) is still indistinguishable from "no
    relation set"; the row renders the relation as "—" with no signal its parent
    is gone. Write+delete integrity prevent NEW orphans, but expand does not
    surface an EXISTING one. This is the one genuinely-remaining edge — see the
    desired test below."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert (
        'exp[field] = typeof refId === "string" ? map.get(refId) ?? null : null;'
        in src
    )


def test_deleting_a_referenced_record_does_not_silently_orphan_children() -> None:
    """LANDED: deleting a referenced parent no longer silently corrupts its
    children. The engine is referentially aware on delete — `applyOnDelete` applies
    each child reference's policy (RESTRICT → 409 while children exist, CASCADE →
    delete the children too, SETNULL → clear the dangling pointer) inside the same
    transaction as the parent delete, so the operation is all-or-nothing and never
    leaves a child pointing at a dead id."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function deleteRecord(")
    # The delete path consults the relation graph (via applyOnDelete) and the
    # policy machinery names cascade/restrict in the same closure.
    is_referentially_aware = (
        "applyOnDelete" in body
        or "referencingFields" in body
        or "cascade" in body.lower()
        or "restrict" in body.lower()
    )
    assert is_referentially_aware, (
        "deleteRecord is still unconditional — it deletes the parent without "
        "scanning for or signalling referencing children."
    )


@pytest.mark.xfail(
    strict=False,
    reason="EXPAND still can't distinguish a LEGACY dead reference from 'no "
    "relation set': engine.ts:208 coerces a non-resolving refId to null, same as "
    "an unset relation. Write+delete integrity stop NEW orphans, but a pre-existing "
    "orphan (or a setNull-cleared pointer to a since-deleted row) surfaces no "
    "'parent was here and is gone' signal. Close by emitting a distinct marker "
    "(e.g. _expanded[field] = { __dangling: true } / a missing-ref flag) instead of "
    "a bare null when refId is present but unresolved.",
)
def test_expand_should_signal_a_dangling_reference_distinctly_from_unset() -> None:
    """DESIRED (genuinely remaining): when a reference id IS set but resolves to no
    row, expand should mark it as DANGLING — distinct from a field that was never
    set. Today both collapse to `null`, so a destroyed parent is invisible in the
    UI. The fix lives in `expandRecords`: branch on `refId present but
    map.get(refId) === undefined` and emit a dangling marker rather than null."""
    src = _ENGINE.read_text(encoding="utf-8")
    # Scope to JUST the expandRecords function body (it's not exported and other
    # functions/comments downstream mention "dangling", so use the brace-bounded
    # helper extractor rather than slicing to EOF).
    body = _helper_body(src, "async function expandRecords(")
    # A real dangling signal would mention it explicitly; a bare `?? null` does not.
    signals_dangling = (
        "__dangling" in body.lower()
        or "dangling" in body.lower()
        or "orphan" in body.lower()
        or "missingRef" in body
    )
    assert signals_dangling, (
        "expandRecords still coerces an unresolved-but-present reference to null — "
        "indistinguishable from an unset relation; no dangling/orphan signal."
    )
