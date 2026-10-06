"""Operator-only reconciliation. No provider HTTP or customer billing writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

import asyncpg  # type: ignore[import-untyped]


class StatementError(ValueError):
    """Safe codes only: provider identifiers and source payloads are never echoed."""


@dataclass(frozen=True)
class Operation:
    operation_id: str
    ref_id: str | None
    kind: str
    delta_kopecks: int
    currency: str

    @property
    def receipt_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.operation_id, self.ref_id, self.kind, self.delta_kopecks, self.currency],
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class Statement:
    organization_id: str
    source_sha256: str
    source_kind: str
    operations: tuple[Operation, ...]


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and value == value.strip()
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StatementError("invalid_statement")
        result[key] = value
    return result


def parse_statement(data: bytes, *, expected_organization_id: str, source: bytes) -> Statement:
    """Accept a verified normalized ledger plus its original source, never guessed XLSX.

    Source bytes are hashed, not retained in the database. Authenticity is the
    operator's responsibility; a checksum alone does not authenticate a provider.
    """
    try:
        if not _identifier(expected_organization_id) or not 0 < len(data) <= 20_000_000:
            raise StatementError("invalid_statement")
        payload = json.loads(data, object_pairs_hook=_unique_keys)
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {
                "schema_version",
                "organization_id",
                "source_kind",
                "source_sha256",
                "operations",
            }
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["source_kind"]
            not in {
                "balance_ledger_export",
                "authorized_ledger_api",
            }
        ):
            raise StatementError("invalid_statement")
        if payload["organization_id"] != expected_organization_id:
            raise StatementError("organization_mismatch")
        digest = hashlib.sha256(source).hexdigest()
        if payload["source_sha256"] != digest:
            raise StatementError("source_hash_mismatch")
        rows = payload["operations"]
        if not isinstance(rows, list) or not 0 < len(rows) <= 50_000:
            raise StatementError("invalid_statement")
        operations = []
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row)
                != {
                    "operation_id",
                    "ref_id",
                    "kind",
                    "delta_kopecks",
                    "currency",
                }
                or not _identifier(row["operation_id"])
                or (row["ref_id"] is not None and not _identifier(row["ref_id"]))
                or row["kind"] not in {"usage", "correction", "other"}
                or row["currency"] != "RUB"
                or type(row["delta_kopecks"]) is not int
                or abs(row["delta_kopecks"]) > 2**63 - 1
            ):
                raise StatementError("invalid_statement")
            operations.append(Operation(**row))
        return Statement(
            expected_organization_id, digest, payload["source_kind"], tuple(operations)
        )
    except StatementError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise StatementError("invalid_statement") from None


async def _rows(connection: asyncpg.Connection, organization_id: str) -> list[dict[str, Any]]:
    entries = await connection.fetch(
        "SELECT e.*,f.call_id AS confirmed_call_id,f.call_receipt_hash AS confirmed_call_hash "
        "FROM provider_ledger_entries e LEFT JOIN provider_ledger_confirmations f "
        "USING (organization_id,operation_id) WHERE e.organization_id=$1 ORDER BY e.operation_id",
        organization_id,
    )
    conflicts = await connection.fetch(
        "SELECT operation_id,observed_ref_id,observed_delta_kopecks "
        "FROM provider_ledger_conflicts WHERE organization_id=$1 ORDER BY observed_hash",
        organization_id,
    )
    refs = list({entry["ref_id"] for entry in entries if entry["ref_id"] is not None})
    calls = (
        await connection.fetch(
            "SELECT id,provider_organization_id,provider_request_id,receipt_hash,status,"
            "calculated_cost_rub FROM provider_calls WHERE provider_scope='llmgw' "
            "AND provider_request_id=ANY($1::text[])",
            refs,
        )
        if refs
        else []
    )
    by_ref: dict[str, list[Any]] = {}
    for call in calls:
        by_ref.setdefault(call["provider_request_id"], []).append(call)
    conflicting: dict[str, list[int]] = {}
    conflict_refs = set()
    for conflict in conflicts:
        conflicting.setdefault(conflict["operation_id"], []).append(
            conflict["observed_delta_kopecks"]
        )
        if conflict["observed_ref_id"] is not None:
            conflict_refs.add(conflict["observed_ref_id"])
    usage_counts: dict[str, int] = {}
    for entry in entries:
        ref = entry["ref_id"]
        if entry["operation_id"] in conflicting and ref is not None:
            conflict_refs.add(ref)
        if entry["kind"] == "usage" and ref is not None:
            usage_counts[ref] = usage_counts.get(ref, 0) + 1
    rows = []
    for entry in entries:
        ref = entry["ref_id"]
        matched = by_ref.get(ref, [])
        call = matched[0] if len(matched) == 1 else None
        op_id = entry["operation_id"]
        if op_id in conflicting:
            state = "conflict"
        elif entry["kind"] == "other":
            state = "not_usage"
        elif entry["kind"] == "usage" and entry["delta_kopecks"] > 0:
            state = "invalid_usage_direction"
        elif ref is None:
            state = "missing_ref_id"
        elif ref in conflict_refs:
            state = "ref_id_conflict"
        elif usage_counts.get(ref, 0) > 1 or len(matched) > 1:
            state = "ambiguous_ref_id"
        elif call is None:
            state = "call_not_found"
        elif call["provider_organization_id"] is None:
            state = "call_organization_unknown"
        elif call["provider_organization_id"] != organization_id:
            state = "organization_mismatch"
        elif call["status"] == "started" or call["receipt_hash"] is None:
            state = "call_receipt_incomplete"
        elif entry["confirmed_call_id"] is None:
            state = "needs_confirmation"
        elif (
            entry["confirmed_call_id"] != call["id"]
            or entry["confirmed_call_hash"] != call["receipt_hash"]
        ):
            state = "confirmation_mismatch"
        else:
            state = "confirmed"
        eligible = state in {"needs_confirmation", "confirmed"}
        # Foreign or unknown account identities do not leak matched call details.
        rows.append(
            {
                "operation_id": op_id,
                "ref_id": ref,
                "kind": entry["kind"],
                "delta_kopecks": entry["delta_kopecks"],
                "currency": "RUB",
                "state": state,
                "call_id": str(call["id"]) if eligible and call is not None else None,
                "call_receipt_hash": call["receipt_hash"]
                if eligible and call is not None
                else None,
                "entry_receipt_hash": entry["receipt_hash"],
                "source_sha256": entry["source_sha256"],
                "conflicting_delta_kopecks": conflicting.get(op_id, []),
                "calculated_cost_rub": str(call["calculated_cost_rub"])
                if eligible and call is not None and call["calculated_cost_rub"] is not None
                else None,
                "confirmed_expense_kopecks": -entry["delta_kopecks"] if state == "confirmed" else 0,
            }
        )
    return rows


def _report(organization_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed = [row for row in rows if row["state"] == "confirmed"]
    expense = sum(row["confirmed_expense_kopecks"] for row in confirmed)
    unique_calls = {row["call_id"]: row["calculated_cost_rub"] for row in confirmed}
    original_calls = {row["call_id"] for row in confirmed if row["kind"] == "usage"}
    # An export containing only a correction has no original charge cohort to
    # compare with the full response estimate; its signed amount is still exact.
    complete = (
        bool(unique_calls)
        and set(unique_calls) <= original_calls
        and all(cost is not None for cost in unique_calls.values())
    )
    with localcontext() as context:
        context.prec = 64
        estimate = (
            sum((Decimal(cost) for cost in unique_calls.values()), Decimal(0)) if complete else None
        )
        difference = Decimal(expense) / 100 - estimate if estimate is not None else None
    states: dict[str, int] = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    return {
        "organization_id": organization_id,
        "states": states,
        "operations": rows,
        "confirmed_expense_kopecks": expense,
        "linked_estimate_rub": str(estimate) if estimate is not None else None,
        "estimate_difference_rub": str(difference) if difference is not None else None,
        "unresolved_operations": sum(
            row["state"] not in {"confirmed", "not_usage"} for row in rows
        ),
        "customer_billing_modified": False,
    }


async def report_organization(
    connection: asyncpg.Connection, organization_id: str
) -> dict[str, Any]:
    """Restricted operator report; never a customer endpoint."""
    if not _identifier(organization_id):
        raise StatementError("invalid_organization")
    async with connection.transaction():
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('llmgw:ledger'),hashtext($1))", organization_id
        )
        return _report(organization_id, await _rows(connection, organization_id))


async def import_statement(connection: asyncpg.Connection, statement: Statement) -> dict[str, Any]:
    """Atomic, replay-safe import. All variants are recorded before matching.

    The three ledger tables are the only write targets. Original call receipts,
    Usage and customer wallets are neither mutated nor used as payment intents.
    """
    from uuid import UUID

    org = statement.organization_id
    async with connection.transaction():
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('llmgw:ledger'),hashtext($1))", org
        )
        for operation in statement.operations:
            await connection.execute(
                "INSERT INTO provider_ledger_entries (organization_id,operation_id,ref_id,kind,"
                "delta_kopecks,currency,receipt_hash,source_sha256,source_kind) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING",
                org,
                operation.operation_id,
                operation.ref_id,
                operation.kind,
                operation.delta_kopecks,
                operation.currency,
                operation.receipt_hash,
                statement.source_sha256,
                statement.source_kind,
            )
            existing_hash = await connection.fetchval(
                "SELECT receipt_hash FROM provider_ledger_entries "
                "WHERE organization_id=$1 AND operation_id=$2",
                org,
                operation.operation_id,
            )
            if existing_hash != operation.receipt_hash:
                await connection.execute(
                    "INSERT INTO provider_ledger_conflicts (organization_id,operation_id,"
                    "observed_hash,observed_ref_id,observed_kind,observed_delta_kopecks,"
                    "source_sha256) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING",
                    org,
                    operation.operation_id,
                    operation.receipt_hash,
                    operation.ref_id,
                    operation.kind,
                    operation.delta_kopecks,
                    statement.source_sha256,
                )
        for row in await _rows(connection, org):
            if row["state"] == "needs_confirmation":
                await connection.execute(
                    "INSERT INTO provider_ledger_confirmations (organization_id,operation_id,"
                    "call_id,entry_receipt_hash,call_receipt_hash) "
                    "VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                    org,
                    row["operation_id"],
                    UUID(row["call_id"]),
                    row["entry_receipt_hash"],
                    row["call_receipt_hash"],
                )
        return _report(org, await _rows(connection, org))
