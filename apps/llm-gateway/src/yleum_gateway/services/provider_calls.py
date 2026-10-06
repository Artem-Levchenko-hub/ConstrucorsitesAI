"""Independent provider expense journal; never writes customer usage or wallets."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.services.pricing import validate_cost_provenance

_ROUTES = {"/v1/chat/completions", "/v1/messages", "/v1/project-cell/messages"}
_STAGES = {"build_plan", "native_agent", "verification", "source_repair", "runtime_ai",
           "product_advisor", "unknown"}
_ERRORS = {"ReadTimeout", "ConnectTimeout", "ConnectError", "RemoteProtocolError",
           "HTTPStatusError", "CancelledError", "TimeoutError", "UpstreamProviderError",
           "ValidationFailedError", "BillingReconciliationRequiredError", "unknown"}


def _error(call_id: UUID, *, started: bool = True) -> BillingReconciliationRequiredError:
    return BillingReconciliationRequiredError(
        "Provider accounting requires reconciliation; do not replay the provider call",
        details={"provider_charge_ambiguous": started, "provider_call_id": str(call_id)},
    )


def _model(value: str | None) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}", value) is not None


async def start_call(
    *, route: str, model: str, user_id: UUID | None = None,
    project_id: UUID | None = None, run_id: UUID | None = None,
    message_id: UUID | None = None, stage: str | None = None, free: bool = False,
) -> UUID:
    """Commit admission before upstream I/O. DB failure prevents a paid call."""
    call_id = uuid4()
    if (route not in _ROUTES or not _model(model) or type(free) is not bool
            or any(v is not None and not isinstance(v, UUID)
                   for v in (user_id, project_id, run_id, message_id))):
        raise _error(call_id, started=False)
    try:
        organization = get_settings().llmgw_organization_id
        if organization is not None and (
            not isinstance(organization, str) or not 0 < len(organization) <= 512
            or organization != organization.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in organization)
        ):
            raise ValueError
        async with get_pool().acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO provider_calls "
                "(id,provider_scope,route,requested_model,expense_owner,user_id,project_id,"
                "run_id,message_id,stage,free,status,provider_organization_id) "
                "VALUES($1,'llmgw',$2,$3,$4,$5,$6,$7,$8,$9,$10,'started',$11)",
                call_id, route, model, "user" if user_id is not None else "platform",
                user_id, project_id, run_id, message_id,
                stage if isinstance(stage, str) and stage in _STAGES else "unknown", free,
                organization,
            )
    except Exception:
        raise _error(call_id, started=False) from None
    return call_id


async def finish_call(
    call_id: UUID, *, status: str = "completed", actual_model: str | None = None,
    provider_request_id: str | None = None, tokens_in: int | None = None,
    tokens_out: int | None = None, cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None, calculated_cost_rub: Decimal | None = None,
    provider_cost_rub: Decimal | None = None, provider_cost_usd: Decimal | None = None,
    cost_provenance: dict[str, Any] | None = None, error_type: str | None = None,
    usage_id: UUID | None = None,
) -> None:
    """Persist a closed receipt before billing; replay conflicts never overwrite it.

    Duplicate upstream IDs on another attempt are quarantined and raise after
    committing that state, so callers cannot debit a second customer receipt.
    Unknown costs remain NULL, including failed or ambiguous attempts.
    """
    try:
        if not isinstance(call_id, UUID) or status not in {"completed", "failed", "ambiguous"}:
            raise ValueError
        if actual_model is not None and not _model(actual_model):
            raise ValueError
        if provider_request_id is not None and (
            not isinstance(provider_request_id, str) or not provider_request_id.strip()
            or len(provider_request_id) > 512 or any(ord(c) < 32 for c in provider_request_id)
        ):
            raise ValueError
        counters = (tokens_in, tokens_out, cache_read_tokens, cache_write_tokens)
        if any(v is not None and (type(v) is not int or v < 0 or v > 2**63 - 1) for v in counters):
            raise ValueError
        amounts = (calculated_cost_rub, provider_cost_rub, provider_cost_usd)
        if any(v is not None and (not isinstance(v, Decimal) or not v.is_finite() or v < 0)
               for v in amounts):
            raise ValueError
        if status == "completed" and (not _model(actual_model) or all(v is None for v in amounts)):
            raise ValueError
        if usage_id is not None and (not isinstance(usage_id, UUID) or status != "completed"):
            raise ValueError
        provenance = validate_cost_provenance(cost_provenance) if cost_provenance is not None else None
        evidence_json = json.dumps(provenance, sort_keys=True, separators=(",", ":")) if provenance else None
        safe_error = error_type if error_type in _ERRORS else "unknown" if error_type is not None else None
        receipt_hash = hashlib.sha256(json.dumps(
            [status, actual_model, provider_request_id, *counters,
             *[str(v.normalize()) if v is not None else None for v in amounts],
             provenance, safe_error], sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
    except Exception:
        raise _error(call_id) from None

    duplicate = False
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1 FOR UPDATE", call_id)
            if row is None:
                raise _error(call_id)
            if row["status"] != "started":
                if row["receipt_hash"] != receipt_hash:
                    raise _error(call_id)
                if usage_id is not None:
                    if row["usage_id"] not in {None, usage_id}:
                        raise _error(call_id)
                    await conn.execute("UPDATE provider_calls SET usage_id=$2 WHERE id=$1", call_id, usage_id)
                return
            if provider_request_id is not None:
                # Serialize account-scoped receipt ownership, not customer billing.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('llmgw:provider-call'), hashtext($1))",
                    provider_request_id,
                )
                existing = await conn.fetchrow(
                    "SELECT id FROM provider_calls WHERE provider_scope='llmgw' "
                    "AND provider_request_id=$1 AND id<>$2", provider_request_id, call_id,
                )
                if existing is not None:
                    duplicate = True
                    await conn.execute(
                        "UPDATE provider_calls SET status='ambiguous', "
                        "error_type='duplicate_provider_receipt',finished_at=now() WHERE id=$1", call_id,
                    )
            if not duplicate:
                await conn.execute(
                    "UPDATE provider_calls SET status=$2,actual_model=$3,provider_request_id=$4,"
                    "tokens_in=$5,tokens_out=$6,cache_read_tokens=$7,cache_write_tokens=$8,"
                    "calculated_cost_rub=$9,provider_cost_rub=$10,provider_cost_usd=$11,"
                    "cost_provenance=$12::jsonb,error_type=$13,usage_id=$14,receipt_hash=$15,"
                    "finished_at=now() WHERE id=$1",
                    call_id, status, actual_model, provider_request_id, *counters, *amounts,
                    evidence_json, safe_error, usage_id, receipt_hash,
                )
    except BillingReconciliationRequiredError:
        raise
    except Exception:
        raise _error(call_id) from None
    if duplicate:
        raise _error(call_id)
