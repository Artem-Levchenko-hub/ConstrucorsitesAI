"""Token counting — tiktoken for OpenAI/Anthropic-compatible, ~4 chars/token fallback.

Anthropic does not publish a tokenizer; tiktoken's `cl100k_base` is the standard
approximation. For Yandex / Qwen we fall back to character-count heuristic.
"""

from __future__ import annotations

import tiktoken

# Lazy-init to avoid loading the BPE merges file at import time.
_ENCODER: tiktoken.Encoding | None = None


def _encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def _uses_tiktoken(model_id: str) -> bool:
    return model_id.startswith(("claude-", "gpt-", "qwen-"))


def count_text_tokens(model_id: str, text: str) -> int:
    if not text:
        return 0
    if _uses_tiktoken(model_id):
        return len(_encoder().encode(text))
    # Yandex / unknown — coarse fallback
    return max(1, len(text) // 4)


def count_message_tokens(model_id: str, messages: list[dict[str, str]]) -> int:
    """Approx total input tokens for a chat completion request.

    Adds a small per-message overhead (4 tokens) consistent with OpenAI's
    cookbook estimate. Good enough for billing pre-checks.
    """
    total = 0
    for m in messages:
        total += count_text_tokens(model_id, m.get("content", "")) + 4
    return total + 2  # priming overhead per OpenAI spec
