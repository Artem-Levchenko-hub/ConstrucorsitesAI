"""Last authenticated AI outcome per project and operation, without visitor records."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from redis.exceptions import RedisError

from yleum_api.core.redis import get_redis
from yleum_api.services.runtime_nutrition_ai import MEAL_PLAN_TASK, NUTRITION_TASK

_OPERATIONS = {"ai", "meal_plan", "nutrition"}
_CODES = {
    "ok", "failed", "wallet_empty", "rate_limited", "integration_provider_unavailable",
    "integration_provider_failed", "integration_credentials_invalid",
    "integration_configuration_invalid", "ai_integration_required", "max_deploy_required",
    "integration_operation_conflict", "max_init_data_invalid", "invalid_request",
    "integration_response_invalid",
}


def ai_operation(task: object) -> str:
    if task == NUTRITION_TASK:
        return "nutrition"
    return "meal_plan" if task == MEAL_PLAN_TASK else "ai"


def _key(project_id: UUID, operation: str) -> str:
    return f"yleum:max:ai-outcome:{project_id}:{operation}"


async def record_ai_outcome(project_id: UUID, operation: str, code: str) -> None:
    if operation not in _OPERATIONS:
        return
    safe_code = code if code in _CODES else "failed"
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        async with asyncio.timeout(0.2):
            await get_redis().set(_key(project_id, operation), f"{safe_code}|{timestamp}", ex=86400)
    except (RedisError, TimeoutError, RuntimeError, ValueError):
        # Observability is best-effort. A lost signal must never retry a paid call.
        return


async def latest_ai_outcome(project_id: UUID, operation: str) -> tuple[str, str] | None:
    if operation not in _OPERATIONS:
        return None
    try:
        async with asyncio.timeout(0.2):
            data = await get_redis().get(_key(project_id, operation))
        if not isinstance(data, str):
            return None
        code, _, timestamp = data.partition("|")
        when = datetime.fromisoformat(timestamp)
        if code in _CODES and when.tzinfo is not None:
            return code, when.astimezone(UTC).isoformat(timespec="seconds")
    except (RedisError, TimeoutError, RuntimeError, ValueError):
        pass
    return None
