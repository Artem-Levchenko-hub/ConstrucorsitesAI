---
title: "Wiki Taxonomy and Link Conventions"
tags: [secondbrain, conventions, taxonomy]
sources:
  - secondbrain/AGENTS.md
  - secondbrain/README.md
created: 2026-04-12
updated: 2026-04-12
tier: archive
---

# Wiki Taxonomy and Link Conventions

## Purpose

Define a stable authoring contract for `secondbrain/knowledge/*` so project memory stays compact, searchable, and reusable across sessions.

## Taxonomy

- `knowledge/concepts/`: atomic technical knowledge (one durable topic per page).
- `knowledge/connections/`: cross-topic synthesis (must connect at least two concept pages).
- `knowledge/qa/`: query-shaped answers and operational FAQ notes.
- `knowledge/project-context.md`: compact master context for fast startup orientation.
- `knowledge/index.md`: catalog of significant pages.
- `knowledge/log.md`: chronological ingest/query/maintenance history.

## Link Conventions

- Use canonical wiki links: `[[knowledge/concepts/<slug>]]`, `[[knowledge/connections/<slug>]]`, `[[qa/<slug>]]`.
- Prefer stable slugs; avoid creating timestamped variants unless content is meaningfully different.
- Every concept should link to related concepts or at least one connection page.
- Every connection page should list linked concepts explicitly.

## Ingest Rules

- Add wiki entries only for durable changes: architecture, contracts, invariants, security policies, debugging playbooks.
- Do not create noisy pages for trivial edits (formatting, copy tweaks, tiny renames).
- For impactful work, update:
  - concept/connection pages,
  - `knowledge/index.md`,
  - `knowledge/log.md` with `ingest | ...`.

## Exclusions for Full-Project Ingest

- Exclude generated/vendor/build artifacts:
  - `node_modules/`, frontend `dist/`, `__pycache__/`, local virtualenv folders.
  - build outputs under mobile toolchains (`android/app/build/`, similar).
  - binary/vendor bundles (for example backend wheels cache).

