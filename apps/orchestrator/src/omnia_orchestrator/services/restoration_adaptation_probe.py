"""Validate the target-owned business probe declared by adapted code."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    ActivationBusinessWitness,
)
from omnia_orchestrator.services.restoration_binding import canonical_digest
from omnia_orchestrator.services.restoration_data_contract import (
    DataContract,
    DataTable,
    technical_default,
)

_PROBE_PATH = ".omnia/restoration-probe.json"
_MAX_MANIFEST_BYTES = 32 * 1024
_MANAGED_TABLES = frozenset(
    {
        "max_users",
        "max_webhook_events",
        "max_catalog_items",
        "max_business_actions",
        "max_consents",
        "max_analytics_events",
        "max_bot_outbox",
        "max_audit_log",
    }
)
_TEXT_TYPES = ("text", "char", "varchar", "character varying")


class _ProbeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(strict=True)
    endpoint: str = Field(min_length=1, max_length=240)
    witnesses: tuple[ActivationBusinessWitness, ...] = Field(min_length=1, max_length=32)
    max_payload_bytes: int = Field(default=4096, ge=1024, le=8192, strict=True)


type ValidatedProbeContract = ActivationBusinessProbe

_IMPLEMENTATION_REQUIREMENTS = """\
Create `.omnia/restoration-probe.json` with strict JSON fields: version=1,
same-origin target-owned endpoint under `/api/`, max_payload_bytes 1024..8192,
and one witness per changed probeable business entity. Each witness names the
actual DataContract entity, UUID primary-key id_column, direct owner_column,
one mutable text value_column, and scalar create_values for every other
required column without a technical default.

Implement the declared endpoint in the generated target application. It must
use the application's signed owner session and the declared business table on
DATABASE_URL. GET endpoint?entity=&marker=&limit= returns
{probeContractDigest,items,complete}; POST accepts
{id,entity,marker,phase,values}; GET/PATCH/DELETE use
endpoint/{entity}/{id}. Every item is {id,ownerId,entity,marker,phase}, where
the stored value_column is exactly `${marker}:${phase}`. POST must use the
supplied UUID id and must not upsert another owner's row. GET/PATCH/DELETE must
scope by both id and signed owner. Missing or malformed auth returns 401;
cross-owner reads and mutations return 403/404/409 without changing data.
Every successful response echoes the normalized probeContractDigest. Marker
filtering must be exact and complete. No platform-managed table or
`/api/omnia/*` route may implement this proof.
"""


def probe_implementation_requirements() -> str:
    """Deterministic prompt/repair contract for generated adaptive code."""

    return _IMPLEMENTATION_REQUIREMENTS


def validate_probe_contract(
    files: Mapping[str, str],
    data_contract: DataContract | Mapping[str, object],
    *,
    changed_entities: Iterable[str] | None = None,
) -> ValidatedProbeContract:
    """Return the canonical live-probe contract or fail closed.

    The source manifest cannot choose arbitrary response locations: the returned
    contract carries one fixed, bounded response envelope. Each witness is
    checked against the observed live ``DataContract`` and every requested
    changed entity must be directly owner-scoped and writable.
    """

    raw = files.get(_PROBE_PATH)
    if not isinstance(raw, str):
        raise CellIdentityConflict("adaptation business probe manifest is missing")
    encoded = raw.encode("utf-8")
    if not encoded or len(encoded) > _MAX_MANIFEST_BYTES:
        raise CellIdentityConflict("adaptation business probe manifest is invalid")
    try:
        parsed = json.loads(raw)
        manifest = _ProbeManifest.model_validate(parsed)
        contract = (
            data_contract
            if isinstance(data_contract, DataContract)
            else DataContract.model_validate(data_contract)
        )
    except (json.JSONDecodeError, UnicodeError, ValidationError, TypeError, ValueError):
        raise CellIdentityConflict("adaptation business probe manifest is invalid") from None
    if manifest.version != 1:
        raise CellIdentityConflict("adaptation business probe version is unsupported")

    tables = {table.name: table for table in contract.tables}
    requested = _requested_entities(tables, changed_entities)
    covered = {witness.entity for witness in manifest.witnesses}
    if not requested.issubset(covered):
        raise CellIdentityConflict("adaptation business probe misses a changed entity")
    for witness in manifest.witnesses:
        table = tables.get(witness.entity)
        if table is None:
            raise CellIdentityConflict("adaptation business probe table is unavailable")
        _validate_witness(witness, table)

    _validate_payload_budget(manifest)

    data_contract_digest = canonical_digest(contract.model_dump(mode="json"))
    normalized = {
        "version": 1,
        "endpoint": manifest.endpoint,
        "data_contract_digest": data_contract_digest,
        "witnesses": [item.model_dump(mode="json") for item in manifest.witnesses],
        "max_payload_bytes": manifest.max_payload_bytes,
        "pointers": {
            "items": "/items",
            "item": "/item",
            "item_id": "/item/id",
            "owner_id": "/item/ownerId",
            "entity": "/item/entity",
            "marker": "/item/marker",
            "phase": "/item/phase",
            "contract_digest": "/probeContractDigest",
        },
    }
    return ActivationBusinessProbe.model_validate(
        {**normalized, "contract_digest": canonical_digest(normalized)}
    )


def _validate_payload_budget(manifest: _ProbeManifest) -> None:
    """Reject an offer that cannot carry the deterministic rehearsal envelopes."""

    digest = "f" * 64
    owner = "00000000-0000-0000-0000-000000000000"
    item_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    for witness in manifest.witnesses:
        marker = f"{'f' * 32}:unauth:{witness.entity}"
        phase = "unauthenticated"
        item = {
            "id": item_id,
            "ownerId": owner,
            "entity": witness.entity,
            "marker": marker,
            "phase": phase,
        }
        envelopes: tuple[object, ...] = (
            {
                "id": item_id,
                "entity": witness.entity,
                "marker": marker,
                "phase": phase,
                "values": witness.create_values,
            },
            {"phase": phase},
            {"probeContractDigest": digest, "item": item},
            {"probeContractDigest": digest, "items": [item], "complete": True},
            {"probeContractDigest": digest, "deleted": True, "id": item_id},
        )
        largest = max(
            len(
                json.dumps(
                    envelope,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            for envelope in envelopes
        )
        if largest > manifest.max_payload_bytes:
            raise CellIdentityConflict(
                "adaptation business probe payload cap cannot carry its witness"
            )


def _requested_entities(
    tables: Mapping[str, DataTable], changed_entities: Iterable[str] | None
) -> set[str]:
    supported = {
        name
        for name, table in tables.items()
        if name not in _MANAGED_TABLES
        and not table.read_only
        and table.owner_column is not None
        and table.owner_reference is None
    }
    if changed_entities is None:
        requested = supported
    else:
        changed = set(changed_entities)
        if not changed or any(not isinstance(name, str) for name in changed):
            raise CellIdentityConflict("adaptation changed business entities are invalid")
        if any(name not in tables for name in changed):
            raise CellIdentityConflict("adaptation changed business entity is unavailable")
        requested = changed & supported
    if not requested:
        raise CellIdentityConflict("adaptation has no probeable business entity")
    return requested


def _validate_witness(witness: ActivationBusinessWitness, table: DataTable) -> None:
    columns = {column.name: column for column in table.columns}
    if (
        table.name in _MANAGED_TABLES
        or table.read_only
        or table.owner_reference is not None
        or table.owner_column is None
        or table.owner_column != witness.owner_column
        or table.primary_key != [witness.id_column]
        or "uuid" not in columns[witness.id_column].type.casefold()
        or witness.value_column not in columns
        or len(
            {
                witness.id_column,
                witness.owner_column,
                witness.value_column,
            }
        )
        != 3
    ):
        raise CellIdentityConflict("adaptation business witness does not match its table")
    if not any(token in columns[witness.value_column].type.casefold() for token in _TEXT_TYPES):
        raise CellIdentityConflict("adaptation business witness value is not text")
    reserved = {
        witness.id_column,
        witness.owner_column,
        witness.value_column,
    }
    if reserved & witness.create_values.keys():
        raise CellIdentityConflict("adaptation business witness overrides protected columns")
    if any(name not in columns for name in witness.create_values):
        raise CellIdentityConflict("adaptation business witness value column is unavailable")
    supplied = reserved | witness.create_values.keys()
    # Недостающие колонки известны ровно здесь, поэтому здесь их и называем.
    # Живой прогон 4a154055: агент открывал их по одной, каждая ценой полного
    # круга с копией проекта (~13 минут), и прогон кончился по сроку раньше,
    # чем перебрал все. Имя колонки — это схема, а не данные владельца, и оно
    # уже и так звучит в отчёте для него.
    missing = [
        column.name
        for column in table.columns
        if not column.nullable
        and column.name not in supplied
        and not technical_default(column)
    ]
    if missing:
        raise CellIdentityConflict(
            "adaptation business witness misses a required value " + " ".join(missing)
        )
