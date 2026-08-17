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
    "internal_error",
    "conflict",
    # V2: orchestrator-proxy errors surfaced through apps/api/services/orchestrator_client.
    # `unavailable` = transport / 5xx / token missing (503). `rejected` = orchestrator
    # returned 4xx that we passed through (400/404/etc).
    "orchestrator_unavailable",
    "orchestrator_rejected",
    # GitHub export — apps/api/src/omnia_api/routers/github.py + services/github_client.py.
    "github_not_configured",
    "github_not_connected",
    "github_state_invalid",
    "github_state_expired",
    "github_unavailable",
    "github_oauth_failed",
    "github_token_invalid",
    "github_repo_exists",
    "github_repo_failed",
    "github_push_failed",
    "github_network_error",
    "project_empty",
    "import_bad_url",
    "import_not_found",
    "import_forbidden",
    "import_empty",
    "bad_request",
    "invalid_preset",
    "topup_disabled",
    "too_large",
    "bad_image",
    "upload_failed",
    "bad_src",
    "src_not_found",
    "text_not_found",
    "element_not_found",
    "overlap",
    # Direct style-patch (1.5) — in-preview color/font edit.
    "empty_patch",
    "banned_color",
    "invalid_font",
    "no_snapshot",
    "no_index",
    # BYO-VPS (deploy_targets) + свой домен (custom_domains) —
    # routers/deploy_targets.py and routers/domains.py.
    "deploy_target_not_found",
    "deploy_target_verify_failed",
    "deploy_target_in_use",
    "deploy_target_not_verified",
    "deploy_target_switch_pending",
    "deploy_not_proven",
    "domain_not_found",
    "domain_taken",
    "domain_invalid",
    "domain_dns_mismatch",
    "domain_cert_failed",
    "hero_media_asset_not_found",
    "hero_media_plan_not_found",
    "hero_media_render_not_found",
    "hero_media_invalid_state",
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
    "max_init_data_invalid",
    "payment_integration_required",
    "crm_integration_required",
    # MAX Studio account, legal and payment lifecycle.
    "max_registration_required",
    "email_verification_required",
    "business_profile_required",
    "business_verification_required",
    "business_locked",
    "business_already_registered",
    "inn_invalid",
    "inn_kind_mismatch",
    "ogrn_invalid",
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
    "refund_unavailable",
    "refund_balance_used",
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
