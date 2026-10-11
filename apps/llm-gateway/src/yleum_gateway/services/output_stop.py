"""Validate output separately from its billable receipt; settle before rejecting."""

from typing import Any

from yleum_gateway.core.errors import GatewayError


class ProviderOutputError(GatewayError):
    http_status = 502

    def __init__(self, *, filtered: bool = False) -> None:
        self.code = "provider_output_filtered" if filtered else "provider_output_invalid"
        super().__init__(
            "Provider output was filtered"
            if filtered
            else "Provider output has no supported completion reason",
            details={"retry_safe": False},
        )


def output_stop_error(reason: object) -> ProviderOutputError | None:
    if isinstance(reason, str) and reason in {"stop", "tool_calls", "length"}:
        return None
    return ProviderOutputError(filtered=reason == "content_filter")


def completion_finish_reason(response: dict[str, Any]) -> object:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason")
    return None
