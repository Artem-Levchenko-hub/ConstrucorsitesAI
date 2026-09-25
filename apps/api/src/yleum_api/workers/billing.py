"""Standalone billing worker: `python -m yleum_api.workers.billing`.

Runs the billing tick (subscription lifecycle + reconciliation of open orders)
without the RQ queue, MinIO or Playwright, and answers `GET /health` on
`BILLING_WORKER_HEALTH_PORT` for Kubernetes probes and operators. This is the
process the commerce cluster runs (infra/max-k3s/k8s/commerce); on core the
same tick still runs inside the RQ worker until `BILLING_LIFECYCLE_ENABLED=false`
hands it over.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from yleum_api.core.config import get_settings
from yleum_api.core.release import normalize_release_sha
from yleum_api.services.billing_cycle import (
    BILLING_HEARTBEAT_KEY,
    BillingCycleState,
    run_billing_cycles_forever,
)

log = structlog.get_logger(__name__)


def build_health_app(state: BillingCycleState, *, release_sha: str) -> FastAPI:
    """200 while ticks complete on schedule, 503 once they stall or keep failing."""
    app = FastAPI(title="omnia-billing-worker", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        now = datetime.now(UTC)
        healthy = state.healthy(now)
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ok" if healthy else "degraded",
                "service": "billing-worker",
                "release_sha": release_sha,
                **state.snapshot(now),
            },
        )

    return app


async def serve(state: BillingCycleState) -> None:
    settings = get_settings()
    release_sha = normalize_release_sha(settings.omnia_release_sha)
    server = uvicorn.Server(
        uvicorn.Config(
            build_health_app(state, release_sha=release_sha),
            host="0.0.0.0",  # pod-internal probe port, never published
            port=settings.billing_worker_health_port,
            log_level="warning",
            access_log=False,
        )
    )
    log.info(
        "billing_worker.started",
        health_port=settings.billing_worker_health_port,
        release_sha=release_sha,
    )
    health_task = asyncio.create_task(server.serve(), name="billing-health")
    cycle_task = asyncio.create_task(
        run_billing_cycles_forever(heartbeat_key=BILLING_HEARTBEAT_KEY, state=state),
        name="billing-cycles",
    )
    # uvicorn owns SIGTERM/SIGINT: when it stops, the loop stops with it, so a
    # pod termination ends the process instead of waiting for the kill.
    done, pending = await asyncio.wait(
        {health_task, cycle_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


def main() -> None:
    state = BillingCycleState(poll_seconds=get_settings().billing_lifecycle_poll_seconds)
    asyncio.run(serve(state))


if __name__ == "__main__":
    main()
