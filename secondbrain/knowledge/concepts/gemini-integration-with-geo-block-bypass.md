---
title: "Gemini Integration with Geo-Block Bypass"
tags: [api-configuration, docker-compose, geo-blocking, google-gemini, litellm, llm-integration, proxy]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# Gemini Integration with Geo-Block Bypass

Successfully integrated Google Gemini 2.5 (Pro + Flash) into the LLM Gateway, overcoming Google's geo-blocking for Russian IPs using a UK proxy and optimizing Gemini's response behavior.

## Key Points

- Added Gemini 2.5 Pro and Flash models to LLM Gateway pricing and routing.
- Implemented a UK proxy for `generativelanguage.googleapis.com` to bypass geo-blocking.
- Configured `NO_PROXY` whitelist to route only Gemini traffic through the proxy.
- Adjusted `max_tokens` and `reasoning_effort` for Gemini to prevent 'thinking mode' from consuming output.
- Verified end-to-end functionality and billing for Gemini Flash on a live project.

## Details

The integration involved updating `pricing.py` with Gemini model costs and context windows, modifying `litellm_router.py` to handle Gemini API keys and fallbacks, and adding `gemini_api_key` to `core/config.py`. Initial tests revealed Google's geo-blocking for Russian IPs, necessitating a proxy solution.

A UK-based HTTPS/SOCKS5 proxy was configured via `docker-compose.yml` environment variables (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`). The `NO_PROXY` whitelist was crucial to ensure only Gemini API calls were routed through the proxy, while other LLM providers continued direct connections.

A peculiar 'thinking mode' behavior of Gemini 2.5, where it consumed tokens for reasoning without generating text, was identified. This was resolved by explicitly setting `max_tokens` to a higher value (16384) and disabling `reasoning_effort` (mapping to `thinkingBudget: 0`) in LiteLLM's `acompletion` and `_litellm_stream` functions.

The successful integration was validated on a live project, demonstrating full HTML generation with branding and accurate billing for Gemini Flash usage. This confirmed the geo-block bypass and output optimization were effective.

## Related Concepts

- [[knowledge/concepts/llm-gateway-architecture]]
- [[knowledge/concepts/api-contract]]
- [[knowledge/concepts/monetization-plan]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/gemini-integration-and-geo-blocking-solution-enables-new-llm-capabilities-for-users]]
- [[connections/monorepo-structure-and-agent-briefs-guide-development-and-maintain-consistency]]
