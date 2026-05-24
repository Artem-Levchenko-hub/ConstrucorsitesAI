---
title: "Agent D — Orchestrator + DevOps (V2 Phase A)"
tags: [agent, devops, docker, internal-api, nginx, orchestrator, postgres, v2]
sources:
  - daily/2026-05-20.md
created: 2026-05-20
updated: 2026-05-20
---

# Agent D — Orchestrator + DevOps (V2 Phase A)

Agent D is the Orchestrator and DevOps agent responsible for the V2 runtime plane, including per-project Docker containers, build pipelines, Nginx auto-configuration, and Postgres schema isolation. It operates the `omnia-orchestrator` service, listening on `:8003` and interacting with `apps/api` via an internal token.

## Key Points

- Owner of the V2 runtime plane: Docker containers, build pipelines, Nginx config, Postgres isolation.
- Service named `omnia-orchestrator`, located in `apps/orchestrator/`.
- Listens on host port `:8003`, accepts requests from `apps/api` with `X-Internal-Token`.
- Strictly limited to modifying `apps/orchestrator/` and `infra/`.
- Cannot modify `apps/web/`, `apps/api/`, `apps/llm-gateway/`.
- Coordinates with Agent B for public API extensions related to V2.

## Details

Agent D is a new addition to the Omnia team, complementing existing agents A/B/C by focusing exclusively on the V2 runtime infrastructure. Its core responsibility is to manage the lifecycle of per-project Docker containers, implement hibernation timers, orchestrate build pipelines for deployment, and configure Nginx for routing, ensuring project isolation through dedicated Postgres schemas.

The `omnia-orchestrator` service, developed by Agent D, is a critical component of the V2 architecture. It runs on the VPS host, listening on port `:8003`, and is designed to be an internal service, accepting requests only from `apps/api` and authenticated via a shared `X-Internal-Token` secret. This internal communication pattern ensures secure and controlled interactions within the Omnia ecosystem.

Agent D operates under strict boundaries: it is only permitted to modify code within `apps/orchestrator/` and `infra/` (for Docker Compose and Nginx templates). It is explicitly forbidden from touching `apps/web/`, `apps/api/`, or `apps/llm-gateway/`. Any necessary public API extensions for V2 must be coordinated with Agent B, who will implement the proxying endpoints in `apps/api`.

The internal API contract for Agent D's service is defined in `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`. Any changes to this contract require coordination, ensuring that `apps/api` remains compatible. This structured approach to agent responsibilities and API contracts is vital for maintaining a coherent and scalable system.

## Related Concepts

- [[knowledge/concepts/omnia-v2-architecture-phase-a]]
- [[knowledge/concepts/vps-setup-for-v2-phase-a-runtime]]
- [[knowledge/concepts/api-contract-omnia]]

## Sources

- [[daily/2026-05-20.md]]

## Backlinks

- [[concepts/vps-setup-for-omnia-v2-phase-a-runtime]]
