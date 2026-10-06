"""Recover only safe provider identity and money before receipt validation."""

from collections.abc import Mapping
from typing import Any

from yleum_gateway.services.pricing import read_reported_cost


def receipt_evidence(data: object, headers: object = None) -> dict[str, Any]:
    """Malformed fields become unknown; no response content enters the journal."""
    body = data if isinstance(data, Mapping) else {}
    raw_headers = headers if headers is not None else body.get("_provider_headers", body.get("headers"))
    safe_headers = raw_headers if isinstance(raw_headers, Mapping) else {}
    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    # The chat adapter explicitly sets metadata.provider_request_id=None when
    # its public response ID was synthesized locally. Never promote that ID.
    body_id = None if "provider_request_id" in metadata else body.get("id")
    request_id = next((value for value in (
        safe_headers.get("x-llmgw-request-id"), metadata.get("provider_request_id"),
        body.get("provider_request_id"), body_id,
    ) if isinstance(value, str) and value.strip() and len(value) <= 512 and value.isprintable()), None)
    reported = read_reported_cost(body, safe_headers)
    # GatewayError.details uses explicit provider_cost_* fields. Re-validate
    # these scalars too, rather than trusting arbitrary exception payloads.
    error_reported = read_reported_cost({"usage": {
        "cost_rub": body.get("provider_cost_rub"), "cost_usd": body.get("provider_cost_usd"),
    }}, {})
    return {
        "provider_request_id": request_id,
        "provider_cost_rub": reported.cost_rub if reported.cost_rub is not None else error_reported.cost_rub,
        "provider_cost_usd": reported.cost_usd if reported.cost_usd is not None else error_reported.cost_usd,
    }
