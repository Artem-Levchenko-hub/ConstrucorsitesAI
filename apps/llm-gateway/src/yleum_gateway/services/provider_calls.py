"""Independent provider expense journal; never writes customer usage or wallets."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import BillingReconciliationRequiredError, GatewayError
from yleum_gateway.services.pricing import validate_cost_provenance

_ROUTES = {"/v1/chat/completions", "/v1/messages", "/v1/project-cell/messages"}
_STAGES = {"build_plan", "native_agent", "verification", "source_repair", "runtime_ai",
           "product_advisor", "unknown"}


class ReceiptFailure(StrEnum):
    """Fixed local protocol facts; never provider text or proof of non-payment."""

    INVALID_IDENTITY = "receipt_invalid_identity"
    EVENT_LIMIT = "receipt_event_limit"
    INVALID_EVENT = "receipt_invalid_event"
    UPSTREAM_ERROR = "receipt_upstream_error"
    CONFLICTING_IDENTITY = "receipt_conflicting_identity"
    CONFLICTING_USAGE = "receipt_conflicting_usage"
    INVALID_RESPONSE = "receipt_invalid_response"
    INVALID_TOOL = "receipt_invalid_tool"
    CONFLICTING_FINISH = "receipt_conflicting_finish"
    INCOMPLETE = "receipt_incomplete"
    INVALID_USAGE = "receipt_invalid_usage"
    INVALID_TOOL_ARGUMENTS = "receipt_invalid_tool_arguments"
    DUPLICATE_TOOL = "receipt_duplicate_tool"
    SIZE_LIMIT = "receipt_size_limit"
    INVALID_ENCODING = "receipt_invalid_encoding"
    MISSING_COMPLETION = "receipt_missing_completion"


_ERRORS = {"ReadTimeout", "ConnectTimeout", "ConnectError", "RemoteProtocolError",
           "HTTPStatusError", "CancelledError", "TimeoutError", "UpstreamProviderError",
           "ValidationFailedError", "BillingReconciliationRequiredError", "unknown", *ReceiptFailure}


def classify_failure(error: BaseException) -> str:
    """Both chat routes retain a bounded receipt reason before legacy class names."""
    if isinstance(error, GatewayError):
        reason = error.details.get("provider_receipt_failure")
        if isinstance(reason, str) and reason in ReceiptFailure:
            return reason
    name = type(error.__cause__ or error).__name__
    return name if name in _ERRORS else "unknown"


def _error(call_id: UUID, *, started: bool = True) -> BillingReconciliationRequiredError:
    return BillingReconciliationRequiredError(
        "Provider accounting requires reconciliation; do not replay the provider call",
        details={"provider_charge_ambiguous": started, "provider_call_id": str(call_id)},
    )


def _model(value: str | None) -> bool:
    return isinstance(value, str) and (
        value == "~anthropic/claude-sonnet-latest"
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}", value) is not None
    )


def receipt_digest(
    status: str, actual_model: str | None, provider_request_id: str | None,
    counters: tuple[Any, ...], amounts: tuple[Any, ...],
    provenance: dict[str, Any] | None, safe_error: str | None,
) -> str:
    """The immutable journal's existing receipt encoding, shared by readers."""
    return hashlib.sha256(json.dumps(
        [status, actual_model, provider_request_id, *counters,
         *[str(value.normalize()) if value is not None else None for value in amounts],
         provenance, safe_error], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


async def insert_call(
    conn: Any, call_id: UUID, *, route: str, model: str, user_id: UUID | None = None,
    project_id: UUID | None = None, run_id: UUID | None = None,
    message_id: UUID | None = None, stage: str | None = None, free: bool = False,
    provider_scope: str = "llmgw",
) -> None:
    """Insert into the caller's transaction; this helper never commits or dispatches."""
    if (not isinstance(provider_scope, str) or provider_scope not in {"llmgw", "aitunnel"}
            or route not in _ROUTES or not _model(model) or type(free) is not bool
            or any(v is not None and not isinstance(v, UUID)
                   for v in (call_id, user_id, project_id, run_id, message_id))):
        raise _error(call_id, started=False)
    settings = get_settings()
    organization = (settings.llmgw_organization_id if provider_scope == "llmgw"
                    else settings.aitunnel_text_organization_id)
    if organization is not None and (
        not isinstance(organization, str) or not 0 < len(organization) <= 512
        or organization != organization.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in organization)
    ):
        raise _error(call_id, started=False)
    await conn.execute(
        "INSERT INTO provider_calls "
        "(id,provider_scope,route,requested_model,expense_owner,user_id,project_id,"
        "run_id,message_id,stage,free,status,provider_organization_id) "
        "VALUES($1,$12,$2,$3,$4,$5,$6,$7,$8,$9,$10,'started',$11)",
        call_id, route, model, "user" if user_id is not None else "platform",
        user_id, project_id, run_id, message_id,
        stage if isinstance(stage, str) and stage in _STAGES else "unknown", free, organization,
        provider_scope,
    )


async def start_call(
    *, route: str, model: str, user_id: UUID | None = None,
    project_id: UUID | None = None, run_id: UUID | None = None,
    message_id: UUID | None = None, stage: str | None = None, free: bool = False,
    provider_scope: str = "llmgw",
) -> UUID:
    """Commit admission before upstream I/O. DB failure prevents a paid call."""
    call_id = uuid4()
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            await insert_call(conn, call_id, route=route, model=model, user_id=user_id,
                              project_id=project_id, run_id=run_id, message_id=message_id,
                              stage=stage, free=free, provider_scope=provider_scope)
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
        receipt_hash = receipt_digest(
            status, actual_model, provider_request_id, counters, amounts, provenance, safe_error,
        )
    except Exception:
        raise _error(call_id) from None

    duplicate = False
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1 FOR UPDATE", call_id)
            if row is None:
                raise _error(call_id)
            scope = row["provider_scope"]
            if scope not in {"llmgw", "aitunnel"}:
                raise _error(call_id)
            if provenance is not None and provenance.get("provider_scope", "llmgw") != scope:
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
                    "SELECT pg_advisory_xact_lock(hashtext($2), hashtext($1))",
                    provider_request_id, scope + ":provider-call",
                )
                existing = await conn.fetchrow(
                    "SELECT id FROM provider_calls WHERE provider_scope=$3 "
                    "AND provider_request_id=$1 AND id<>$2", provider_request_id, call_id, scope,
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
