# AGENTS.md — Omnia.AI SecondBrain Schema

> Inspired by [Andrej Karpathy's LLM Knowledge Base](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
> and the [claude-memory-compiler](https://github.com/coleam00/claude-memory-compiler) reference implementation.
> Knowledge is compiled from Claude Code / Cursor sessions, project docs, and Claude Code auto-memory —
> not re-derived on every query.

This port is direct from the CorporateMessanger / Innertalk codebase; the
pipeline and contracts are identical, only the domain scope and trigger
paths are Omnia-specific.

---

## Lineage

This wiki combines three complementary patterns:

| Pattern | What it contributes |
|---------|---------------------|
| **LLM Wiki** (Karpathy) | The persistent, compounding artefact — the wiki is never re-derived, only maintained. Three layers: raw / wiki / schema. |
| **claude-memory-compiler** (coleam00) | The pipeline: editor hooks automatically flush transcripts into `daily/`, then `compile.py` turns them into `knowledge/` articles. Index-guided retrieval, no RAG. |
| **Claude Code auto-memory** | A parallel raw source: `~/.claude/projects/<hash>/memory/MEMORY.md` + `feedback_*.md`, edited live during sessions. `sync_memory.py` bridges it into the daily log. |

All three layers are **repo-local** — `secondbrain/` lives inside `omnia-mvp/` so
`git clone` gives the team a working wiki on day one.

---

## The Compiler Analogy

```
daily/          = source code    (conversations + doc + memory ingest)
LLM             = compiler       (extracts, organises, cross-references)
knowledge/      = executable     (structured, queryable wiki)
lint            = test suite     (health checks for consistency)
queries         = runtime        (using the knowledge)
```

You don't manually organise knowledge. Sessions happen, hooks fire, and the LLM handles synthesis.

---

## Storage Model

**Git-versioned** (team-shared):

- `knowledge/` — compiled wiki (`index.md`, `log.md`, `concepts/`, `connections/`, `qa/`)
- `daily/YYYY-MM-DD.md` — append-only raw logs (`merge=union` eases conflicts)
- `raw/` — immutable snapshots of external sources
- `scripts/`, `hooks/`, `templates/` — the compiler and its helpers
- `AGENTS.md`, `README.md` — schema + quick start

**Git-ignored** (machine-local state, see `.gitignore`):

- `scripts/state.json`, `last-flush.json`, `docs-state.json`, `memory-state.json`,
  `maintenance-state.json`, `last-memory-sync.json`, `context-dirty.json`
- `scripts/*.log`, `scripts/tmp/`, `reports/`, `.venv/`

---

## Layers

### Layer 1 — `daily/` (Immutable Source)

Append-only. Never edited after the fact.

```
daily/
├── 2026-05-17.md
├── 2026-05-18.md
└── ...
```

Each file has sections for `## Sessions`, `## Docs Ingest`, `## Memory Maintenance`.

### Layer 2 — `knowledge/` (Compiled, LLM-Owned)

```
knowledge/
├── index.md              # Master catalog
├── log.md                # Append-only build/query/lint log
├── project-context.md    # Persistent project map (injected every session)
├── concepts/             # Atomic knowledge articles
├── connections/          # Cross-concept synthesis
└── qa/                   # Filed query answers
```

### Layer 3 — `AGENTS.md` (Schema)

This file. The contract the LLM follows when compiling and querying.

---

## Claude Code Auto-Memory Bridge

Claude Code maintains a file-based memory per project at:

```
~/.claude/projects/<project-path-hash>/memory/
├── MEMORY.md                           # Index (injected into every session)
├── user_*.md                           # Who the user is
├── feedback_*.md                       # Corrections / confirmed approaches
├── project_*.md                        # Ongoing initiatives
└── reference_*.md                      # Pointers to external systems
```

The `<project-path-hash>` is derived by replacing `:`, `/`, `\\` with `-`
(e.g. `C:\\Бизнес план\\omnia-mvp` → `C--------------omnia-mvp`).
`scripts/sync_memory.py` auto-detects this; override with `CLAUDE_CODE_MEMORY_DIR` if needed.

### Flow 1 — Inject at session start
`hooks/session-start.py` reads `MEMORY.md` and prepends it to `additionalContext`.
Every new session sees **both** the auto-memory index **and** the SecondBrain `project-context.md`.

### Flow 2 — Bridge into daily log
`scripts/sync_memory.py`:
1. Hashes every `*.md` in the memory dir (SHA-256, skips `MEMORY.md` — it's injected live).
2. Appends a `### Memory Sync (HH:MM)` block to today's `daily/YYYY-MM-DD.md`
   for each changed file, categorised (`Feedback` / `User Profile` / `Project` / …).
3. Stores hashes in `scripts/memory-state.json` to dedupe across runs.

Trigger modes:
- **Automatic**: `session-start.py` fires it as a background subprocess
  **at most once per UTC date** (marker: `scripts/last-memory-sync.json`).
- **Manual**: `uv run python scripts/sync_memory.py [--dry-run|--force]`.

### Flow 3 — Compile into wiki
After 18:00 local, `flush.py` triggers `compile.py`, which converts the memory
entries (now part of the daily log) into concept / connection / qa articles
with proper wikilinks. After this, recalling the lesson from the wiki
(via `/sb-query`) is preferred over re-reading `feedback_*.md`.

### What stays in auto-memory vs. wiki

| Auto-memory (`memory/`) | Wiki (`knowledge/`) |
|-------------------------|---------------------|
| User identity, preferences, short corrections | Compiled, linked, encyclopedia-style knowledge |
| Updated in seconds during a session | Updated after compile pass |
| Small (<2KB/file) | Can be large, detailed, cross-referenced |
| One file = one rule or fact | One file = one topic / concept |
| Read every session (injected into context) | Read when wiki-relevant (via `/sb-query` or session-start) |
| Machine-local (each dev's own memory) | Repo-local (whole team shares after `git pull`) |

When a memory crystallises into a stable, multi-facet topic, promote it to a
concept article. The memory file can then be shortened to a pointer.

---

## Domain Scope (Omnia.AI)

When compiling, prioritise topics relevant to Omnia.AI's three-agent architecture:

### Agent A — Frontend (`apps/web/`)
- Stack: Next.js 15 App Router, React 19, TypeScript, Tailwind v4, shadcn/ui, framer-motion, React Query 5
- Workspace UI: `src/components/workspace/` — `ChatPanel`, `PreviewFrame`, `StreamingPreviewFrame`, `CodeView`, `Timeline`, `SnapshotCard`, `PromptInput`, `ModelSelector`
- Auth.js (NextAuth) with JWT, cookies, project-aware sessions
- Real-time streaming preview via morphdom postMessage (no iframe reload between LLM chunks)
- `src/lib/parse-assistant.ts` — frontend parser mirror of backend `file_extractor.py`
- `src/store/workspace.ts` — Zustand store: selected model, snapshot, view mode (preview/code)
- `src/hooks/usePromptStream.ts` — WS subscription + prompt queue + cancel
- See [`docs/03-design-system.md`](../docs/03-design-system.md), [`agents/AGENT-A-FRONTEND.md`](../agents/AGENT-A-FRONTEND.md)

### Agent B — Backend (`apps/api/`)
- Stack: FastAPI (Python 3.12), SQLAlchemy + asyncpg, Alembic, Redis, MinIO, pygit2, Playwright (preview render), RQ workers
- `src/omnia_api/routers/messages.py` — `POST /api/projects/:id/prompt` orchestrator (LLM stream → file extract → git commit → snapshot → preview enqueue → WS broadcast)
- `src/omnia_api/services/{file_extractor,prompt_builder,repo,billing,llm_client}.py` — pipeline кирпичи
- `src/omnia_api/workers/preview.py` — Playwright headless preview render → MinIO PNG
- `src/omnia_api/routers/ws.py` + `services/ws_hub.py` — WebSocket distribution (`llm.chunk`, `llm.done`, `llm.error`, `snapshot.created`, `preview.ready`, `wallet.updated`)
- Per-project git repo in MinIO (`projects/<uuid>.git.tar.gz`)
- See [`docs/01-api-contract.md`](../docs/01-api-contract.md), [`agents/AGENT-B-BACKEND.md`](../agents/AGENT-B-BACKEND.md)

### Agent C — LLM Gateway (`apps/llm-gateway/`)
- Stack: FastAPI + LiteLLM + Redis (cache) + Postgres (billing). Custom providers for Yandex (`providers/yandex.py`) and Sber GigaChat OAuth (`providers/sber.py`).
- `services/litellm_router.py` — model catalog, slug map, `_PROXY_ROUTES` override (Haiku via proxyapi.ru → `anthropic/...` slug + custom `api_base`)
- `services/pricing.py` — RUB price table (`PRICE_TABLE` + `_MODEL_META`); single source of truth for `/v1/models` and billing
- `services/streaming.py` — SSE-aware streaming with pseudo-streaming fallback for non-streaming providers (Sber, Yandex)
- `routers/models.py` — `/v1/models` with `available: bool` per provider key in env + `_MODEL_KEY_OVERRIDE` for model-specific keys
- See [`agents/AGENT-C-LLM-GATEWAY.md`](../agents/AGENT-C-LLM-GATEWAY.md)

### Infra (`infra/`, `apps/llm-gateway/deploy/full/`)
- Local: `docker-compose.yml` in `infra/` — Postgres, Redis, MinIO, api, worker, plus separate llm-gateway demo compose
- Prod: `apps/llm-gateway/deploy/full/docker-compose.yml` on VPS Serverum (170.168.72.200, `i48ptgvnis@`), reverse-proxied via nginx, public domain `constructor.lead-generator.ru` (planned permanent: `omnia.ai`)
- `.env` lives only on VPS — secrets never in git

### Cross-cutting
- Decisions and their rationale (why X over Y — e.g. tool-calling vs XML parsing for file output, srcDoc reload vs morphdom diff)
- Regressions, bugs, post-mortems (e.g. 0-files silent failure with GigaChat, hairpin-NAT on VPS)
- Integration patterns (WebSocket ↔ REST, frontend ↔ backend ↔ gateway contracts in `docs/01-api-contract.md`)

---

## Article Formats

### Concept Articles (`knowledge/concepts/`)

```markdown
---
title: "Concept Name"
aliases: [alternate-name]
tags: [backend, fastapi]
sources:
  - "daily/2026-05-17.md"
created: 2026-05-17
updated: 2026-05-17
---

# Concept Name

[2–4 sentence core explanation]

## Key Points

- [Self-contained bullet]

## Details

[Encyclopedia-style deeper explanation, 2+ paragraphs.]

## Related Concepts

- [[concepts/related-concept]] — How it connects

## Sources

- [[daily/2026-05-17.md]] — Initial discovery
```

### Connection Articles (`knowledge/connections/`)

Cross-cutting synthesis linking 2+ concepts. Created when a session reveals a non-obvious relationship.

### Q&A Articles (`knowledge/qa/`)

Filed answers from `query.py --file-back`.

---

## Core Operations

### 1. Compile (`daily/` → `knowledge/`)

1. Read the daily log
2. Read `knowledge/index.md` for current state
3. Read existing articles likely to need updating
4. For each knowledge unit:
   - **Existing concept covers it** → UPDATE with new info, add source
   - **New topic** → CREATE new `concepts/` article
5. If log reveals non-obvious cross-concept link → CREATE `connections/` article
6. UPDATE `knowledge/index.md`
7. APPEND to `knowledge/log.md`

**Rules:**
- One daily log touches 3–10 articles max
- Prefer updating over creating near-duplicates
- Use `[[wikilinks]]` with full relative paths
- Every article: YAML frontmatter + link back to source daily log

### 2. Query (`query.py`)

1. Read `knowledge/index.md`
2. Identify 3–10 relevant articles from the index
3. Read those articles in full
4. Synthesise answer with `[[wikilink]]` citations
5. If `--file-back`: create `knowledge/qa/` article, update index and log

**Why no RAG:** at this KB size (50–500 articles), the LLM reading a structured index outperforms cosine similarity.

### 3. Lint (`lint.py`)

Seven checks:

1. **Broken links** — `[[wikilinks]]` pointing to non-existent articles
2. **Orphan pages** — Articles with zero inbound links
3. **Orphan sources** — Daily logs not yet compiled
4. **Stale articles** — Source log changed since last compile
5. **Contradictions** — Conflicting claims (LLM check; disable via `--structural-only`)
6. **Missing backlinks** — A links to B but B doesn't link back
7. **Sparse articles** — Under 200 words, likely incomplete

Reports saved to `reports/lint-YYYY-MM-DD.md` (gitignored).

---

## Hook System (Automatic Capture)

Hooks are registered in `.claude/settings.json` with project-relative paths so
every clone works out of the box.

### `session-start.py` (SessionStart)
- Pure local I/O, <1s
- Injects: `MEMORY.md`, `project-context.md`, full concept articles, tail of most recent daily log
- **Side-effect**: spawns `sync_memory.py` once per UTC date

### `session-end.py` (SessionEnd) & `pre-compact.py` (PreCompact)
- Spawn `flush.py` to extract the transcript into today's daily log
- Set `CLAUDE_INVOKED_BY` to prevent recursion
- After 18:00 local, `flush.py` also spawns `compile.py`

### `post-tool-use.py` (PostToolUse)
- Updates `knowledge/live-activity.md`, marks `context-dirty.json` when files matching CONTEXT_TRIGGERS are touched (Omnia-specific path list: `apps/{web,api,llm-gateway}/...`, `infra/`, `docs/`, `agents/`)
- No LLM calls — pure local I/O, <50ms

### `after-agent-response.py` (Stop)
- Lightweight bookkeeping after each agent response

---

## Scripts Reference

| Script | Purpose | CLI |
|--------|---------|-----|
| `compile.py` | Compile daily logs → knowledge articles | `uv run python scripts/compile.py` |
| `query.py` | Ask questions (index-guided, no RAG) | `uv run python scripts/query.py "question"` |
| `lint.py` | 7 health checks | `uv run python scripts/lint.py` |
| `flush.py` | Extract session transcript → daily log | auto-spawned by hooks |
| `sync_project_docs.py` | Ingest changed project markdown | `uv run python scripts/sync_project_docs.py` |
| `sync_memory.py` | Ingest Claude Code auto-memory files | `uv run python scripts/sync_memory.py` |
| `update_context.py` | Regenerate `project-context.md` after arch changes | `uv run python scripts/update_context.py` |
| `init_workspace.py` | First-time workspace init | `uv run python scripts/init_workspace.py` |
| `maintenance.py` | Once-per-day orchestration (sync → compile → lint) | `uv run python scripts/maintenance.py` |
| `ingest_web.py` | Ingest external URL into `raw/web/` | `uv run python scripts/ingest_web.py URL` |
| `check_staleness.py` | Verify wiki vs daily-log freshness | `uv run python scripts/check_staleness.py` |
| `repair_backlinks.py` | Repair broken `[[wikilinks]]` | `uv run python scripts/repair_backlinks.py` |
| `check_sdk.py` | Verify Claude SDK backend availability | `uv run python scripts/check_sdk.py` |

---

## Conventions

- **Wikilinks**: Obsidian-style `[[path/to/article]]` without `.md`
- **Writing style**: Encyclopedia, factual, third-person
- **Dates**: ISO 8601 (`YYYY-MM-DD` for dates, full ISO in `log.md`)
- **File naming**: lowercase kebab-case
- **Frontmatter**: Every article must have `title`, `sources`, `created`, `updated`
- **Sources**: Always link back to the daily log(s) that contributed

---

## LLM Backend

Set `SECOND_BRAIN_LLM_BACKEND` (default: `claude`):

- `claude` — `claude-agent-sdk` with bundled CLI fallback
- `cursor` — tries Cursor CLI `agent` command
- `none` — disables LLM calls (deterministic local mode only)

Health check:

```bash
uv run python scripts/check_sdk.py
```
