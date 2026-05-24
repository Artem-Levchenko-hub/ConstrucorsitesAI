---
title: "API Contract (Omnia)"
tags: [api, authentication, contract, json, omnia, rest]
sources:
  - daily/2026-05-20.md
created: 2026-05-20
updated: 2026-05-20
---

# API Contract (Omnia)

The API Contract document (01-api-contract.md) defines the unified truth for all agents (A/B/C) regarding the public API. It specifies conventions for REST endpoints, data formats, authentication, and error handling, ensuring consistency across the Omnia platform.

## Key Points

- Unified truth for agents A/B/C.
- Public API endpoints under `/api/*`, preview under `/p/*`, WebSocket under `/api/ws/*`.
- JSON, UTF-8, snake_case payload format.
- ISO 8601 UTC for time, UUID v4 for identifiers.
- JWT authentication via `httpOnly` Secure cookie `omnia_session`.
- Standardized error format with `code`, `message`, and `details`.

## Details

The API Contract is a critical document that mandates coordination among agents for any changes. It establishes foundational agreements for API design, including URL prefixes for different service types (public, preview, WebSocket) and strict adherence to JSON, UTF-8, and snake_case for data payloads.

Authentication is handled via JWT tokens stored in `httpOnly` Secure cookies, ensuring security. Error responses are standardized to provide consistent feedback to clients, including a string constant code, a human-readable message, and optional detailed information, alongside appropriate HTTP status codes.

Specific REST endpoints for authentication (register, login, logout, me) are detailed, including expected request bodies, responses, and HTTP statuses. This section also outlines basic validation rules for user registration, such as email format and password strength requirements, and specifies bcrypt for password hashing.

## Related Concepts

- [[knowledge/concepts/agent-d-orchestrator-devops-v2-phase-a]]

## Sources

- [[daily/2026-05-20.md]]
