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
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Адрес backend **для браузера**. Нужен только когда API живёт на другом адресе, чем сайт (локальная разработка: сайт `:3000` → API `:8000`). В проде не задаётся: браузер обращается к тому же домену, с которого открыта страница |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | То же для WebSocket. Без неё сокет идёт на хост страницы (`wss://` на https, `ws://` на http) |
| `PUBLIC_ORIGIN` | `http://localhost:3000` | Публичный адрес сайта для абсолютных ссылок в метаданных, `sitemap.xml` и `robots.txt`. Сервер читает её при каждом запросе; в проде задаётся контейнеру `web` в Compose |
| `INTERNAL_API_URL` | не задана | Адрес API для серверных запросов Next.js внутри docker-сети (в проде `http://api:8000`). Без неё сервер берёт `NEXT_PUBLIC_API_URL`, затем `http://localhost:8000` |
| `COOKIE_DOMAIN` | не задана | Родительский домен для cookie сессии (например `.example.ru`), если сессию нужно делить между поддоменами. Старое имя `NEXT_PUBLIC_COOKIE_DOMAIN` по-прежнему читается |
| `NEXTAUTH_SECRET` | сгенерировать `openssl rand -base64 32` | Для next-auth |
| `NEXTAUTH_URL` | `http://localhost:3000` | Callback URL |

### Образ не привязан к домену

Один и тот же Docker-образ `web` должен работать под любым доменом, поэтому домен
в него не вшивается:

- Next.js подставляет **каждую** переменную `NEXT_PUBLIC_*`, существующую в момент
  сборки, во все бандлы сразу — браузерный, серверный и edge. Такая переменная
  после сборки уже не меняется, даже если код читает её «только на сервере».
- Поэтому `Dockerfile` не принимает `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL`
  как build-аргументы. Возвращать их нельзя: `ENV X=$X` без переданного аргумента
  определяет переменную как **пустую строку**, и в бандл попадёт именно она.
- В проде браузер ходит на свой же домен (`/api/...`, WebSocket на `/api/ws/...`),
  а прокси перед приложением направляет `/api/` в backend. Всё, что зависит от
  домена на сервере, читается во время работы из `PUBLIC_ORIGIN`
  (`src/lib/public-origin.ts`).
- Пустое значение везде обрабатывается как «не задано» (`||`, не `??`).

CI после сборки образа проверяет, что в клиентском бандле нет прод-домена
(job `image-build`), а `infra/release/test-compose-policy.sh` — что у `web` нет
URL-аргументов сборки и есть `PUBLIC_ORIGIN` с `INTERNAL_API_URL`.

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
