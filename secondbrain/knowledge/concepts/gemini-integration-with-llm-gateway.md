---
title: "Gemini Integration with LLM Gateway"
tags: [api-gateway, configuration, gemini, llm-integration, pricing]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# Gemini Integration with LLM Gateway

Integration of Google Gemini 2.5 (Pro and Flash) into the LLM Gateway, including pricing, model routing, API key handling, and fallback mechanisms. This involved modifying several core files to support Gemini models and ensure their availability through the gateway.

## Key Points

- Added Gemini 2.5 Pro and Flash models to pricing and model metadata.
- Implemented specific routing logic for Gemini models using `gemini/` prefix.
- Configured API key handling for Gemini via `settings.gemini_api_key`.
- Updated fallback chain to prioritize Flash over Claude Haiku, removing OpenAI due to missing keys.
- Ensured public `/v1/models` endpoint correctly reports Gemini availability.

## Details

The integration involved updating `services/pricing.py` to include Gemini's token costs and context window (1,000,000 tokens). `services/litellm_router.py` was modified to correctly route requests to Gemini and handle its API key. The `_FALLBACKS` chain was adjusted to `pro → flash → claude-haiku-4-5`, removing `gpt-5-mini` due to a lack of an OpenAI key in production.

Configuration changes in `core/config.py` added `gemini_api_key: SecretStr`, and `routers/models.py` was updated to reflect Gemini's availability based on the presence of this key. Documentation in `.env.example` and test fixtures in `tests/conftest.py` and `tests/test_pricing.py` were also updated to support the new Gemini integration.

## Related Concepts

- [[knowledge/concepts/llm-gateway-model-routing]]
- [[knowledge/concepts/api-key-management]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/end-to-end-validation-of-new-features]]
- [[connections/gemini-token-budget-and-output-quality]]
