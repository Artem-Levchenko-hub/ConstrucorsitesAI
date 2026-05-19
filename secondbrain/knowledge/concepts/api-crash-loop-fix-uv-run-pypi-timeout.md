---
title: "API Crash-Loop Fix (uv run pypi timeout)"
tags: [containerization, deployment, docker, production-stability, python, troubleshooting, uv, uvicorn]
sources:
  - daily/2026-05-19.md
created: 2026-05-19
updated: 2026-05-19
---

# API Crash-Loop Fix (uv run pypi timeout)

Resolved a production API crash-loop caused by `uv run` attempting to resolve packages from pypi.org on every startup, leading to timeouts and container failures.

## Key Points

- Identified `uv run`'s network dependency on pypi.org as the root cause.
- Replaced `uv run` with direct execution of `uvicorn` and `python` commands.
- Eliminated network requests during container startup.
- Significantly reduced container startup time from 50 seconds to 2 seconds.

## Details

The `omnia-prod-api` container was entering a crash-loop after `docker compose up -d --build web`, with logs indicating `Failed to resolve hatchling from pypi.org — operation timed out`. This pointed to `uv run`'s behavior of re-validating the lock file against pypi.org on each invocation.

In a production environment with intermittent network issues, this validation process would frequently time out after 50 seconds, causing the container to restart repeatedly. This made the API service unstable and unreliable.

The fix involved overriding the `command:` in `apps/llm-gateway/deploy/full/docker-compose.yml` for both the API and worker services. Instead of `uv run uvicorn ...` and `uv run python ...`, the commands were changed to directly execute the installed binaries: `/app/.venv/bin/uvicorn ...` and `/app/.venv/bin/python -m omnia_api.workers.run`.

This change bypassed the `uv run` wrapper's network checks, allowing the containers to start up almost instantly (within 2 seconds) without any external network dependencies. This significantly improved the stability and deployment speed of the API and worker services.

## Related Concepts

- [[knowledge/concepts/llm-gateway-architecture]]
- [[knowledge/concepts/backend-agent-brief]]

## Sources

- [[daily/2026-05-19.md]]

## Backlinks

- [[connections/api-stability-and-performance-directly-impacts-user-experience-and-platform-reliability]]
- [[connections/deployment-stability-and-startup-performance]]
- [[connections/monorepo-structure-and-agent-briefs-guide-development-and-maintain-consistency]]
