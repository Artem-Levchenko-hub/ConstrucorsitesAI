"""Versioned public metadata transport: no source snapshots or generation admission."""

import hashlib
import json
from typing import Any

from yleum_api.core.errors import ApiError
from yleum_api.schemas.max_studio import MaxProjectConfigPayload

MAX_APP_CONFIG_BYTES = 900 * 1024


def public_business_config(payload: MaxProjectConfigPayload) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude={"max_url_attached", "management_modules"})


def config_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def validate_delivery_size(payload: MaxProjectConfigPayload, version: int) -> None:
    envelope = {"config_version": version, "config": public_business_config(payload)}
    if len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode()) > (
        MAX_APP_CONFIG_BYTES
    ):
        raise ApiError("too_large", "Каталог слишком большой. Уменьшите описания и варианты", 413)
