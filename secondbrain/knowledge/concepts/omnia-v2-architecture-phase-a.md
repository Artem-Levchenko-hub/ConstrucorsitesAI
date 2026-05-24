---
title: "Omnia V2 Architecture (Phase A)"
tags: [ai, architecture, drizzle, full-stack, nextjs, postgres, runtime, v2]
sources:
  - daily/2026-05-20.md
created: 2026-05-20
updated: 2026-05-20
---

# Omnia V2 Architecture (Phase A)

The V2 Architecture document (07-v2-architecture.md) outlines the transition from Omnia.AI V1's static site generation to V2's full-stack product development, focusing on Phase A: Next.js + Postgres + Drizzle web applications. It details changes in AI artifacts, code storage, live preview, and runtime environments.

## Key Points

- Transition from V1 static sites to V2 full-stack products.
- Phase A focuses on Next.js 15, Postgres, and Drizzle web applications.
- AI artifacts evolve from HTML/CSS/JS files to complete applications (package.json, src/, Dockerfile).
- Code storage includes bare git repos in MinIO and Postgres schema-per-project.
- Live preview shifts from static iframes to real Next.js dev servers.
- Runtime introduces persistent dev-containers that live between prompts.

## Details

Omnia.AI V1 generated static websites, serving files via FastAPI. V2 significantly expands this capability to generate and manage full-stack applications. Phase A specifically targets web applications built with Next.js 15, Postgres, and Drizzle, laying the groundwork for future phases like Telegram/Discord bots and custom mobile apps.

Key changes include the nature of AI-generated artifacts, which transform from simple HTML files into complete application structures, including `package.json`, source code directories, database migrations, and Dockerfiles. This enables the AI to produce deployable, dynamic applications.

The storage mechanism evolves to support these complex artifacts, combining bare Git repositories in MinIO with a schema-per-project approach in Postgres for database management. The live preview experience is enhanced, moving from static iframes to dynamic previews served by actual Next.js development servers, providing a more accurate representation of the running application.

A crucial runtime change is the introduction of persistent development containers. Unlike V1, where Playwright rendered PNGs once, V2's dev-containers remain active between prompts, allowing for iterative development and a more interactive AI-driven development workflow.

## Related Concepts

- [[knowledge/concepts/vps-setup-for-v2-phase-a-runtime]]
- [[knowledge/concepts/agent-d-orchestrator-devops-v2-phase-a]]

## Sources

- [[daily/2026-05-20.md]]

## Backlinks

- [[concepts/vps-setup-for-omnia-v2-phase-a-runtime]]
