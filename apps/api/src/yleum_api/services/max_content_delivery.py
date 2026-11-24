"""Versioned public metadata transport: no source snapshots or generation admission."""

import hashlib
import json
from typing import Any
from uuid import UUID

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.schemas.max_studio import MaxProjectConfigPayload

MAX_APP_CONFIG_BYTES = 900 * 1024


def public_business_config(
    payload: MaxProjectConfigPayload, project_id: UUID | str | None = None,
) -> dict[str, Any]:
    result = payload.model_dump(
        mode="json", exclude={"max_url_attached", "management_modules", "legacy_orders_table"},
    )
    # Old compiled cores already read this URL on each request. The private
    # owner configuration keeps the original external URL and approved text.
    if result["legal"].pop("policy_text", None) and not payload.legal.policy_url:
        if project_id is None:
            raise ValueError("missing project identity for approved privacy policy")
        origin = get_settings().web_base_url.rstrip("/")
        result["legal"]["policy_url"] = f"{origin}/api/projects/{project_id}/max/privacy-policy"
    return result


def config_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def validate_delivery_size(
    payload: MaxProjectConfigPayload, version: int, project_id: UUID | None = None,
) -> None:
    envelope = {"config_version": version, "config": public_business_config(payload, project_id)}
    if len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode()) > (
        MAX_APP_CONFIG_BYTES
    ):
        raise ApiError("too_large", "Каталог слишком большой. Уменьшите описания и варианты", 413)
