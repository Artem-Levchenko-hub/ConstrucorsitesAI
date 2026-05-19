---
title: "Claude Haiku 4.5 Integration via proxyapi.ru"
tags: [api-key-management, claude-haiku, deployment, litellm, llm-integration, proxy-api]
sources:
  - daily/2026-05-17.md
created: 2026-05-19
updated: 2026-05-19
---

# Claude Haiku 4.5 Integration via proxyapi.ru

Integration of Claude Haiku 4.5 model into the LLM gateway using proxyapi.ru as an intermediary, including routing, API key management, and configuration updates.

## Key Points

- Added `claude-haiku-4-5` to model catalog and pricing.
- Configured routing via `_PROXY_ROUTES` in `litellm_router.py`.
- Implemented per-model API key override for Haiku.
- Updated `core/config.py` with `proxyapi_api_key` and `proxyapi_base_url`.
- Verified successful chat completion and cost in production.

## Details

The integration involved modifying `apps/llm-gateway/.../pricing.py` to list the new model and configuring its routing in `services/litellm_router.py` to use `https://api.proxyapi.ru/anthropic` as the base URL. This setup leverages LiteLLM's ability to append `/v1/messages` automatically.

A crucial part was managing API keys: `_MODEL_KEY_OVERRIDE` in `routers/models.py` was used to ensure Haiku reads from `PROXYAPI_API_KEY` instead of `ANTHROPIC_API_KEY`. New configuration fields were added to `core/config.py` to support this.

Deployment involved updating the production `.env` with the new model as default and the proxy API credentials. Verification confirmed the model's availability via `/v1/models` and successful, cost-effective chat completions in a production environment.

## Related Concepts

- [[knowledge/concepts/knowledgeconceptsproxyapi-anthropic-route]]

## Sources

- [[daily/2026-05-17.md]]

## Backlinks

- [[connections/haiku-integration-and-zero-files-fix-synergy]]
