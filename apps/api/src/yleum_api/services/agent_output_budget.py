"""Per-response defaults; None leaves the output limit to the provider."""

from typing import Final

KIMI_PILOT_MODEL: Final = "kimi-k3.1-pro"
SOL_MODEL: Final = "gpt-6.2-sol"

SONNET55_REPAIR_OUTPUT_TOKENS = 128_000
DEFAULT_REPAIR_OUTPUT_TOKENS = 8192


def is_sonnet55_model(model: str) -> bool:
    return model.removeprefix("anthropic/") == "claude-sonnet-5.5"


def is_sol_model(model: str) -> bool:
    return model.removeprefix("openai/") == SOL_MODEL


def agent_output_tokens(model: str, *, fallback: int) -> int | None:
    # LLMGW publishes no numeric output maximum for this model. Omit the
    # automatic budget, without claiming the provider default is unlimited.
    return None if is_sonnet55_model(model) or is_sol_model(model) else fallback


def repair_output_tokens(model: str) -> int | None:
    if is_sol_model(model):
        return None
    return (
        SONNET55_REPAIR_OUTPUT_TOKENS if is_sonnet55_model(model)
        else DEFAULT_REPAIR_OUTPUT_TOKENS
    )
