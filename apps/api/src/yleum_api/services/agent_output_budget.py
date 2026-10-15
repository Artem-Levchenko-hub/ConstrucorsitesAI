"""Per-response defaults; None leaves the output limit to the provider."""


def is_sonnet55_model(model: str) -> bool:
    return model.removeprefix("anthropic/") == "claude-sonnet-5.5"


def agent_output_tokens(model: str, *, fallback: int) -> int | None:
    # LLMGW publishes no numeric output maximum for this model. Omit the
    # automatic budget, without claiming the provider default is unlimited.
    return None if is_sonnet55_model(model) else fallback
