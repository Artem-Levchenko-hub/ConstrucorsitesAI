---
title: "Daily Summary 2026-05-17"
tags: [corporate-messanger, daily, fallback]
sources:
  - daily/2026-05-17.md
created: 2026-05-18
updated: 2026-05-18
---

# Daily Summary 2026-05-17

Compiled fallback summary for 2026-05-17.md

## Key Points

- Добавил `claude-haiku-4-5` в каталог моделей (`apps/llm-gateway/.../pricing.py`).
- Routing: `anthropic/claude-haiku-4-5` slug + `api_base=https://api.proxyapi.ru/anthropic` (без `/v1` — LiteLLM сам добавит `/v1/messages`). Через override-механизм `_PROXY_ROUTES` в `services/litellm_router.py`.
- Per-model availability в `routers/models.py`: `_MODEL_KEY_OVERRIDE` для haiku (читает PROXYAPI_API_KEY, не ANTHROPIC).
- `core/config.py`: новые поля `proxyapi_api_key`, `proxyapi_base_url`.
- **T1-fix:** `apps/api/.../routers/messages.py:308` — теперь когда `extract_files()` возвращает {}, шлём `llm.error` с подсказкой про Haiku/Sonnet. До этого был silent fail: только `llm.done`, без snapshot — UI выглядел «всё ок» но preview не обновлялся.
- `conftest.py`: использовать `setenv("")` вместо `delenv` — pydantic-settings читает `.env` файл независимо от `os.environ`.
- Обновил `docs/01-api-contract.md` и `agents/AGENT-C-LLM-GATEWAY.md`.
- Деплой: prod `.env` обновлён (`DEFAULT_MODEL=claude-haiku-4-5`, `PROXYAPI_API_KEY`+`PROXYAPI_BASE_URL`), `docker compose up --build gateway api worker`.

## Details

# Daily — 2026-05-17

## Sessions

### Session: Haiku via proxyapi.ru + T1 fix + Streaming Preview + CodeView + Compact Cards + SecondBrain port

**Контекст:** Один длинный chat (Sun 17 May 2026), Claude Opus 4.7 (1M context). Цели накапливались по ходу — каждая решённая задача рождала следующую.

#### Что сделано (хронологически, с коммитами):

1. **`84a8986` `feat: add Claude Haiku 4.5 via proxyapi.ru + surface 0-files failure`**
   - Добавил `claude-haiku-4-5` в каталог моделей (`apps/llm-gateway/.../pricing.py`).
   - Routing: `anthropic/claude-haiku-4-5` slug + `api_base=https://api.proxyapi.ru/anthropic` (без `/v1` — LiteLLM сам добавит `/v1/messages`). Через override-механизм `_PROXY_ROUTES` в `services/litellm_router.py`.
   - Per-model availability в `routers/models.py`: `_MODEL_KEY_OVERRIDE` для haiku (читает PROXYAPI_API_KEY, не ANTHROPIC).
   - `core/config.py`: новые поля `proxyapi_api_key`, `proxyapi_base_url`.
   - **T1-fix:** `apps/api/.../routers/messages.py:308` — теперь когда `extract_files()` возвращает {}, шлём `llm.error` с подсказкой про Haiku/Sonnet. До этого был silent fail: только `llm.done`, без snapshot — UI выглядел «всё ок» но preview не обновлялся.
   - `conftest.py`: использовать `setenv("")` вместо `delenv` — pydantic-settings читает `.env` файл независимо от `os.environ`.
   - Обновил `docs/01-api-contract.md` и `agents/AGENT-C-LLM-GATEWAY.md`.
   - Деплой: prod `.env` обновлён (`DEFAULT_MODEL=claude-haiku-4-5`, `PROXYAPI_API_KEY`+`PROXYAPI_BASE_URL`), `docker compose up --build gateway api worker`.
   - Верификация: `/v1/models` показывает `claude-haiku-4-5 ON`, реальный chat-completion проходит (PROD-OK, 0.0075 ₽).

2. **`9c80dfe` `feat(web): compact chat with file chips + non-blocking input + live srcDoc preview`**
   - `ChatMessage` парсит `<file>` блоки через `lib/parse-assistant.ts` (новый файл) и рендерит compact-чипы с expand-on-click + размер в KB.
   - `PromptInput`: убран `disabled={isStreaming}` с textarea — можно набирать пока модель отвечает.
   - `PreviewFrame`: первая версия streaming preview — использует `srcDoc` с собранным HTML из закрытых `<file>` блоков. Inline-ит CSS из `<link rel=stylesheet>` тегов.

3. **`a49a1d9` `fix(web): Stop actually stops + Enter queues during streaming`**
   - `cancel()` в `usePromptStream`: теперь не только закрывает WS, но и помечает текущее ассистентское сообщение завершённым в React Query кэше (`tokens_out=0` + `[Отменено пользователем]`) — UI разлочивается мгновенно.
   - **Очередь промптов:** `submit()` во время стрима кладёт в `pendingRef` (single slot). На `llm.done
...(truncated)

## Related Concepts

- [[knowledge/concepts/secondbrain-runtime]]
- [[knowledge/concepts/daily-ingestion-process]]

## Sources

- [[daily/2026-05-17.md]]
