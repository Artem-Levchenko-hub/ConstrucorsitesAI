"""BS-17 / P-CLEAR — clearing an optional field on edit.

WAS the bug (dogfood run #14, 2026-06-16): you could CHANGE a field on edit but
never CLEAR an optional one — clearing silently no-op'd while the UI toasted
"Изменения сохранены". Three surfaces conspired:
  1. entity-form.tsx validate(): an empty non-required field hit `continue`
     BEFORE any `out[f.name] = …`, so a cleared field was dropped from the payload.
  2. registry.ts updateSchema(): every field was `zodForField(f).optional()` —
     NOT `.nullable()` — so even an explicit `null` would 400 the update.
  3. engine.ts merge `{...existing, ...parsed.data}`: a field absent from the
     payload kept its old value.

FIXED 2026-06-22 (template change, base-image rebuild). The fix touches the two
surfaces that were wrong (the merge was already correct — absent=keep,
present-null=overwrite):
  - updateSchema is now `zodForField(f).optional().nullable()` → a clear is
    expressible on the wire (sending `null` blanks the field).
  - entity-form.tsx now emits an explicit `null` for an emptied optional field in
    EDIT mode (gated on `initial`); CREATE still omits it so defaults apply.

These are deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

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


def test_form_emits_explicit_null_clear_on_edit() -> None:
    """FIXED: on EDIT (initial present) the form now sends an emptied optional field
    as an explicit `null` instead of omitting it, so the engine overwrites the stored
    value. On CREATE it's still omitted (so a field's `default` applies)."""
    src = _FORM.read_text(encoding="utf-8")
    assert 'const empty = raw === "" || raw == null;' in src
    # The empty branch now emits an explicit clear…
    assert re.search(r"(?:out|payload)\[[^\]]+\]\s*=\s*null\b", src), (
        "the form must emit an explicit null clear for an emptied field"
    )
    # …gated on EDIT mode (initial != null), so a CREATE still omits empties.
    assert re.search(r"else if \(initial != null\)", src), (
        "the null clear must be gated on edit mode (initial)"
    )


def test_update_schema_accepts_null_to_express_a_clear() -> None:
    """FIXED: updateSchema marks every field `.optional().nullable()`, so a clear is
    expressible on the wire — sending `null` blanks the field instead of a 400."""
    body = _update_schema_body()
    assert "nullable" in body or "nullish" in body
    assert ".optional()" in body


def test_engine_merge_applies_an_explicit_null_clear() -> None:
    """The shallow merge is correct: a field ABSENT from the payload keeps its value
    (partial update), while a field PRESENT as null overwrites it (the clear). With
    the form now sending null and updateSchema accepting it, the merge persists the
    clear — so this third surface needed no change."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert (
        "const merged = { ...(existing[0].data as Record<string, unknown>), ...parsed.data };"
        in src
    )


def test_clearing_an_optional_field_persists_the_clear() -> None:
    """FIXED (was xfail): clearing an optional field on edit now sticks — both
    surfaces changed together: updateSchema accepts null AND the form sends an
    explicit null in edit mode for an emptied field."""
    update_body = _update_schema_body()
    form = _FORM.read_text(encoding="utf-8")
    schema_allows_clear = "nullable" in update_body or "nullish" in update_body
    form_emits_clear = bool(
        re.search(r"\binitial\b", form)
        and re.search(r"\bempty\b", form)
        and re.search(r"(?:out|payload)\[[^\]]+\]\s*=\s*null\b", form)
    )
    assert schema_allows_clear and form_emits_clear
