"""Repro-lock for malformed entity JSON shipping DOA (dogfood run #28, BS-31).

Live repro: a sales-CRM build ("CRM для отдела продаж: сделки со стадиями …
канбан-доска …", slug dogfood-crm-kanban-sdelki-882231) escalated correctly to
nextjs_entities (BS-4) and the writer even used the kanban board view
(`<CrudResource entity="Deal" view="board">`) as the brief asked — but the
writer (deepseek) emitted INVALID JSON for one entity file:

    entities/Client.json
    { "name": "Client",
      "access":",                 <-- unterminated string, swallows the newline
      "fields": { ... } }

`node -e JSON.parse(...)` on the shipped file → "Bad control character in string
literal in JSON". The runtime `loadEntity` swallows the parse error and returns
null (registry.ts:87-89), so on the LIVE container:

    GET /api/entities/Client → 404 {"error":"unknown entity 'Client'"}
    GET /api/entities/Deal   → 401 {"error":"authentication required"}  (valid)

Client is a CORE entity — Deal.clientId references it, /clients lists it, the
dashboard "Новые клиенты" KPI counts it — so a whole slice of the CRM is silently
dead, with zero signal anywhere.

The blind spot: NO guard validates that writer-emitted `entities/*.json` actually
parses. `_normalize_entity_filenames` explicitly skips unparseable files
(messages.py:1227-1228, `except Exception: continue # malformed JSON — leave it
for the registry/normalize() path`), and the registry then silently nulls it.
Distinct from BS-12 (filename≠Name, JSON valid): here the JSON itself is broken,
so BS-12's guard — which only acts when JSON parses — cannot catch it.

These tests pin the current (broken) behavior as evidence and xfail the desired
post-fix contract: nothing unparseable under `entities/` should ever ship.
"""

from __future__ import annotations

import json

import pytest

from omnia_api.routers.messages import _normalize_entity_filenames

# The exact malformed payload dumped by the live writer (03_writer_raw.html):
# an unterminated `access` string that swallows the rest of the object.
_BROKEN_CLIENT = '{\n  "name": "Client",\n  "access":",\n  "fields": {\n    "name": { "type": "string", "required": true }\n  }\n}'
_VALID_DEAL = '{"name": "Deal", "access": "admin", "fields": {"title": {"type": "string", "required": true}, "clientId": {"type": "reference", "entity": "Client"}}}'


def test_live_payload_is_genuinely_unparseable() -> None:
    """Sanity: the shipped Client.json really does fail JSON parsing."""
    with pytest.raises(ValueError):
        json.loads(_BROKEN_CLIENT)
    # …while the sibling Deal.json parses fine (only Client was fumbled).
    assert json.loads(_VALID_DEAL)["name"] == "Deal"


def test_current_guard_chain_ships_malformed_entity_json_verbatim() -> None:
    """EVIDENCE of the bug: today the broken entity file passes through the
    filename guard untouched — it is committed and hot-reloaded as-is, then the
    registry silently nulls it (`loadEntity` → null → 404 'unknown entity')."""
    files = {
        "entities/Client.json": _BROKEN_CLIENT,
        "entities/Deal.json": _VALID_DEAL,
        "src/app/(app)/dashboard/clients/page.tsx": "// uses entities.Client",
    }
    out = _normalize_entity_filenames(files)
    # The malformed file is shipped exactly as the writer produced it.
    assert out["entities/Client.json"] == _BROKEN_CLIENT
    # And it is still unparseable on the way out — nothing repaired or dropped it.
    with pytest.raises(ValueError):
        json.loads(out["entities/Client.json"])


def test_invalid_entity_json_is_flagged_in_build_log(capsys: pytest.CaptureFixture[str]) -> None:
    """BS-31 safe sub-fix (detection half of P-ENTITYJSON, dogfood run #31):
    an unparseable `entities/*.json` is now LOGGED loudly at build time instead
    of shipping with zero signal. The file is still emitted verbatim — repair /
    regen / build-fail is the policy-adjacent action that stays deferred — so
    this only removes the silence, it does not change what ships."""
    from omnia_api.routers.messages import _warn_unparseable_entity_json

    files = {
        "entities/Client.json": _BROKEN_CLIENT,
        "entities/Deal.json": _VALID_DEAL,
    }
    bad = _warn_unparseable_entity_json(files)
    # Only the genuinely-corrupt entity is flagged; the valid sibling is not.
    assert bad == ["Client"]
    out = capsys.readouterr().out
    assert "entity_json_INVALID: Client.json" in out
    assert "Deal" not in out


@pytest.mark.xfail(
    reason="BS-31 / PROPOSAL P-ENTITYJSON: no guard validates writer-emitted "
    "entity JSON; a malformed entities/*.json ships and the entity is silently "
    "DOA (404 'unknown entity'). Fix is regen-of-broken-file or loud build-fail "
    "(policy-adjacent, cross-surface) — not shipped blind.",
    strict=True,
)
def test_no_unparseable_entity_json_ships() -> None:
    """DESIRED contract: after the guard chain, EVERY shipped `entities/*.json`
    must parse as JSON (repaired, regenerated, or the build must fail loudly).
    A user must never receive a runtime where a declared entity is silently
    unresolvable."""
    files = {
        "entities/Client.json": _BROKEN_CLIENT,
        "entities/Deal.json": _VALID_DEAL,
    }
    out = _normalize_entity_filenames(files)
    for path, content in out.items():
        if path.startswith("entities/") and path.endswith(".json"):
            # Must not raise — every entity definition the app ships is loadable.
            json.loads(content)
