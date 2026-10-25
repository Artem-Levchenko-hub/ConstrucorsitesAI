"""Check the final encoded provider request, including images, text and tools."""
import json
from typing import Any

from yleum_gateway.core.errors import ValidationFailedError

# Conservative decimal limit for the documented 32 MB Messages request limit.
MAX_PROVIDER_REQUEST_BYTES = 32_000_000


def require_provider_payload_size(payload: dict[str, Any]) -> None:
    size = 0
    try:
        for piece in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"),
                                      allow_nan=False).iterencode(payload):
            size += len(piece.encode("utf-8"))
            if size > MAX_PROVIDER_REQUEST_BYTES:
                raise ValidationFailedError("Provider request exceeds size limit")
    except (ValueError, TypeError):
        raise ValidationFailedError("Invalid provider request encoding") from None
