# Omnia.AI starter — `nextjs-realtime` (channels + SSE + Postgres)

Шаблон, который orchestrator копирует в `/opt/omnia-runtime/projects/<id>/` при
создании нового real-time проекта (мессенджер, живая доска, лента уведомлений).

## Что это за стек

Готовая **realtime-подложка**, на которую AI пишет только фронтенд:

- **Модель = каналы.** Канал — строка `"<kind>:<id>"`: `conversation:<id>` (комната
  чата), `user:<uid>` (личная лента), `public:<name>` (открытый бродкаст),
  `presence:<id>` (кто онлайн).
- **Транспорт = Server-Sent Events.** Клиент подписывается через
  `GET /api/realtime/<channel>/stream` (`EventSource`), публикует через
  `POST /api/realtime/<channel>` с `{ type, data }`. Хаб
  (`src/lib/realtime/hub.ts`) делает in-process pub/sub + presence, а при заданном
  `REDIS_URL` — fan-out между репликами в проде.
- **Авторизация — серверная, на основе отношений** (`src/lib/realtime/policy.ts`):
  каждая подписка И публикация проверяются по таблице `channel_members` ДО хаба.
  Для `conversation:*`/`presence:*` читать и писать могут ТОЛЬКО участники — не
  владелец-скоуп, а реляционная проверка, поэтому мессенджер не утекает по умолчанию;
  посторонний получает 403. Неизвестные виды каналов — fail closed.
- **Персистентность:** публикация `type:"message"` сохраняется в `messages` и
  раздаётся уже сохранённой строкой; `typing`/`reaction`/`cursor` — эфемерные;
  `presence` — управляется движком (его не публикуют).
- **Rate limit:** 20 публикаций / 10с на пару пользователь+канал.

## Карта файлов — что зафиксировано, что редактируемо

| Файл / папка | Статус |
|---|---|
| `src/lib/realtime/**` (hub, policy, transport) | 🔒 FIXED — не трогать |
| `src/lib/db/schema.ts` (`users`, `channels`, `channel_members`, `messages`) | 🔒 FIXED |
| `src/lib/auth.ts`, `src/lib/session.ts`, `src/lib/channels.ts` | 🔒 FIXED |
| `src/app/api/realtime/**`, `/api/auth/**`, `/api/channels/**` | 🔒 FIXED — route handlers |
| `src/components/realtime/use-channel.tsx` (хук `useChannel`) | 🔒 FIXED — импортировать |
| `src/app/(app)/**` | ✍️ AI пишет — страницы приложения |
| app-specific компоненты и серверные хелперы | ✍️ AI пишет |
| `src/lib/db/app-schema.ts` (СВОИ таблицы, если нужны) | ✍️ AI — НОВЫЙ файл, не `schema.ts` |
| `SYSTEM_PROMPT.md` | Инструкции для AI: что можно/нельзя |

## Переменные окружения

| Var | Назначение |
|---|---|
| `DATABASE_URL` | Postgres-подключение (orchestrator инжектит) |
| `AUTH_SECRET` | секрет NextAuth v5 (JWT) |
| `REDIS_URL` | опционально — fan-out между репликами в проде; без него хаб работает in-process (одна реплика) |

## Что AI получает в каждом промпте

Orchestrator конкатенирует:
1. `SYSTEM_PROMPT.md` (этот шаблон)
2. Текущее состояние всех файлов проекта (или их diff)
3. Историю диалога (последние N сообщений)
4. Пользовательский промпт

## Как запускается

```bash
# dev (HMR):
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dev AUTH_SECRET=dev pnpm dev
# http://localhost:3000

# prod: next build → standalone-сервер (output: "standalone"), REDIS_URL для multi-replica fan-out
pnpm build && node .next/standalone/server.js
```

## Почему это настоящее приложение, а не прототип

Доступ к каналу проверяется на сервере по таблице `channel_members` на КАЖДОЙ
подписке и публикации (реляционный ACL, а не клиентская проверка), история сообщений
персистится в Postgres и переживает перезагрузку, presence показывает реальных
онлайн-участников, а SSE-хук авто-переподключается и доигрывает пропущенные
сообщения при разрыве. Это не демо с фейковым стейтом — это леак-устойчивый
мессенджер, который масштабируется на несколько реплик через Redis.
