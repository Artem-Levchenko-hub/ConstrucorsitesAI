# SecondBrain — Omnia.AI LLM Wiki

A **team-shared, repo-local** knowledge base for Omnia.AI. Clone the project, run
two commands, and every developer (and every Claude Code session) gets the same
compounding wiki built from Claude Code / Cursor sessions, project docs, and
auto-memory.

Architecture and pipeline are direct port of the system used in the
CorporateMessanger / Innertalk codebase, inspired by
[Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
and the [claude-memory-compiler](https://github.com/coleam00/claude-memory-compiler) pipeline.

---

## Why this exists

Most RAG setups re-derive answers from raw documents on every query. The LLM never
builds anything up. Here, sessions, project docs, and Claude Code's auto-memory
are **compiled** into a structured markdown wiki that grows richer over time.
The wiki is committed to git — teammates share the same knowledge after `git pull`.

For Omnia specifically the wiki captures: the three-agent (A/B/C) architecture
of `apps/web` / `apps/api` / `apps/llm-gateway`, prompt → file → snapshot →
preview pipeline contracts, LLM Gateway routing (Anthropic, OpenAI, Yandex,
Sber, proxyapi.ru proxy), prod incidents on `constructor.lead-generator.ru`,
and design / UX decisions in the workspace.

## Three-sources pipeline

```
Claude Code session ─┐
                     ├─► daily/YYYY-MM-DD.md ─► compile.py ─► knowledge/
docs/**.md ──────────┤   (append-only)                         (wiki — git-versioned)
                     │                                            │
memory/*.md ─────────┘                                            ▼
(Claude Code auto-memory)                              session-start.py
                                                                  │
                                                                  ▼
                                                     next session's context
```

| Source | Who writes it | Ingest script |
|--------|---------------|---------------|
| Session transcripts | Claude Code / Cursor hooks | `flush.py` (auto on SessionEnd / PreCompact) |
| Project markdown (docs, CLAUDE.md, agents/) | humans via git | `sync_project_docs.py` (manual / maintenance) |
| Claude Code auto-memory | Claude Code during sessions | `sync_memory.py` (auto, once per UTC day) |

## Setup (new clone)

```bash
cd secondbrain
uv sync
uv run python scripts/init_workspace.py
```

After setup, open the project in Claude Code — the hooks in `.claude/settings.json`
(project-relative paths) take it from there.

## Slash commands (Claude Code)

| Command | What it does |
|---------|--------------|
| `/sb-query <question>` | Index-guided query, optional `--file-back` stores answer in `qa/` |
| `/sb-compile` | Compile changed daily logs into the wiki |
| `/sb-lint` | Seven health checks |
| `/sb-ingest-memory` | Sync Claude Code auto-memory into today's daily log |
| `/sb-ingest-docs` | Sync project markdown docs into today's daily log |

(Slash commands not seeded by this port — add via `.claude/commands/` when needed.)

## CLI (scripts / CI / no slash commands)

```bash
# compile & query (run from secondbrain/)
uv run python scripts/compile.py                    # incremental compile
uv run python scripts/query.py "question" --file-back

# bring in new sources
uv run python scripts/sync_memory.py                # auto-memory → daily
uv run python scripts/sync_project_docs.py          # docs → daily
uv run python scripts/ingest_web.py "https://…"     # external URL → raw/web

# health & maintenance
uv run python scripts/lint.py                       # 7 checks
uv run python scripts/lint.py --structural-only     # no LLM call
uv run python scripts/maintenance.py                # once-per-day orchestrator
uv run python scripts/check_sdk.py                  # verify SDK backend
```

## What's in git vs. local-only

| In repo (team-shared) | Local-only (`.gitignore`) |
|-----------------------|----------------------------|
| `knowledge/`, `daily/`, `raw/` | `scripts/*.json` state files |
| `scripts/`, `hooks/`, `templates/` | `scripts/*.log`, `scripts/tmp/` |
| `AGENTS.md`, `README.md`, `pyproject.toml` | `reports/`, `.venv/` |

Commit convention for wiki-only changes: `docs(secondbrain): …`.

## Claude Code auto-memory integration

Claude Code writes small memory files (feedback, project, user, reference) into
`~/.claude/projects/<project-path-hash>/memory/`. `sync_memory.py`:

1. Auto-derives the memory dir from `PROJECT_ROOT` (override with `CLAUDE_CODE_MEMORY_DIR`).
2. Hashes each `*.md` (SHA-256), skips unchanged, appends changes to today's `daily/`.
3. Runs automatically once per UTC day at session start.

See `AGENTS.md` → *Claude Code Auto-Memory Bridge* for the full contract.

## LLM Backend

Set `SECOND_BRAIN_LLM_BACKEND`:

- `claude` (default) — `claude-agent-sdk` with bundled CLI fallback
- `cursor` — tries Cursor CLI `agent` command
- `none` — disables LLM calls (deterministic local mode)

## Scale

Index-guided retrieval (no RAG) works well up to ~500 articles. Beyond that, swap
`query.py` to use `qmd` or a vector index — the rest of the pipeline is unchanged.
