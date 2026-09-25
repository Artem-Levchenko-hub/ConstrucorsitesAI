"""Anthropic prompt caching — wrap the system prompt in `cache_control: ephemeral`.

Why this exists:
  apps/api builds a ~1200-line system prompt for every generation (the
  ui-ux-pro-max skill blocks, _DESIGN_KIT, _STYLE_KIT, AWWWARDS_PRINCIPLES,
  the active preset's `format_preset_block`). The same prompt rides every
  request inside one project, so caching it on the Anthropic side saves
  50-90% of input tokens after the first call (5-min TTL).

Anthropic's `cache_control` lives on individual content blocks, not at
message level, so a plain ``{"role":"system","content":"..."}`` string can't
carry it. We rewrite the system message into the structured form:

  {"role":"system","content":[{"type":"text","text":"<prompt>","cache_control":{"type":"ephemeral"}}]}

LiteLLM forwards this verbatim to Anthropic's `messages.create`. The
Anthropic provider recognises the cache block and returns
``usage.cache_creation_input_tokens`` / ``cache_read_input_tokens`` we
don't currently surface in the UI but can mine from gateway logs later.

Non-Anthropic models are left untouched. OpenAI / Gemini have their own
caching APIs (automatic for OpenAI, explicit for Gemini) we don't engage
with here — adding them is a future M.

Min-size note: Anthropic only caches blocks ≥ 1024 tokens (Sonnet/Opus) or
≥ 2048 (Haiku). Our system prompts comfortably exceed that — if a future
prompt shrinks below the floor, Anthropic silently degrades to no-op
billing (no cache write fee, no cache hit), which is fine.
"""

from __future__ import annotations

from typing import Any


def _is_anthropic(model: str) -> bool:
    """`claude-*` is the canonical Anthropic family across our slugs and
    LiteLLM's. Provider-prefixed forms (`anthropic/claude-...`) are routed
    through the same code path so we cover them too."""
    m = model.lower()
    return m.startswith("claude-") or m.startswith("anthropic/")


def apply_anthropic_cache(
    model: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with the system message marked for
    ephemeral caching when the model is Anthropic; otherwise return the
    input unchanged.

    Only the first system message is wrapped — multiple system messages
    are rare in our pipeline, and Anthropic's API treats only the first
    one as the "system" parameter anyway.

    String content is converted to ``[{type, text, cache_control}]``.
    A system message that already uses the structured form is left alone
    (caller may have set cache_control intentionally).
    """
    if not _is_anthropic(model):
        return messages

    wrapped = False
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not wrapped and msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str) and content:
                out.append(
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
                wrapped = True
                continue
            # Already structured or empty — leave it alone.
        out.append(msg)
    return out
