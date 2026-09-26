from typing import Any, Literal

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ErrorCode = Literal[
    "feature_disabled",
    "validation_failed",
    "unauthorized",
    "forbidden",
    "not_found",
    "rate_limited",
    "wallet_empty",
    "model_unavailable",
    "advice_unavailable",
    "internal_error",
    "conflict",
    # Why a prompt (or another claim on the project) was refused with 409. The
    # client must not guess: only `generation_active` means "a build is running".
    "generation_active",
    "restoration_active",
    "idempotency_conflict",
    "source_changed",
    # V2: orchestrator-proxy errors surfaced through apps/api/services/orchestrator_client.
    # `unavailable` = transport / 5xx / token missing (503). `rejected` = orchestrator
    # returned 4xx that we passed through (400/404/etc).
    "orchestrator_unavailable",
    "orchestrator_rejected",
    "project_empty",
    "bad_request",
    "topup_disabled",
    "too_large",
    "upload_failed",
    "no_snapshot",
    # Проект без своей ячейки: среды приложения не существует.
    "runtime_unavailable",
    "deploy_not_proven",
    "max_integration_not_found",
    "max_integration_required",
    "max_token_invalid",
    "max_api_unavailable",
    "max_api_tls_untrusted",
    "max_project_required",
    "max_deploy_required",
    "max_webhook_failed",
    "integration_not_found",
    "integration_credentials_invalid",
    "integration_credentials_corrupted",
    "integration_provider_unavailable",
    "integration_connection_failed",
    "integration_oauth_unavailable",
    "integration_oauth_state_invalid",
    "integration_request_rejected",
    "integration_request_failed",
    "integration_configuration_invalid",
    "integration_response_invalid",
    "integration_operation_unknown",
    "integration_operation_conflict",
    "max_init_data_invalid",
    "unsafe_generated_backend",
    "payment_integration_required",
    "ai_integration_required",
    "crm_integration_required",
    # Yleum account, legal and payment lifecycle.
    "max_registration_required",
    "email_verification_required",
    "legal_acceptance_required",
    "legal_version_outdated",
    "account_unavailable",
    "billing_account_not_found",
    "email_delivery_unavailable",
    "token_invalid",
    "payments_unavailable",
    "payment_provider_unavailable",
    "invalid_webhook",
    "subscription_already_active",
    "subscription_checkout_in_progress",
    "subscription_plan_not_purchasable",
    "subscription_consent_required",
    "subscription_management_unavailable",
    "subscription_entitlement_required",
    # A numeric plan limit (projects, publish slots, always-on slots) is used up.
    # `details` carries {entitlement, limit, used, plan_code, plan_version} so
    # the client can say exactly which limit and by how much.
    "entitlement_exceeded",
    "refund_unavailable",
    "refund_balance_used",
    # Вход через VK ID / Яндекс ID — routers/auth_oauth.py. Ошибки самого
    # рукопожатия (браузер приходит на callback навигацией, не fetch-ем)
    # возвращаются редиректом на /login?oauth_error=<код> с теми же кодами.
    "oauth_provider_unavailable",
    "oauth_state_invalid",
    "oauth_exchange_failed",
    "oauth_email_required",
    "oauth_ticket_invalid",
]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    body = ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": body.model_dump(exclude_none=True)},
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    body = ErrorBody(
        code="validation_failed",
        message="request validation failed",
        details={
            "errors": jsonable_encoder(
                exc.errors(),
                custom_encoder={ValueError: str},
            )
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": body.model_dump(exclude_none=True)},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    body = ErrorBody(code="internal_error", message="internal server error")
    return JSONResponse(status_code=500, content={"error": body.model_dump(exclude_none=True)})
