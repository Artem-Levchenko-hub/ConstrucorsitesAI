"""Acceptance-lock for BS-25 (dogfood run #22, 2026-06-16; CLOSED by the
P-LOSTUPDATE wave): the entity engine now HAS opt-in optimistic-concurrency
control. Historically two people editing the SAME record (the solo owner on
phone + laptop, two browser tabs, a double-submit) silently clobbered each other
— last write wins, no version check, no 409, no warning; the edit that lost had
no signal it was lost. The wave added a load-time `updated_at` token (carried as
`_updatedAt`): updateRecord compares it against the row's current `updatedAt`
and 409s on mismatch, and the edit form sends it on every save. It is OPT-IN —
a write body WITHOUT `_updatedAt` keeps the old last-write-wins behaviour, so
token-less callers are unchanged (the one genuinely-remaining slice, kept as a
tight xfail below). The original live repro is preserved verbatim for context.

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


def test_update_has_optimistic_lock_token_in_write_path() -> None:
    """The wave landed optimistic concurrency: updateRecord still does the
    read-merge-write, but now — BEFORE the merge — it reads the `_updatedAt` the
    caller loaded the row at and, on mismatch with the row's current `updatedAt`,
    throws a 409 instead of clobbering. The merge itself is unchanged (the token
    is consulted on the RAW body, the stored data is still `existing + parsed`)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    # Read-merge-write still present: the merge takes the incoming data wholesale.
    assert "...(existing[0].data as Record<string, unknown>)" in body
    assert "...parsed.data" in body
    assert ".update(records)" in body
    # The optimistic-lock token IS now consulted in the write path: the loaded
    # `_updatedAt` is compared against the row's current `updatedAt`...
    assert "._updatedAt" in body
    assert "existing[0].updatedAt" in body
    # ...and a divergence raises 409 (conflict) rather than silently overwriting.
    norm = body.replace("\n", " ")
    assert "EngineError(" in norm
    assert "409" in body
    # The check fires only on a real mismatch (`exp !== cur`).
    assert "exp !== cur" in body


def test_optimistic_lock_token_flows_engine_to_form() -> None:
    """The optimistic-lock primitive now exists end-to-end. The chosen token is
    the row's load-time `updated_at` (carried as `_updatedAt` on the write body) —
    not a `version`/`etag`/`If-Match` header — so a stale write CAN be detected
    and rejected. The engine reads & compares it; the edit form emits it."""
    engine = _ENGINE.read_text(encoding="utf-8")
    form = _FORM.read_text(encoding="utf-8")
    # Engine side: the token is read off the body and the path is described as
    # optimistic concurrency.
    assert "_updatedAt" in engine
    assert "optimistic" in engine.lower()
    # Form side: edit mode carries the loaded `updated_at` as the `_updatedAt`
    # token so the engine has something to compare against.
    assert "_updatedAt" in form
    assert "initial.updated_at" in form


def test_edit_form_full_snapshot_now_carries_the_lock_token() -> None:
    """The edit form still submits a FULL snapshot (seeds every field from the
    loaded record, validate() emits every non-empty one) — that mechanic is
    unchanged by the wave. What changed: on submit in EDIT mode the form now also
    attaches `_updatedAt = initial.updated_at`, so the otherwise-clobbering full
    snapshot now carries the precondition the engine 409s on. The token is added
    only when `initial` exists, so a create payload never carries it."""
    src = _FORM.read_text(encoding="utf-8")
    # Seeds every field from `initial` (the loaded record).
    assert "initial?.[f.name]" in src
    # validate() iterates ALL fields and emits each non-empty one.
    assert "for (const f of fields)" in src
    # NEW: the loaded `updated_at` rides along as the optimistic-lock token, but
    # only in edit mode (guarded on `initial`), so create is unaffected.
    assert "payload._updatedAt = initial.updated_at" in src
    assert "if (initial && initial.updated_at != null)" in src


def test_concurrent_edit_should_not_silently_clobber() -> None:
    """DESIRED (now landed, BS-25 / P-LOSTUPDATE): a write based on a stale read
    must not silently overwrite a newer change. updateRecord now consults an
    optimistic-concurrency token — the `updated_at` the caller loaded the row at,
    carried as `_updatedAt` — and, when it diverges from the row's current
    `updatedAt`, rejects the stale write with 409 so the UI can prompt to
    reload/merge. The form supplies the token on every edit save (see
    test_edit_form_full_snapshot_now_carries_the_lock_token), so the managed
    <CrudResource> edit flow is conflict-protected end-to-end."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    # The token is read off the body, compared against the row's stored
    # timestamp, and a mismatch raises a 409 — the conflict signal that was
    # missing before.
    assert "._updatedAt" in body
    assert "existing[0].updatedAt" in body
    assert "exp !== cur" in body
    assert "409" in body
    is_concurrency_aware = (
        "_updatedAt" in body
        and "existing[0].updatedAt" in body
        and "409" in body
    )
    assert is_concurrency_aware, (
        "updateRecord must consult the loaded `_updatedAt` and 409 on mismatch — "
        "otherwise a stale concurrent edit overwrites silently."
    )


@pytest.mark.xfail(
    strict=False,
    reason="OPT-IN by design: the 409 fires ONLY when the write body carries a "
    "non-empty `_updatedAt`. A stale PUT that omits the token (a hand-rolled SDK "
    "call, a script, an older client) still merges last-write-wins with no "
    "conflict signal — updateRecord does not derive/require the token "
    "server-side. Closing this would mean making the precondition mandatory (or "
    "reconstructing it from the request), which the wave deliberately did NOT do "
    "to avoid regressing existing token-less callers. Flip to XPASS if the engine "
    "ever rejects a token-less stale write.",
)
def test_token_less_stale_write_should_also_be_rejected() -> None:
    """REMAINING GAP (honesty guard): optimistic locking is opt-in. updateRecord
    only enforces the precondition when `_updatedAt` is present AND non-empty —
    `expected !== undefined && expected !== null && expected !== ""`. A body
    without it skips the whole block and falls through to the unconditional merge.
    So a caller that does NOT send the token can still clobber a concurrent edit.
    The desired stricter behaviour would enforce a precondition unconditionally
    (no `_updatedAt` => reject, or reconstruct the expected version server-side)."""
    src = _ENGINE.read_text(encoding="utf-8")
    body = _fn_body(src, "export async function updateRecord(")
    # Prove the gate is opt-in: the 409 path is conditional on the token being
    # present and non-empty. If that guard were gone, the check would be
    # mandatory and this test would XPASS.
    opt_in_guard = (
        'expected !== undefined' in body
        and 'expected !== null' in body
        and 'expected !== ""' in body
    )
    # The remaining gap is exactly this opt-in gate. While it stands, a token-less
    # stale write is NOT rejected — so we (correctly) expect this assert to fail.
    assert not opt_in_guard, (
        "updateRecord still gates the 409 on a present/non-empty `_updatedAt`, so "
        "a token-less stale write bypasses the conflict check and clobbers."
    )
