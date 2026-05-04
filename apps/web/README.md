# apps/web — Frontend (Next.js 15)

Зона ответственности **агента A**. Перед стартом прочитай:

1. [`/CLAUDE.md`](../../CLAUDE.md)
2. [`/agents/AGENT-A-FRONTEND.md`](../../agents/AGENT-A-FRONTEND.md)
3. [`/docs/01-api-contract.md`](../../docs/01-api-contract.md)
4. [`/docs/03-design-system.md`](../../docs/03-design-system.md)

## Быстрый старт

```bash
cd apps/web
pnpm install
cp .env.local.example .env.local
pnpm dev    # → http://localhost:3000
```

Backend должен быть запущен на `:8000` (см. `apps/api/`) и LLM Gateway на `:8001` (см. `apps/llm-gateway/`).

## Env

| Переменная | Значение в dev | Назначение |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Базовый URL backend |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | Базовый URL WebSocket |
| `NEXTAUTH_SECRET` | сгенерировать `openssl rand -base64 32` | Для next-auth |
| `NEXTAUTH_URL` | `http://localhost:3000` | Callback URL |

## Команды

```bash
pnpm dev          # dev server
pnpm build
pnpm start        # production
pnpm typecheck
pnpm lint
pnpm test         # vitest, если будет
```

Структура и фазы — в `agents/AGENT-A-FRONTEND.md`.
