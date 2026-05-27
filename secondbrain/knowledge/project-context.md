---
title: "Omnia.AI — Project Context"
tags: [project-context, persistent-memory]
sources:
  - "CLAUDE.md"
  - "docs/00-architecture.md"
  - "docs/01-api-contract.md"
created: 2026-05-17
updated: 2026-05-17
tier: hot
---

# Omnia.AI — Project Context

> Persistent project map. Injected into every Claude Code session via
> `hooks/session-start.py`. Keep it under 8 KB so it doesn't dominate context;
> rotate stale items into concept articles instead of bloating this file.

## Кто и зачем

**Omnia.AI** — AI-сайт-билдер «под ключ» для русского рынка. Пиши промпты — получай
готовый сайт с backend, доменом, деплоем и кнопкой «вернуться назад» для
каждого промпта. Всё в рублях. Владельцы: Артём Левченко + Рома Исакин.

Прод сейчас: `https://constructor.lead-generator.ru` (временный домен, VPS
Serverum, IP `170.168.72.200`, SSH `i48ptgvnis@`). Целевые: `omnia.ai`,
`omnia.ru`, `омния.рф` — не куплены.

Бизнес-план: `C:\Бизнес план\AI_Site_Builder_Business_Plan_v1.xlsx`.

## Стек (фиксированный — менять только через обсуждение)

- **Frontend:** Next.js 15 (App Router) + React 19 + TypeScript + Tailwind v4 +
  shadcn/ui + framer-motion + React Query 5
- **Backend:** FastAPI (Python 3.12) + Postgres 16 + Redis 7 + MinIO + pygit2 +
  Playwright + RQ
- **LLM Gateway:** FastAPI + LiteLLM (proxy к Anthropic/OpenAI/YandexGPT/Sber/proxyapi.ru)
- **Auth:** Auth.js (NextAuth) с JWT, бэкенд верифицирует через JWKS
- **Платежи:** ЮKassa (stub в MVP)
- **Infra:** Docker Compose локально → Ansible+Docker на VPS Serverum в проде

## Трёхагентная структура

| Агент | Папка | Брифинг |
|---|---|---|
| **A — Frontend** | `apps/web/` | `agents/AGENT-A-FRONTEND.md` |
| **B — Backend** | `apps/api/` | `agents/AGENT-B-BACKEND.md` |
| **C — LLM Gateway + DevOps** | `apps/llm-gateway/`, `infra/` | `agents/AGENT-C-LLM-GATEWAY.md` |

**Жёсткое правило:** агент пишет ТОЛЬКО в свою папку. Контракт изменений — через
`docs/01-api-contract.md`. Если нужна правка контракта — записка в координацию
`~/.claude/coordination/<slug>/inbox/` + продолжай.

## Pipeline: промпт → сайт

```
ChatPanel.submit
  → POST /api/projects/:id/prompt
    → background task _process_prompt:
        → llm_client.stream_chat_completion (SSE из llm-gateway)
        → накапливает accumulated, шлёт llm.chunk через WS
        → file_extractor.extract_files (regex `<file path="...">...</file>`)
        → if files:
            repo.commit_files (pygit2 + tar.gz в MinIO)
            insert Snapshot, update project.current_snapshot_id
            enqueue_preview (RQ → workers/preview.py → Playwright PNG → MinIO)
            publish snapshot.created через WS
        → else: publish llm.error «модель не вернула файлов» (T1-fix 2026-05-17)
        → publish llm.done с usage
```

Контракт WS-событий и REST: `docs/01-api-contract.md`.

## Поддерживаемые модели (LLM Gateway)

| Model ID | Provider | Slug | Key env |
|---|---|---|---|
| `claude-sonnet-4-6` | Anthropic | `anthropic/claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| `claude-opus-4-7` | Anthropic | `anthropic/claude-opus-4-5` | `ANTHROPIC_API_KEY` |
| `claude-haiku-4-5` | Anthropic via proxyapi.ru | `anthropic/claude-haiku-4-5` + `api_base=https://api.proxyapi.ru/anthropic` | `PROXYAPI_API_KEY` |
| `gpt-4.1` | OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| `gpt-5-mini` | OpenAI | `openai/gpt-4o-mini` | `OPENAI_API_KEY` |
| `yandexgpt-5` | Yandex | custom httpx | `YANDEX_API_KEY` + folder |
| `qwen-3-coder` | Alibaba via OpenRouter | `openrouter/qwen/qwen3-coder` | `OPENROUTER_API_KEY` |
| `gigachat-2{,-pro,-max}` | Sber | OAuth + httpx (`providers/sber.py`) | `GIGACHAT_AUTH_KEY` |

Дефолтная модель на проде: `claude-haiku-4-5` (`DEFAULT_MODEL` env).

## Workspace UI (apps/web/src/components/workspace)

- **ChatPanel** — чат с ассистентом, очередь промптов (single slot, fires на `llm.done`)
- **PromptInput** — textarea не блокируется во время стрима (Enter → в очередь), Стоп + Send одновременно
- **ChatMessage** — парсит `<file>` блоки через `lib/parse-assistant.ts`, рендерит файловые чипы (expand-on-click + размер)
- **PreviewFrame** — таб переключатель **Preview** / **Код** + баннер «Просматриваете старую версию» когда selectedSnapshotId ≠ HEAD
- **StreamingPreviewFrame** — долгоживущий iframe + morphdom postMessage DOM diff: элементы добавляются с fade+slide-up (250ms cubic-bezier 0.16,1,0.3,1), CSS inject в `<style id="omnia-css">`. Bootstrap-HTML грузит Tailwind CDN + morphdom CDN
- **CodeView** — файл-дерево слева + `<pre>` справа с Copy/Download. Тянет `GET /api/projects/:id/snapshots/:sid`
- **Timeline + SnapshotCard** — компактные карточки, при hover scale 1.04, `Откатить` создаёт новый snapshot на старом commit_sha
- **ModelSelector** — динамический dropdown из `/llm/v1/models`, `available: false` → disabled

## Доменный словарь (Ubiquitous language, R-08)

- **Проект / Project** — рабочее пространство пользователя с git-репо
- **Snapshot** — git commit + preview PNG + prompt + model_id + parent_id
- **Прогон / Stream** — один акт чата (от Send до `llm.done`)
- **Стримящий iframe** — реалтайм morphdom-patched preview, активен пока isStreaming
- **Коммитет(ный) snapshot** — статический `/p/<slug>` после snapshot.created
- **Биллинг** — списание ₽ при `llm.done` через `wallet_charges` + `wallets`

## Важные правила (помимо канона)

- Не лезть в чужие папки агентов A/B/C — если надо, через `docs/01-api-contract.md`
- Перед `Edit/Write` — активировать `code-canon` skill (10 ironclad rules в глобальном CLAUDE.md)
- После значимых изменений (3+ файлов) — `/canon-review`
- Перед коммитом — `/safe-commit`
- **MOCK_LLM=false** на проде ([deploy/full/docker-compose.yml](../../apps/llm-gateway/deploy/full/docker-compose.yml))
- **NEXT_PUBLIC_USE_MOCKS=false** на проде (build-arg)

## Что НЕ в MVP

- Реальная оплата через ЮKassa (только UI кошелька)
- Регистрация доменов (только наш `*.omnia.ai` поддомен в плане)
- GitHub-синк, white-label, A/B-тесты, мобильные приложения

## Точки синхронизации (read-only для агентов)

- [`docs/00-architecture.md`](../../docs/00-architecture.md) — как A/B/C соединяются
- [`docs/01-api-contract.md`](../../docs/01-api-contract.md) — REST + WebSocket контракт
- [`docs/02-data-model.md`](../../docs/02-data-model.md) — Postgres-схема
- [`docs/03-design-system.md`](../../docs/03-design-system.md) — палитра, типографика, компоненты
- [`docs/04-monetization-plan.md`](../../docs/04-monetization-plan.md) — биллинг + цены
- [`docs/05-platform-experience.md`](../../docs/05/platform-experience.md) — общая UX-философия
- [`docs/06-session-log.md`](../../docs/06-session-log.md) — лог релизов и инцидентов
