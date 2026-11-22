"""Omission delegates output policy; it does not claim an unlimited response."""

SOL_MODEL = "gpt-6.2-sol"


def is_sol_model(model: str) -> bool:
    return model.removeprefix("openai/") == SOL_MODEL


def uses_provider_output_default(model: str, max_tokens: int | None) -> bool:
    return max_tokens is None and (
        is_sol_model(model) or model in {"claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"}
    )


def output_tokens(model: str, max_tokens: int | None, *, fallback: int) -> int | None:
    if uses_provider_output_default(model, max_tokens):
        return None
    return fallback if max_tokens is None else max_tokens
