# Fullstack template (Next.js 15 + Postgres + Drizzle)

This is the **api-side mirror** of the orchestrator's
`apps/orchestrator/templates/nextjs-postgres-drizzle/` template. The two
copies serve different roles:

- **api-side** (this directory) — files committed to the project's git
  repo on creation. Drives `current_files` in the LLM context so the AI
  sees the real stack from the first prompt.
- **orchestrator-side** — files baked into the
  `omnia-template-nextjs-postgres-drizzle:dev` Docker image. Drives what
  the dev container actually serves.

We only mirror files the AI needs to **understand the stack and write
matching code**: a starter page, the Drizzle schema scaffold, and this
README. Boilerplate the AI must never touch (Dockerfile.*, package.json,
tsconfig.json, drizzle.config.ts, next.config.ts) deliberately stays on
the orchestrator side and isn't visible to the LLM.

If you change the orchestrator template structure, mirror the *visible*
files here so the AI's mental model stays in sync.
