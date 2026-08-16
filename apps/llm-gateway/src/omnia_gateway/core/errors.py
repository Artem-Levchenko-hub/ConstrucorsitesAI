"""Domain exceptions mapped to HTTP error codes from docs/01-api-contract.md.

R-08 (ubiquitous language): error codes match the contract verbatim
(`wallet_empty`, `model_unavailable`, …) so frontend / backend / gateway speak
one language.
"""

from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailedError(GatewayError):
    code = "validation_failed"
    http_status = 400


class WalletEmptyError(GatewayError):
    code = "wallet_empty"
    http_status = 402


class ModelNotFoundError(GatewayError):
    code = "model_not_found"
    http_status = 404


class ModelUnavailableError(GatewayError):
    code = "model_unavailable"
    http_status = 503


class UpstreamProviderError(GatewayError):
    code = "model_unavailable"
    http_status = 502
