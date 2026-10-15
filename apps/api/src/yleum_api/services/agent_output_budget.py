"""Per-response output defaults shared by generation and source repair."""


def is_sonnet55_model(model: str) -> bool:
    return model.removeprefix("anthropic/") == "claude-sonnet-5.5"


def agent_output_tokens(model: str, *, fallback: int) -> int:
    # Ordinary API maximum, not the extended Batch API limit:
    # https://platform.claude.com/docs/en/models/sonnet-5-5/overview
    # The intermediary's acceptance and long-response latency need live verification.
    return 128000 if is_sonnet55_model(model) else fallback
