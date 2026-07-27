"""GET /v1/models — model catalog with RUB prices and per-key availability."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import SecretStr

from omnia_gateway.core.config import Settings, get_settings
from omnia_gateway.services.pricing import list_models

router = APIRouter(prefix="/v1", tags=["models"])


def _has(secret: SecretStr | None) -> bool:
    """SecretStr is not None AND has non-empty value (env vars often arrive empty)."""
    return secret is not None and bool(secret.get_secret_value())


def _is_available(model_id: str, provider: str, s: Settings) -> bool:
    """A model is available iff the key for its upstream is present.

    Everything text + flux images runs on aitunnel; only gpt-image-1 (and the
    whisper STT surface) run on proxyapi.
    """
    if model_id == "gpt-image-1":
        return _has(s.proxyapi_api_key)
    return _has(s.aitunnel_api_key)


@router.get("/models")
async def list_models_endpoint() -> dict[str, object]:
    settings = get_settings()
    data: list[dict[str, object]] = []
    for m in list_models():
        entry = dict(m)
        entry["available"] = _is_available(
            str(entry["id"]), str(entry["provider"]), settings
        )
        data.append(entry)
    return {"object": "list", "data": data}
