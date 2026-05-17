# Knowledge Base Index

> Auto-maintained by `scripts/compile.py`. Sorted by recency of last update.
> Stable anchor pages first, session-specific concepts below.

| Article | Summary | Compiled From | Updated |
| --- | --- | --- | --- |
| [[knowledge/project-context]] | Persistent project map: стек, агенты A/B/C, pipeline промпт→сайт, модели, workspace UI, доменный словарь. Инжектится в каждую сессию. | CLAUDE.md + docs/* | 2026-05-17 |
| [[knowledge/concepts/proxyapi-anthropic-route]] | claude-haiku-4-5 routes via proxyapi.ru native-Anthropic endpoint (`anthropic/claude-haiku-4-5` slug + `api_base=https://api.proxyapi.ru/anthropic` — без `/v1`, LiteLLM сам добавит `/v1/messages`). Override-механизм `_PROXY_ROUTES` в `services/litellm_router.py`. | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/realtime-streaming-preview]] | Долгоживущий iframe + morphdom postMessage DOM-diff: новые top-level элементы получают `data-omnia-new` и fade+slide-up через CSS keyframe. Bootstrap-HTML с Tailwind CDN + morphdom CDN. Дебаунс 150ms. | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/file-extractor-pipeline]] | `apps/api/.../file_extractor.py` regex `<file path="X">...</file>` + path sanitize. `prompt_builder.SYSTEM_PROMPT` мандатирует этот формат. Frontend mirror в `apps/web/.../parse-assistant.ts`. GigaChat почти всегда нарушает → 0 файлов → silent fail (см. zero-files-silent-failure). | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/zero-files-silent-failure]] | До 2026-05-17 backend молча финализировал ответ когда `extract_files` возвращал {} — UI получал только `llm.done` и думал «всё ок», но preview не обновлялся. Fix: явный `llm.error` с подсказкой про Haiku/Sonnet. | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/secondbrain-runtime]] | Core runtime conventions: daily logs append-only → compile.py LLM-powered → knowledge wiki. Session-start hook инжектит project-context + MEMORY.md. Auto-memory bridge через `sync_memory.py`. | AGENTS.md | 2026-05-17 |
| [[knowledge/concepts/auto-memory-bridge]] | Bridge между Claude Code auto-memory (`~/.claude/projects/<hash>/memory/`) и SecondBrain: SHA-256 dedup, once-per-UTC-day trigger из session-start, append в daily log с категорией (Feedback/User/Project). | AGENTS.md | 2026-05-17 |
| [[knowledge/concepts/wiki-taxonomy-and-link-conventions]] | Stable taxonomy: concepts/connections/qa, wikilinks с полным relative path, YAML frontmatter обязателен (title/sources/created/updated), kebab-case имена. | AGENTS.md | 2026-05-17 |
