"""Token counting — tiktoken for OpenAI/Anthropic-compatible, ~4 chars/token fallback.

Anthropic does not publish a tokenizer; tiktoken's `cl100k_base` is the standard
approximation. For Yandex / Qwen we fall back to character-count heuristic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import structlog
import tiktoken

log = structlog.get_logger(__name__)

# Lazy-init to avoid loading the BPE merges file at import time.  On a fresh
# host tiktoken may need to download that file.  Token accounting must never
# turn an otherwise successful LLM response into a failed/stuck generation, so
# remember a failed load and use the existing character heuristic instead.
_ENCODER: tiktoken.Encoding | None = None
_ENCODER_UNAVAILABLE = False


def _encoder() -> tiktoken.Encoding | None:
    global _ENCODER, _ENCODER_UNAVAILABLE
    if _ENCODER is None and not _ENCODER_UNAVAILABLE:
        try:
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:
            # Billing uses an estimate here, not a security boundary.  Fail soft
            # when the tokenizer asset/CDN is unavailable and avoid retrying the
            # same slow network request for every streamed completion.
            _ENCODER_UNAVAILABLE = True
            log.warning(
                "tokenizer.asset_unavailable",
                error_type=type(exc).__name__,
                fallback="character_estimate",
            )
    return _ENCODER


def _uses_tiktoken(model_id: str) -> bool:
    return model_id.startswith(("claude-", "gpt-", "qwen-"))


def count_text_tokens(model_id: str, text: str) -> int:
    if not text:
        return 0
    if _uses_tiktoken(model_id):
        encoder = _encoder()
        if encoder is not None:
            return len(encoder.encode(text))
    # Yandex / unknown — coarse fallback
    return max(1, len(text) // 4)


def _content_text(content: object) -> str:
    """Flatten chat-message content to plain text for token estimation.

    Multimodal content is a list of blocks ({"type":"text","text":...} /
    {"type":"image_url",...}). We count only the text parts; image bytes are
    billed separately by the provider, so ignoring them here keeps the
    pre-flight estimate from crashing on list content (Phase 11 vision audit).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(parts)
    return str(content)


def count_message_tokens(
    model_id: str, messages: Sequence[Mapping[str, object]]
) -> int:
    """Approx total input tokens for a chat completion request.

    Adds a small per-message overhead (4 tokens) consistent with OpenAI's
    cookbook estimate. Good enough for billing pre-checks.
    """
    total = 0
    for m in messages:
        total += count_text_tokens(model_id, _content_text(m.get("content", ""))) + 4
    return total + 2  # priming overhead per OpenAI spec
