---
title: "Geo-block Circumvention via UK Proxy"
tags: [docker-compose, geo-blocking, google-api, network-configuration, proxy]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# Geo-block Circumvention via UK Proxy

Addressed Google's geo-blocking of Generative Language API for Russian IPs by routing Gemini requests through a UK-based HTTPS/SOCKS5 proxy. This was achieved by configuring container-level environment variables for proxy settings and a `NO_PROXY` whitelist.

## Key Points

- Google Generative Language API geo-blocks Russian IPs, causing `FAILED_PRECONDITION` errors.
- A UK-based HTTPS/SOCKS5 proxy was configured using `HTTPS_PROXY`/`HTTP_PROXY` environment variables.
- `NO_PROXY` whitelist ensures only `generativelanguage.googleapis.com` uses the proxy.
- Other LLM providers (Claude, GigaChat, Yandex) continue to use direct connections.
- Free-tier Gemini Pro keys have a 0 daily request limit without a billing project, forcing fallback to Flash, which then hits geo-block.

## Details

Initial attempts to use Gemini Flash from a Russian IP resulted in a `FAILED_PRECONDITION` error, indicating a geo-block. Furthermore, Gemini Pro free-tier keys have a zero-request limit without a billing project, meaning requests would fall back to Flash and then be blocked.

The solution involved setting `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY` environment variables in the `docker-compose.yml` for the gateway service. The `NO_PROXY` whitelist was carefully crafted to ensure only Google's Generative Language API traffic was routed through the UK proxy, while other services maintained direct connections, confirmed by logs.

## Related Concepts

- [[knowledge/concepts/container-network-configuration]]
- [[knowledge/concepts/api-geo-restrictions]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/end-to-end-validation-of-new-features]]
