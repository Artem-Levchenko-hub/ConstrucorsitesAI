---
title: "Gemini Thinking Mode and Token Budget Fix"
tags: [api-parameters, gemini, litellm, llm-behavior, token-management]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# Gemini Thinking Mode and Token Budget Fix

Resolved an issue where Gemini 2.5 models would return minimal output ('От') due to an implicit 'thinking mode' consuming the token budget. The fix involved explicitly setting `max_tokens` and disabling `reasoning_effort` for Gemini calls in the LLM Gateway.

## Key Points

- Gemini's default behavior without `max_tokens` leads to 'thinking mode' consuming output budget.
- Initial Gemini Flash calls returned minimal content (1 token, 'От').
- Fixed by setting `max_tokens` to 16384 and `reasoning_effort` to 'disable' for Gemini models.
- LiteLLM maps `reasoning_effort: 'disable'` to `generationConfig.thinkingConfig.thinkingBudget: 0`.
- Ensured fix was applied to both streaming and non-streaming paths in the gateway.

## Details

Upon the first successful production call, Gemini Flash returned only 'От' (1 token), indicating that its internal 'thinking mode' was consuming the allocated token budget before generating actual text. This behavior was observed because the API gateway was not explicitly passing `max_tokens` to Gemini.

The fix involved modifying `services/litellm_router.py:acompletion` and `services/streaming.py:_litellm_stream` to set `kwargs.setdefault('max_tokens', 16384)` and `kwargs.setdefault('reasoning_effort', 'disable')` specifically for Gemini models. This ensures that Gemini allocates its budget primarily for text generation, leading to full and complete responses.

## Related Concepts

- [[knowledge/concepts/llm-output-control]]
- [[knowledge/concepts/api-gateway-parameter-handling]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/end-to-end-validation-of-new-features]]
- [[connections/gemini-token-budget-and-output-quality]]
