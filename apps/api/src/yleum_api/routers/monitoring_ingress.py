from hmac import compare_digest

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from yleum_api.core.config import get_settings
from yleum_api.core.monitoring_metrics import REGISTRY

router = APIRouter(include_in_schema=False)


@router.get("/internal/monitoring/metrics")
async def scrape(request: Request) -> Response:
    token = get_settings().monitoring_scrape_token
    received = request.headers.get("authorization", "")
    if (
        not token
        or not token.get_secret_value()
        or not compare_digest(
            received.encode("utf-8"), f"Bearer {token.get_secret_value()}".encode()
        )
    ):
        return Response(status_code=404)
    return Response(
        content=generate_latest(REGISTRY),
        headers={"Content-Type": CONTENT_TYPE_LATEST, "Cache-Control": "no-store"},
    )
