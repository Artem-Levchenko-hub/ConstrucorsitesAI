---
title: "Auto-Memory ↔ SecondBrain Bridge"
aliases: [memory-sync, auto-memory-integration, claude-memory-bridge]
tags: [infrastructure, memory, hooks, pipeline]
sources:
  - "AGENTS.md"
  - "scripts/sync_memory.py"
  - "hooks/session-start.py"
created: 2026-04-18
updated: 2026-04-18
tier: warm
keywords: auto-memory, feedback, MEMORY.md, sync-memory, daily-log, sha-256
---

# Auto-Memory ↔ SecondBrain Bridge

Omnia.AI runs two parallel memory systems. Claude Code's built-in
auto-memory stores live, small-granular facts (`feedback_*.md`, `user_*.md`,
`project_*.md`) in the user's Claude profile directory. SecondBrain stores
compiled, cross-referenced wiki articles that are committed to the repo. The
**bridge** is a one-way pipeline: memory edits flow into the SecondBrain daily
log, then get compiled into wiki articles during the evening compile pass —
after which the whole team sees them via `git pull`.

## Key Points

- **Source (per developer)**: `~/.claude/projects/<project-path-hash>/memory/*.md`
  where `<project-path-hash>` is the project's absolute path with `:`, `/`, `\`
  replaced by `-`. Override with `CLAUDE_CODE_MEMORY_DIR` env var.
- **Destination (repo)**: today's `daily/YYYY-MM-DD.md`, section `### Memory Sync (HH:MM)`
- **Deduplication**: SHA-256 hash of each memory file, stored in `scripts/memory-state.json` (gitignored)
- **Auto-trigger**: `hooks/session-start.py` spawns `scripts/sync_memory.py`
  as a detached subprocess, **at most once per UTC date** (marker: `scripts/last-memory-sync.json`)
- **Manual trigger**: `/sb-ingest-memory` slash command, or
  `uv run python scripts/sync_memory.py`
- **Compile**: the daily log (now containing memory entries) is compiled into
  concept articles at 18:00 local via `flush.py → compile.py`
- **Context injection**: `session-start.py` also prepends the raw `MEMORY.md`
  index to `additionalContext`, so Claude sees both live memory and compiled
  wiki at session start

## Details

The two memory systems serve different time horizons. Auto-memory is read-write
in seconds — as Claude learns a preference (`user prefers bundled PRs`) it writes
a file immediately. SecondBrain is read-write in days — the compile pass happens
overnight and produces permanent, linked articles.

The bridge preserves the fast path (auto-memory stays authoritative for "what
did the user just tell me") while giving the slow path (wiki) a chance to
crystallise repeated lessons into proper concepts. When a `feedback_*.md` file
keeps getting updated about the same topic, the compile pass can merge it with
related articles, and the original memory file can be shortened to a single-line
pointer at the matching wikilink.

Three safeguards prevent runaway ingest. First, the SHA-256 check skips unchanged
files. Second, the `MEMORY.md` index itself is excluded (`SKIP_FILES`) since
session-start already injects it verbatim. Third, the spawned subprocess sets
`CLAUDE_INVOKED_BY=secondbrain_session_start_memory_sync` so downstream hooks
short-circuit to avoid recursion.

## Portability

Because the memory directory is **auto-derived from `PROJECT_ROOT`**, a teammate
who clones the repo onto a different machine gets the correct path without any
configuration:

| Project path | Memory dir |
|--------------|------------|
| `C:\Бизнес план\omnia-mvp` (Windows) | `~/.claude/projects/C--------------omnia-mvp/memory/` |
| `/home/bob/omnia-mvp` (Linux) | `~/.claude/projects/-home-bob-omnia-mvp/memory/` |
| Custom path via env var | `$CLAUDE_CODE_MEMORY_DIR` |

If the dir doesn't exist yet (first session on a fresh clone), `sync_memory.py`
exits cleanly with a hint — no error.

## Flow Diagram

```
┌─────────────────────────────┐
│ Claude Code session         │
│   Writes: feedback_*.md     │──┐
└─────────────────────────────┘  │
                                 │ 1. edit
                                 ▼
        ┌────────────────────────────────────────┐
        │  ~/.claude/projects/<hash>/memory/*.md │
        └────────────────────┬───────────────────┘
                             │ 2. SHA-256 diff
                             │    (scripts/memory-state.json)
                             ▼
        ┌────────────────────────────────────────┐
        │  scripts/sync_memory.py                │
        │  (fired by session-start once/day, or  │
        │   manually via /sb-ingest-memory)      │
        └────────────────────┬───────────────────┘
                             │ 3. append block
                             ▼
        ┌────────────────────────────────────────┐
        │  daily/YYYY-MM-DD.md                   │
        │  ### Memory Sync (HH:MM)               │
        └────────────────────┬───────────────────┘
                             │ 4. 18:00 or /sb-compile
                             ▼
        ┌────────────────────────────────────────┐
        │  knowledge/concepts/*.md + index.md    │
        │  (git-committed → team sees via pull)  │
        └────────────────────────────────────────┘
```

## Related Concepts

- The *Claude Code Auto-Memory Bridge* section in `AGENTS.md` defines the contract.
- See also *Hook System* and *Scripts Reference* in `AGENTS.md` for operational detail.

## Sources

- `AGENTS.md` — the "Claude Code Auto-Memory Bridge" section
- `scripts/sync_memory.py` — ingest logic and `default_memory_dir()`
- `hooks/session-start.py` — once-per-day trigger and `MEMORY.md` injection
