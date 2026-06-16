"""Repro-lock for the entity filename↔Name guard (dogfood run #10, BS-12).

Live repro: a CRM build ("CRM для записи клиентов … заметки по каждому клиенту",
slug dogfood-crm-edit-563092) escalated correctly to nextjs_entities, but the
art-director brief listed the entity files as `clients.json` / `notes.json` /
`bookings.json` (lowercase-plural) and the writer copied those filenames verbatim
while declaring `{"name": "Client"}` etc. inside. The runtime registry resolves a
definition strictly by `entities/<Name>.json`, so `loadEntity("Client")` stat'd
`entities/Client.json` → ENOENT → 404 "unknown entity 'Client'" on EVERY read and
write → the whole app was dead (browser console: `ApiError: unknown entity
'Client'`, empty lists everywhere, create silently failed).

`_normalize_entity_filenames` realigns each `entities/<X>.json` filename with its
declared `name` before commit + hot_reload, deterministically and fail-soft.
"""

from __future__ import annotations

from omnia_api.routers.messages import _normalize_entity_filenames


def test_lowercase_plural_filename_is_realigned_to_internal_name() -> None:
    """The exact live failure: clients.json{name:Client} → entities/Client.json."""
    files = {
        "entities/clients.json": '{"name": "Client", "access": "owner", "fields": {}}',
        "entities/notes.json": '{"name": "Note", "access": "owner", "fields": {}}',
        "entities/bookings.json": '{"name": "Booking", "access": "owner", "fields": {}}',
        "src/app/(app)/dashboard/clients/page.tsx": "// untouched",
    }
    out = _normalize_entity_filenames(files)
    # Files now resolve at the path the registry reads (`entities/<Name>.json`).
    assert "entities/Client.json" in out
    assert "entities/Note.json" in out
    assert "entities/Booking.json" in out
    # The mismatched originals are gone (the container won't carry a dead twin).
    assert "entities/clients.json" not in out
    assert "entities/notes.json" not in out
    assert "entities/bookings.json" not in out
    # Content is preserved verbatim; non-entity files are untouched.
    assert '"name": "Client"' in out["entities/Client.json"]
    assert out["src/app/(app)/dashboard/clients/page.tsx"] == "// untouched"


def test_already_correct_filenames_are_a_noop() -> None:
    """Idempotent: a build whose filenames already match Name is left as-is."""
    files = {
        "entities/Client.json": '{"name": "Client", "access": "owner", "fields": {}}',
        "entities/Task.json": '{"name": "Task", "access": "owner", "fields": {}}',
    }
    assert _normalize_entity_filenames(files) == files


def test_does_not_clobber_an_existing_correctly_named_file() -> None:
    """If both a correct and a mismatched file declare the same Name, keep the
    correct one untouched rather than overwrite it from the stray sibling."""
    files = {
        "entities/Client.json": '{"name": "Client", "fields": {"a": {"type": "string"}}}',
        "entities/clients.json": '{"name": "Client", "fields": {"b": {"type": "string"}}}',
    }
    out = _normalize_entity_filenames(files)
    # The correctly-named file is preserved verbatim.
    assert out["entities/Client.json"] == files["entities/Client.json"]
    # The stray sibling is NOT written over the correct one.
    assert '"a"' in out["entities/Client.json"]


def test_fail_soft_on_malformed_json_and_missing_name() -> None:
    """Never raise; leave files we can't confidently interpret untouched."""
    files = {
        "entities/broken.json": "{ not valid json ",
        "entities/noname.json": '{"access": "owner", "fields": {}}',
        "entities/weird name.json": '{"name": "has space"}',  # name not an identifier
    }
    out = _normalize_entity_filenames(files)
    assert out == files


def test_non_entity_json_is_ignored() -> None:
    """A JSON file outside entities/ with a `name` field is never renamed."""
    files = {
        "package.json": '{"name": "my-app", "version": "1.0.0"}',
        "tsconfig.json": '{"compilerOptions": {}}',
    }
    assert _normalize_entity_filenames(files) == files
