# 06. Session log — что было сделано (живая хронология)

> Файл фиксирует крупные шаги работы Агента C в порядке появления — с
> commit-хешем, причиной, и ссылкой что заработало в результате. Позволяет
> в будущем не «копаться в `git log`», а прочитать историю как нарратив.

---

## Часть 1. M0–M3 LLM Gateway (apps/llm-gateway/)

### M0 — базовый каркас (`6a52d7e` … `5867d1f`)
- `uv init`, FastAPI 0.115 на порту 8001.
- `core/{config,db,redis,http,errors,logging}.py` — pydantic-settings,
  asyncpg pool, redis-py async, общий httpx-клиент (R-31).
- `services/pricing.py` — `PRICE_TABLE` для 6 моделей (Anthropic / OpenAI /
  Yandex / Qwen) + `calculate_cost_rub` с NUMERIC(12,4)-квантизацией.
- `services/litellm_router.py` — LiteLLM Router с fallback'ами.
- `providers/yandex.py` — кастомная httpx-обёртка (LiteLLM-нативный
  Yandex нестабилен между версиями).
- `routers/{health,models,chat}.py` — REST surface.
- 13/13 pytest зелёные. Ruff чистый.

### M1 — SSE-стриминг + cancellation (`d6f286d`)
- `services/streaming.py` — SSE-генератор через sse-starlette, с
  cancellation-guard в `finally` блоке (биллится только реально отданное).
- `_yandex_pseudo_stream` — пословное чанкование для провайдеров без
  нативного стриминга.
- `services/token_counter.py` — tiktoken (`cl100k_base`) для GPT/Claude/Qwen,
  `len // 4` fallback для Yandex.

### M2 — кеш + safety (`d6f286d`)
- `services/cache.py` — Redis-кеш с sha256-ключом по `model + system + last_user`,
  TTL из ENV. user_id не входит в ключ (экономия при одинаковых запросах).
- `services/safety.py` — 4 regex против prompt-injection (`ignore previous`,
  `system:`, `</file>`, base64 >1k). Триггер заменяет на `[фильтровано]` —
  fail-soft подход вместо полного reject.

### M3 — биллинг + observability (`d6f286d`)
- `services/billing.py` — атомарная транзакция: `UPDATE wallets SET balance -=`
  с условием `>= cost`, при RowCount=0 → `WalletEmptyError`. Плюс INSERT
  в `wallet_charges` и `usage`.
- `services/file_logger.py` — `logs/llm-{date}.jsonl` с дневной ротацией.
- `routers/chat.py` — оркестратор: safety → cache → balance precheck → LLM
  → bill → cache.set → file log.
- 29/29 pytest зелёные.

### Sber GigaChat (`7f2480e`, `f8582bc`)
- `providers/sber.py` — OAuth-token cache + asyncio.Lock (single-flight),
  `acompletion` через `https://gigachat.devices.sberbank.ru/api/v1/chat/completions`.
- 3 модели: `gigachat-2`, `gigachat-2-pro`, `gigachat-2-max`.
- `GIGACHAT_VERIFY_SSL=false` по умолчанию (Russian Trusted Root не в
  стандартном CA bundle Python).
- Fix: пустые `SecretStr("")` env-vars не считаются «ключ присутствует» —
  поправил `is not None` checks.

---

## Часть 2. Деплой на VPS (`b1353fd`, `0c3269c`)

- `apps/llm-gateway/Dockerfile` — multi-stage (uv build → python:3.12-slim).
- `apps/llm-gateway/docker-compose.demo.yml` — standalone gateway + postgres
  + redis для смоук-теста.
- `apps/llm-gateway/deploy/RUNBOOK.md` — пошаговая инструкция деплоя.
- `GATEWAY_HOST_PORT` env-переменная — обход конфликта с lms-backend на
  :8001 через `:8101`.
- На сервере: docker compose up + nginx + Let's Encrypt cert через
  certbot для `constructor.lead-generator.ru`.

---

## Часть 3. Полный стек на проде (`09660e8`, `c02fbc6`, `7c3a89b`, `141d647`, `dd5661b`, `a8263fe`)

- `apps/web/Dockerfile` — Node 20 multi-stage с `output: "standalone"` в
  `next.config.ts`. Build-args для `NEXT_PUBLIC_*`.
- `apps/web/public/.gitkeep` — пустая директория, чтоб COPY в Dockerfile
  не падал.
- `apps/api/.dockerignore` — `!README.md` overrides общий `*.md` exclude
  (hatchling требует README для metadata).
- `apps/api/pyproject.toml` — `bcrypt>=4.0,<4.1` (passlib 1.7.x несовместим
  с bcrypt 4.1+).
- `apps/api/Dockerfile` — отключил `UV_COMPILE_BYTECODE` (на маленькой VPS
  bytecode-compile таймаутил >60s).
- `apps/llm-gateway/deploy/full/docker-compose.yml` — единый стек: web +
  api + worker + gateway + postgres + redis + minio. Внутренние сервисы
  без host-port'ов, внешние (web 3100, api 8200, gateway 8101) под nginx.
- Worker не строит свой образ — переиспользует `omnia-api:prod` чтоб не
  было race на одном теге.
- init.sql отключён в full-stack'е — Alembic от агента B владеет схемой.

---

## Часть 4. Auth — четырёхслойный фикс (`98966cc`, `aaa6c9c`, `84f386c`, `22e3a4e`, `b4f71c2`)

Симптом: после регистрации `/api/projects` возвращал 401, тост «Не удалось
создать проект».

Корни (нашёл три параллельных слоя auth, ссылающихся на разные куки):

1. `app/(auth)/actions.ts` — server actions использовали mock-функции из
   `auth-mock.ts`, **никогда не звали** реальный `/api/auth/{login,register}`.
   Переписал: вызывают api через `INTERNAL_API_URL=http://api:8000` (минуя
   nginx-hop), парсят `Set-Cookie` из ответа, переэмитят через Next.js
   `cookies()` → JWT попадает в браузер.
2. `lib/auth-mock.ts` — `getSession()` читал mock-куку `omnia_session_mock`.
   Переписал: читает реальный `omnia_session` JWT, валидирует через
   `/api/auth/me`.
3. `middleware.ts` — гейт по `omnia_session_mock`, никогда не пускал на
   `/projects`. Поправил константу.
4. `app/(app)/projects/[id]/page.tsx` — server component звал `apiFetch`
   с `credentials:"include"` (это browser-only). На server-side fetch
   куки не подхватываются автоматически. Создал `lib/api/server.ts`
   (`serverApiFetch`) — читает cookie из `next/headers` и attache'т
   к fetch вручную.

Result: register → cookie set → middleware пускает → layout `getSession`
видит юзера → ProjectsList рендерится → POST /api/projects 201.

---

## Часть 5. SEO Phase A (`858ff74`, `1e115c5`, `8ba5dd0`)

- `app/layout.tsx` — full Metadata API: title-template, OG, Twitter card,
  canonical, hreflang ru-RU, Yandex/Google verification env-stubs.
- `app/layout.tsx` — JSON-LD: `Organization` + `SoftwareApplication`.
- `app/sitemap.ts`, `app/robots.ts` — native Next.js endpoints.
- `app/icon.svg` — favicon (закрывает 404 в DevTools console).
- `app/opengraph-image.tsx` — динамическая 1200×630 OG-картинка через
  `next/og`. Сначала упало на edge runtime (`runtime="edge"` не работает
  в standalone) → сменил на `nodejs`. Потом упало на satori
  («multi-child div needs `display: flex`») — переписал layout под satori.
- `components/marketing/Faq.tsx` — `FAQPage` JSON-LD для rich-result
  eligibility.
- `docs/04-monetization-plan.md` — стратегический план: SEO + REG.RU
  reseller (домены) + ЮKassa Connect (маркетплейс) + единый wizard
  onboarding'а с одной формой паспортных данных.

---

## Часть 6. Сделать generate реально работающим

### Provider key check fix (`7f1e7ff`, `fef23b7`)
- Дефолт модели: `claude-sonnet-4-6` → `gigachat-2` (на demo-сервере
  только Sber-ключ).
- ModelSelector: показывать недоступные модели как disabled с пометкой
  `нет ключа`. Добавил «sber» в provider labels.
- `getModels()` — фолбэк на `/llm/v1/models` напрямую (api `/api/models`
  500-ит, известный баг агента B).

### Real WebSocket вместо mock-стрима (`5c58e5d`)
- `usePromptStream.ts` БЕЗ проверки `USE_MOCKS` дёргал `simulatePromptStream`
  → юзер видел канни́рованные «Переделал hero…» и фейк-превью SVG.
- Гейт по `USE_MOCKS`: prod-режим открывает `wss://<host>/api/ws/projects/<id>`
  (cookie attaches автоматически на WS handshake), mocks остаются для dev.

### MinIO public previews (`29493f5`)
- Прокинул `127.0.0.1:9000` MinIO S3 API на host.
- nginx `/minio/` → `http://127.0.0.1:9000/` с trailing slash для path-strip.
- Превью-PNG'ы теперь грузятся в browser.

### Strong-ref для background task'ов (`f693f06`)
- `asyncio.create_task(_process_prompt(...))` без сохранения ссылки →
  Python GC мог собрать Task посреди исполнения → assistant message
  оставался пустым.
- Module-level `_BACKGROUND_TASKS: set[Task]` + `_spawn_process_prompt`
  helper c `add_done_callback` для удаления + логирования исключений.

### Failure marker для UI (`becce85`)
- При исключении в `_process_prompt` ассистент-сообщение оставалось с
  `tokens_out=NULL`, ChatPanel считал его «still streaming» → ввод
  залипал навсегда.
- Теперь: пишем `content="[Ошибка: ...]"` и `tokens_out=0` в catch-блок,
  UI разблокируется.

### Diagnostic logging (`17fe659`, `c57687d`, `54a6cb6`)
- Stdlib `logging.getLogger(__name__).info(...)` глушится в наших docker
  контейнерах (нет logger config'а в api).
- Перевёл ключевые checkpoint'ы `_process_prompt` и `stream_chat_completion`
  на `print(..., flush=True)` — выводится в `docker logs`.
- Cost_rub теперь читается из `metadata.cost_rub` (где gateway его
  кладёт) с fallback на `usage.cost_rub`.

---

## Часть 7. Интерактивный preview (`f8da85b`, `7582f66`, `9e080b7`, `a814d93`, `9b761d6`)

Симптом: статичная PNG-картинка превью без стилей (Playwright делал скрин
до загрузки Tailwind CDN).

- `<img>` → `<iframe src="/p/<slug>">` — реальный сайт, кликается.
- Device toggle: mobile 390 / tablet 768 / desktop 100% (state в локальном
  useState).
- Reload-кнопка через re-key iframe (bypass browser cache).
- URL bar показывает реальный `https://constructor.lead-generator.ru/p/<slug>`
  вместо mock-плейсхолдера `*.omnia.ai`.
- ProjectCard тоже фиксанул: `/p/<slug>` вместо `<slug>.omnia.ai`.
- `apps/api/routers/public.py` — `?snapshot=<id>` query-параметр для
  показа исторических версий. Validate'ит что snapshot принадлежит
  проекту. `X-Frame-Options: SAMEORIGIN` + `Cache-Control: no-cache`.
- `usePromptStream.ts` — на `snapshot.created` авто-`selectSnapshot(null)`
  → iframe re-key'ится на новый HEAD. Hot-reload без клика.
- `docs/05-platform-experience.md` — детальный план «вся разработка на
  сайте»: spr +1 (hot reload + click-to-edit + keyboard), spr +2 (AI
  auto-suggestions + Phase B SEO + Lighthouse), spr +3 (inline editor +
  asset library), spr +4 (collaboration), spr +5 (wallet + домены +
  ЮKassa).

---

## Часть 8. Sber TLS в long-running uvicorn (открытое)

Симптом: после сериий successful генераций gateway начинает таймаутить
TLS handshake к `ngw.devices.sberbank.ru:9443`. Воспроизводимо:

- `curl -k` из того же контейнера → HTTP 200 за 0.05s ✓
- Свежий `python -c` в том же контейнере → HTTP 200 за 0.2s ✓
- Длинный uvicorn-процесс через httpx → `ConnectTimeout('_ssl.c:993:
  The handshake operation timed out')` после 30s ❌

Что попробовано (`5030b36`, `0a8f243`, `6983db9`, `7f9647a`):
- Async httpx → sync httpx через `asyncio.to_thread`.
- connect timeout 15 → 30s.
- `--loop asyncio` вместо uvloop.
- Рестарт контейнера — первый же запрос так же падает.

Гипотезы:
- Resource leak / FD exhaustion в uvicorn после серий retry'ев.
- DPI/RKN на маршруте режет TLS-fingerprint Python's ssl (но не curl).
- Какой-то interaction asyncpg/redis async-pools с httpx ssl.

Workaround (предложено владельцу, ждём ответ):
1. Подключить ANTHROPIC_API_KEY или OPENAI_API_KEY — другие хосты, без
   russian CA-сложностей.
2. Заменить httpx на `requests` через `to_thread`.
3. Использовать LiteLLM-нативный Sber/GigaChat вместо custom-обёртки.

---

## Side-quests

- **kanavto removal** (по запросу владельца): nginx site disable + LE
  cert delete + `/opt/kanavto` rm + Postgres-15 (host-level) database
  drop + role drop. 1.3G дискового пространства освобождено.
- **SSH key auth** для деплоя: одноразовый password-вход через paramiko
  (запушено только в memory, файл с паролем удалён сразу после
  установки `~/.ssh/omnia_gateway_deploy_ed25519`).
- **DNS confusion**: `consconstructor.lead-generator.ru` в задаче не
  резолвился — деплой на `constructor.lead-generator.ru` (corrected
  typo).

---

## Краткий status quo на конец сессии

| Компонент | Статус |
|---|---|
| Регистрация / логин / middleware / cookie | ✅ |
| `/projects`, `/projects/<id>` workspace | ✅ |
| Iframe live preview (clickable, device toggle, reload) | ✅ |
| `/p/<slug>` отдаёт реальный HTML со стилями | ✅ |
| MinIO previews через nginx | ✅ |
| Wallet UI + биллинг (когда генерация работает) | ✅ |
| SEO Phase A (sitemap, robots, OG, JSON-LD, favicon) | ✅ |
| Snapshot timeline + переключение версий | ✅ |
| Hot-reload iframe на snapshot.created | ✅ |
| Real GigaChat генерация → snapshot | ⚠️ интермиттентно работает (TLS-issue выше) |
| `/api/models` 500 (баг агента B) | ❌ workaround: web ходит в `/llm/v1/models` |

Все коммиты на `origin/main`. Working tree чистый.
