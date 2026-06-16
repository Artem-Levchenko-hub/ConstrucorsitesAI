"""Acceptance-lock for BS-25 (dogfood run #22, 2026-06-16): the entity engine
has NO optimistic-concurrency control. Two people editing the SAME record (the
solo owner on phone + laptop, two browser tabs, a double-submit) silently
clobber each other — last write wins, no version check, no 409, no warning. The
edit that lost has no signal it was lost.

LIVE-RUNTIME repro (throwaway omnia-dev-dogfood-lostupdate-probe, built from the
DEPLOYED base image omnia-template-nextjs-entities:dev; starter `Task` entity,
owner-auth via Auth.js credentials — no LLM, no generation). The probe is a
faithful simulation of the managed <CrudResource> edit flow: <EntityForm> in
edit mode seeds EVERY field from the loaded record (entity-form.tsx:82-88) and
validate() emits EVERY non-empty field (entity-form.tsx:149-167), so each save
is a FULL stale snapshot, not a field-level patch.

  CREATE Task {title, priority:"low", notes:"orig"}              -> 200 (done:false default applied)
  -- both editors load the same snapshot {priority:"low", notes:"orig"} --
  Editor A  PUT {title, done:false, priority:"high", notes:"orig"}        -> 200
  CONTROL   GET                                            -> priority:"high"   ← update WORKS
  Editor B  PUT {title, done:false, priority:"low", notes:"edited-by-B"}  -> 200  ← NOT 409
            (B never saw A's high; its form re-submits the STALE priority:"low")
  FINAL     GET  -> priority:"low", notes:"edited-by-B"   ← A's "high" SILENTLY LOST

The CONTROL proves the update mechanism is healthy — a single editor's change
persists. The bug is purely concurrent/stale: B's second save reverts A's field
with no conflict signal.

Root, code-proven below:
  1. updateRecord reads the existing row, then `merged = {...existing,
     ...parsed.data}` and writes it wholesale — the WHERE clause is only
     id+entity+createdBy, with NO version/updated_at precondition. Nothing
     detects that `existing` changed since the editor loaded it
     (engine.ts:285-297).
  2. There is no version/etag/If-Match/expectedVersion token ANYWHERE in the
     entity runtime (the only 409 in the template is auth-register "email
     taken"). The PUT route forwards the body straight to updateRecord with no
     precondition (api/entities/[entity]/[id]/route.ts PUT).
  3. The form makes every edit a full-snapshot submit, so a stale field IS sent
     and DOES overwrite — the merge does not save it (entity-form.tsx:82-88,
     149-167).

Class wider than CRM: any growing entity app where the owner edits from two
places, or two staff share a login, or a slow save is double-clicked — the
quietly-overwritten field is gone. Family of silent data-integrity gaps:
BS-17 (clear is a no-op), BS-19 (delete orphans children), BS-22 (one bad date
500s sort).

Why this is a PROPOSAL, not a blind ship: optimistic concurrency is a
write-contract change for EVERY generated app and multi-surface — a version
column (or updated_at as the token) in the schema/init-db, an If-Match /
expectedVersion precondition in updateRecord that returns 409 on mismatch, the
form/SDK sending the loaded version, and a conflict-resolution UX in apps/web /
CrudResource ("this record changed since you opened it — reload / overwrite").
base-rebuild + regen-verify, data-integrity sensitive. Min one fix per run.
→ PROPOSAL P-LOSTUPDATE.

These are deterministic file-content asserts (money-free, no container, no LLM);
the dynamic behaviour above was proven once live on the deployed base engine and
is identical in every generated app.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_FORM = _ENTITIES / "src" / "components" / "omnia" / "entity-form.tsx"


def _fn_body(src: str, start_marker: str) -> str:
    """Return source from `start_marker` up to the next top-level `export`."""
    i = src.index(start_marker)
    nxt = src.find("\nexport ", i + len(start_marker))
    return src[i : nxt if nxt != -1 else len(src)]


def test_update_is_unconditional_last_write_wins_today() -> None:
    """EVIDENCE (green today): updateRecord merges the incoming body over the
    existing row and writes it back with no precondition — the WHERE clause is
    only id+entity+createdBy. Nothing checks that `existing` is still the row the
    editor loaded (live: B's stale save reverted A's field, both 200)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    # Read-merge-write: the merge takes the incoming data wholesale.
    assert "...(existing[0].data as Record<string, unknown>)" in body
    assert "...parsed.data" in body
    assert ".update(records)" in body
    # No optimistic-concurrency token consulted in the write path.
    assert "version" not in body.lower()
    assert "if-match" not in body.lower()
    assert "expectedversion" not in body.lower()


def test_no_concurrency_token_anywhere_in_entity_runtime_today() -> None:
    """EVIDENCE (green today): there is no version / etag / If-Match /
    optimistic-lock primitive anywhere in the engine, the schema, or the form —
    so a stale write cannot even be detected, let alone rejected with 409."""
    engine = _ENGINE.read_text(encoding="utf-8").lower()
    registry = _REGISTRY.read_text(encoding="utf-8").lower()
    form = _FORM.read_text(encoding="utf-8").lower()
    for blob, label in ((engine, "engine"), (registry, "registry"), (form, "form")):
        for token in ("expectedversion", "if-match", "optimistic", "etag"):
            assert token not in blob, f"unexpected concurrency token {token!r} in {label}"


def test_edit_form_submits_a_full_stale_snapshot_today() -> None:
    """EVIDENCE (green today): the edit form seeds every field from the loaded
    record and validate() emits every non-empty field, so each save is a FULL
    snapshot. A field the editor never touched (but holds a stale value for) is
    still sent — which is exactly what overwrites a concurrent edit through the
    wholesale merge."""
    src = _FORM.read_text(encoding="utf-8")
    # Seeds every field from `initial` (the loaded record).
    assert "initial?.[f.name]" in src
    # validate() iterates ALL fields and emits each non-empty one.
    assert "for (const f of fields)" in src


@pytest.mark.xfail(
    strict=False,
    reason="BS-25 / P-LOSTUPDATE not yet landed: two edits of the same record "
    "silently clobber (last write wins, no 409). When updateRecord gains an "
    "optimistic-concurrency precondition (a version/updated_at token that must "
    "match, returning 409 on mismatch), flip this to XPASS.",
)
def test_concurrent_edit_should_not_silently_clobber() -> None:
    """DESIRED: a write based on a stale read must not silently overwrite a newer
    change. updateRecord should consult an optimistic-concurrency token (a
    version column, or `updated_at` passed as If-Match / expectedVersion) and
    reject the stale write with 409 so the UI can prompt to reload/merge. Until
    then the update is unconditional and the losing edit vanishes with no
    signal."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    is_concurrency_aware = (
        "version" in body.lower()
        or "expectedversion" in body.lower()
        or "if-match" in body.lower()
        # An updated_at precondition in the WHERE clause would also qualify.
        or "updatedat" in body.lower().replace("_", "")
        and "eq(records.updatedat" in body.lower().replace("_", "")
    )
    assert is_concurrency_aware, (
        "updateRecord is still unconditional — it merges and writes with no "
        "version/precondition, so a stale concurrent edit overwrites silently."
    )
