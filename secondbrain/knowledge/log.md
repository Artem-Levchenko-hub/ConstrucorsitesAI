# Build Log

> Append-only log of compile/query/lint operations. Latest entries on top.

## [2026-05-26] ingest | Phase A.1+A.2 + Phase B scaffold

- A.1 — `design_anchor` (palette + skill_brief) перенесён на позицию #2 в system prompt, до `_QUALITY_BAR`. Раньше `skill_brief` сидел в середине ~15K промпта — Haiku 4.5 терял фокус на длинном контексте.
- A.2 — `_DESIGN_KIT` стал conditional: инжектится ТОЛЬКО когда нет preset_id И нет skill_brief. Раньше всегда — 3 конкурирующих источника палитры (generic catalog vs matched skill vs preset).
- A.4 — выяснилось что `_RU_INDUSTRY_KEYWORDS` уже 338 stem'ов (план переоценил «~12»). No change.
- Phase B — multipass scaffold (`services/multipass_generator.py`): 2-pass pipeline skeleton→assembly за feature-flag `MULTIPASS_MODELS`. Default empty = single-shot для всех. Будущее расширение до 4 passes (skeleton/content/visual/assembly) сохраняет тот же event shape.
- См.: commits `a1fb189` (Phase A), `09c3a02` (Phase B); концепт остаётся [[concepts/omniaai-generation-quality-regressions-2026-05-26]]

## [2026-05-26] ingest | generation quality regressions — 4 root causes fixed

- proxyapi.ru cold-start empty-response: warmup loop + in-band retry in `services/warmup.py` and `litellm_router.acompletion`
- preset classifier missed "Flowapteka"-class brands (industry stem buried inside a latin token) — new `_INDUSTRY_FRAGMENT_TO_PRESET` substring matcher in `preset_classifier.py`
- Haiku 4.5 with ~1200-line prompt defaults to training indigo+violet — `_format_palette_anchor()` injected right after `_IDENTITY` for visual templates
- IntersectionObserver doesn't fire initial entry for already-in-viewport `.reveal`-wrapped above-fold counters — `requestAnimationFrame` kick added to `omnia-kit.js` in all 4 static templates
- See: [[concepts/omniaai-generation-quality-regressions-2026-05-26]]

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
## [2026-05-18T14:26:24+03:00] compile | 2026-05-17.md
- Source: daily/2026-05-17.md
- Counts: concepts_created=2, concepts_updated=0, connections_created=0, connections_updated=0
- Concepts upserted: [[knowledge/concepts/daily-2026-05-17-summary]], [[knowledge/concepts/daily-ingestion-process]]
- Connections upserted: (none)
- Mode: fallback

## [2026-05-18T14:26:43+03:00] compile | 2026-05-18.md
- Source: daily/2026-05-18.md
- Counts: concepts_created=1, concepts_updated=0, connections_created=0, connections_updated=0
- Concepts upserted: [[knowledge/concepts/daily-2026-05-18-summary]]
- Connections upserted: (none)
- Mode: fallback

## [2026-05-18T14:26:43+03:00] compile | summary
- Daily files compiled: 2
- Concepts: new=3, updated=0, upserted=3
- Connections: new=0, updated=0, upserted=0
- Quality enrichment: enriched=2, touched=1

## [2026-05-18T14:27:28+03:00] query | Какая модель используется в проде по умолчанию?
- Query executed without file-back
- Wiki articles available: 10
- QA notes currently: 0

## [2026-05-18T14:27:41+03:00] compile | noop
- No changed daily logs. quality_enriched=2, quality_touched=0

## [2026-05-18T14:27:43+03:00] maintenance | nightly
- Wiki totals: before=10, after=10, delta=0
- Docs changed: 0
- Quality enrichment: enriched=2, touched=0
- Compile metrics: n/a (no changed daily logs)
- Backlink repair done. Updated files: 2
- Structural lint: completed

## [2026-05-19T08:49:53+03:00] compile | 2026-05-19.md
- Source: daily/2026-05-19.md
- Counts: concepts_created=5, concepts_updated=0, connections_created=3, connections_updated=0
- Concepts upserted: [[knowledge/concepts/gemini-integration-with-llm-gateway]], [[knowledge/concepts/geo-block-circumvention-via-uk-proxy]], [[knowledge/concepts/gemini-thinking-mode-and-token-budget-fix]], [[knowledge/concepts/api-crash-loop-fix-uv-run-pypi-timeout]], [[knowledge/concepts/workspace-ui-polish-and-optimistic-insert]]
- Connections upserted: [[knowledge/connections/gemini-token-budget-and-output-quality]], [[knowledge/connections/deployment-stability-and-startup-performance]], [[knowledge/connections/end-to-end-validation-of-new-features]]
- Mode: llm-assisted

## [2026-05-19T08:49:53+03:00] compile | summary
- Daily files compiled: 1
- Concepts: new=5, updated=0, upserted=5
- Connections: new=3, updated=0, upserted=3
- Quality enrichment: enriched=5, touched=5

## [2026-05-19T08:52:18+03:00] compile | 2026-05-17.md
- Source: daily/2026-05-17.md
- Counts: concepts_created=6, concepts_updated=0, connections_created=3, connections_updated=0
- Concepts upserted: [[knowledge/concepts/claude-haiku-45-integration-via-proxyapiru]], [[knowledge/concepts/real-time-streaming-preview-with-morphdom]], [[knowledge/concepts/enhanced-chat-ui-and-prompt-queueing]], [[knowledge/concepts/code-view-and-snapshot-management]], [[knowledge/concepts/secondbrain-runtime-port]], [[knowledge/concepts/zero-files-silent-failure-fix]]
- Connections upserted: [[knowledge/connections/haiku-integration-and-zero-files-fix-synergy]], [[knowledge/connections/streaming-preview-and-chat-ui-enhancements]], [[knowledge/connections/code-view-and-snapshot-card-integration]]
- Mode: llm-assisted

## [2026-05-19T08:52:32+03:00] compile | 2026-05-19.md
- Source: daily/2026-05-19.md
- Counts: concepts_created=2, concepts_updated=1, connections_created=3, connections_updated=0
- Concepts upserted: [[knowledge/concepts/gemini-integration-with-geo-block-bypass]], [[knowledge/concepts/api-crash-loop-fix-uv-run-pypi-timeout]], [[knowledge/concepts/workspace-uiux-polish]]
- Connections upserted: [[knowledge/connections/gemini-integration-and-geo-blocking-solution-enables-new-llm-capabilities-for-users]], [[knowledge/connections/api-stability-and-performance-directly-impacts-user-experience-and-platform-reliability]], [[knowledge/connections/monorepo-structure-and-agent-briefs-guide-development-and-maintain-consistency]]
- Mode: llm-assisted

## [2026-05-19T08:52:32+03:00] compile | summary
- Daily files compiled: 2
- Concepts: new=8, updated=1, upserted=9
- Connections: new=6, updated=0, upserted=6
- Quality enrichment: enriched=11, touched=6

## [2026-05-19T08:52:32+03:00] maintenance | nightly
- Wiki totals: before=18, after=32, delta=14
- Docs changed: 12
- Quality enrichment: enriched=11, touched=6
- Compile metrics: concepts(new=8, updated=1, upserted=9), connections(new=6, updated=0, upserted=6)
- Backlink repair done. Updated files: 14
- Structural lint: completed

