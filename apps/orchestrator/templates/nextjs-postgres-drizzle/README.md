# Omnia.AI starter — Next.js 15 + Postgres + Drizzle

Шаблон, который orchestrator копирует в `/opt/omnia-runtime/projects/<id>/` при создании нового full-stack проекта.

## Что внутри

| Файл / папка | Назначение |
|---|---|
| `package.json` | Next.js 15, React 19, Tailwind v4, Drizzle ORM, pg, zod |
| `next.config.ts` | `output: "standalone"` — slim prod image |
| `drizzle.config.ts` | Connection через `DATABASE_URL` (orchestrator injects) |
| `src/lib/db/schema.ts` | Стартовая таблица `examples` — AI расширяет |
| `src/lib/db/index.ts` | Drizzle client с pooled pg |
| `src/app/page.tsx` | Default landing — заменяется по первому промпту |
| `src/app/layout.tsx` | Корневой layout, dark by default |
| `src/app/globals.css` | `@import "tailwindcss"` |
| `Dockerfile.dev` | HMR dev-сервер (node:20-alpine, non-root user) |
| `Dockerfile.prod` | Multi-stage build → standalone (~150 MB) |
| `SYSTEM_PROMPT.md` | Инструкции для AI: что можно/нельзя писать |

## Что AI получает в каждом промпте

Orchestrator конкатенирует:
1. `SYSTEM_PROMPT.md` (этот шаблон)
2. Текущее состояние всех файлов проекта (или их diff)
3. Историю диалога (последние N сообщений)
4. Пользовательский промпт

## Что НЕ в шаблоне (Phase A.5)

- **NextAuth** — добавляется AI по запросу через follow-up промпт `pnpm add next-auth@5`
- **Stripe / ЮKassa** — отдельный template `nextjs-yukassa`
- **Email** — отдельный template `nextjs-resend`
- **i18n** — пока нет; обсуждается для Phase A.6

## Локальный smoke (для разработчиков шаблона)

```bash
cd apps/orchestrator/templates/nextjs-postgres-drizzle
pnpm install
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dev pnpm dev
# http://localhost:3000 — стартовая страница
```

## Обновление шаблона

Когда выйдет Next.js 15.2 / React 19.1 / Tailwind v4.1 — `pnpm up --latest` + `pnpm typecheck` + smoke. PR в main с тегом `template:nextjs-postgres-drizzle:bump`. Orchestrator подцепит новую версию автоматически — старые проекты остаются на свой commit шаблона (lockfile + node_modules уже в их volume).
