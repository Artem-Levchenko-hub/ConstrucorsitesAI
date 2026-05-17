# Build Log

> Append-only log of compile/query/lint operations. Latest entries on top.

## 2026-05-17T17:55:00+03:00 — Initial port from CorporateMessanger

- Created scaffold: `daily/`, `knowledge/{concepts,connections,qa}`, `raw/web`, `scripts/`, `hooks/`, `templates/`
- Adapted `scripts/sync_project_docs.py`, `scripts/update_context.py`, `hooks/post-tool-use.py` for Omnia.AI paths
- Wrote Omnia-specific `README.md`, `AGENTS.md`, `knowledge/project-context.md`, `knowledge/index.md`
- Seeded 5 concept articles from session 2026-05-17 work:
  - proxyapi-anthropic-route
  - realtime-streaming-preview
  - file-extractor-pipeline
  - zero-files-silent-failure
  - secondbrain-runtime (port)
  - auto-memory-bridge (port)
  - wiki-taxonomy-and-link-conventions (port)
- Wired `.claude/settings.json` hooks: SessionStart, SessionEnd, PreCompact, PostToolUse, Stop
- `.gitignore` for state files
