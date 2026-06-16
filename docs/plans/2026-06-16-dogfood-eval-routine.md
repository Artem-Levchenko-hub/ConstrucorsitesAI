# Dogfood-Eval Routine — протокол (источник правды)

**Создан:** 2026-06-16. **Запуск:** scheduled-task `dogfood-eval-loop`, cron `*/10 * * * *` (каждые 10 мин), последовательно, уступает живым сессиям. Токены не ограничены.

## Миссия

Автономно догфудить ПРОДУКТ (constructor.lead-generator.ru) как живой пользователь: генерить приложения / скрипты / ботов, оценивать **дизайн И функциональность** по скриншотам + логам, **находить тупняки и слепые зоны** генератора, и **чинить их по доказательствам (логи + скриншоты), НЕ вслепую**. Каждый прогон — один цикл, evidence → diagnosis → (fix или proposal) → ledger.

## Главный известный тупняк (гипотеза H1 — проверить ПЕРВОЙ)

Владелец: «написал "создай сайт" → получил статику; кинул 5 промптов "переделай в полноценное веб-приложение" — генератор по сути ничего не делал, чуть перегенривал статику, не отвечал по запросу».

**Гипотеза причины** (из root-cause анализа пайплайна):
- Первый промпт → `discovery` выбирает stack; дефолт `static` (`discovery._DEFAULT_STACK`), а полноценный апп — только если LLM-рекомендер активно выберет контейнер.
- **Follow-up промпты идут через triage → surgical-edit / edit-путь, который правит СУЩЕСТВУЮЩИЙ static HTML и НИКОГДА не пере-запускает discovery / stack_routing.** То есть «переделай в приложение» трактуется как правка статики, а не как смена стека static→nextjs_entities/spa. Эскалации стека на follow-up НЕТ.
- Проверить: `apps/api/src/omnia_api/routers/messages.py` (triage/intent → surgical/edit ветка), `services/intent_triage.py`, `services/discovery.py` (запускается ли на follow-up), `services/stack_routing.py` (`switch_to_stack` вызывается ли вне первого билда).
- **Фикс-направление** (когда подтверждено логами): detect intent «сделай настоящее приложение / добавь вход / БД / кабинет» на follow-up → re-trigger discovery/stack-escalation (static→container), а не surgical-edit статики. С тестом-репро.

## Протокол одного прогона (строго по шагам)

### 0. ГАРД (если не прошёл — выйти, записать строку в LEDGER «skipped: <причина>»)
- **Sequential-lock:** файл `_routine/dogfood.lock`. Если есть и моложе 15 мин → другой прогон идёт → ВЫЙТИ. Иначе записать в него ISO-время старта.
- **Уступи живой сессии:** glob `C:\Users\79133\.claude\coordination\omnia-mvp-*\active-sessions.json`. Если есть сессия с heartbeat за последние 5 мин и это НЕ этот прогон → ВЫЙТИ («yielded to active session»). Нет файла/дир → считать idle, продолжать.
- **Resource-guard:** `ssh i48ptgvnis@170.168.72.200 'free -m'`. Если available < 3000 МБ → НЕ генерировать: `docker rm -f` висяки `omnia-dev-{routine,test,dogfood}-*`, перепроверить; всё ещё мало → ВЫЙТИ.
- Снять lock в КОНЦЕ прогона всегда (даже при ошибке).

### 1. ВЫБОР СЦЕНАРИЯ (ротация по номеру прогона = число строк RUNS в LEDGER mod N)
0. **H1-репро (приоритет, пока H1 открыт):** «создай сайт для кофейни» → дождаться → затем «переделай это в полноценное веб-приложение: вход, личный кабинет, база записей» → ещё 1-2 таких. Замерить: эскалировал ли stack static→container или остался статикой.
1. **Web-app с нуля:** «CRM для записи клиентов: вход, список клиентов, добавление, заметки».
2. **Скрипт/инструмент (spa):** «калькулятор ипотеки с графиком».
3. **Телеграм-бот:** «бот для записи в барбершоп» (проверить — доступен ли вообще tgbot-стек: ранее orphaned).
4. **Свободный:** что-то из реальных запросов в логах прода (грепни недавние user-промпты).

### 2. ГЕНЕРАЦИЯ (через продукт)
- Драйв через браузер (авторизован) — gstack/playwright/Chrome MCP на `constructor.lead-generator.ru`: создать проект, отправить промпт(ы), дождаться сборки, открыть live-превью. ЛИБО через API минт-токеном (надёжнее headless): owner проекта-песочницы; токен — `docker exec omnia-prod-api /app/.venv/bin/python -c "from omnia_api.core.security import create_access_token; from uuid import UUID; print(create_access_token(UUID('<owner>')))"`, api на localhost:8200 (с VPS). Публичное превью: `<slug>-dev.preview.lead-generator.ru`.
- Снять **скриншоты** результата (desktop+mobile, light+dark если есть): сохранить в `_routine/runs/<ISO>/`.
- Скачать сгенерённый артефакт (HTML/.tsx/файлы) для анализа.

### 3. ОЦЕНКА (две оси)
- **Функциональность:** совпал ли результат с запросом? static vs реальный апп (есть `/app`-роуты, auth, БД, интерактив)? Для H1-репро — ЭСКАЛИРОВАЛ ли стек на follow-up? Кнопки/ссылки живые или тупики? Бот/скрипт реально делает заявленное?
- **Дизайн:** скриншот → оценка (rubric: иерархия, глубина, палитра-под-нишу, типографика, «не серый дефолт»). Можно прогнать через vision-судью продукта или оценить самому по скриншоту. Балл 0-10 + 2-3 конкретные проблемы.

### 4. EVIDENCE с сервера (корреляция «что увидел» ↔ «что решил сервер»)
- SSH прод, по project_id/slug грепнуть логи `omnia-prod-api` / `omnia-prod-worker`:
  - какой stack выбрал discovery, какой `gen_mode` (freeform/static/catalog), какая модель, был ли re-discovery на follow-up;
  - `[VSEGPT] stream usage … cache_hit=…` (кэш B), ошибки/traceback, пустые брифы, таймауты.
- Зафиксировать ИМЕННО серверное решение, которое привело к видимому провалу.

### 5. ДИАГНОЗ + СЛЕПЫЕ ЗОНЫ
- Назвать тупняк одним предложением + file:line, где решение принимается неверно.
- **Слепые зоны:** чего генератор НЕ проверяет/НЕ ищет (напр. не перечитывает интент на follow-up; не эскалирует стек; не зовёт судью; не видит, что страница — мёртвая статика с нерабочими кнопками).

### 6. УЛУЧШЕНИЕ (по доказательствам, не вслепую)
- Если фикс ЯСНЫЙ, безопасный, доказан логом+скриншотом → реализовать МАЛЫМ диффом + тест-репро → доставить (commit+push main+деплой нужных сервисов) → health-check. Цитировать строку лога/скриншот, что доказывает.
- Если фикс крупный/рискованный → НЕ шипить вслепую: записать PROPOSAL в LEDGER (что, почему, какой evidence, какой план).
- Один прогон — максимум один фикс (последовательность, не ломать прод).

### 7. ЗАПИСЬ
- Append строку в RUNS (ниже): время MSK, сценарий, stack/gen_mode/модель, design-балл, тупняк, действие (fix `<commit>` / proposal / none), PASS/FAIL.
- Сырьё (скриншоты, дампы, полный лог прогона) — в `_routine/runs/<ISO>/` (gitignored).
- Снять lock.

## Доставка / прод (CLAUDE.md)
- atomic commit + трейлер `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; push main (через origin/main, `git fetch` сперва — main двигают 4 агента; rebase при race).
- Деплой: api/worker → `cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose up -d --build api worker`; gateway → `… up -d --build gateway`; **фикс ШАБЛОНА `apps/orchestrator/templates/*` → ПЕРЕСБОРКА base-образа на VPS** (`cd /opt/omnia/apps/orchestrator && docker build -t omnia-template-nextjs-postgres-drizzle:dev -f templates/nextjs-postgres-drizzle/Dockerfile.dev templates/nextjs-postgres-drizzle/`), git pull НЕ достаточно.
- Прод-compose = `apps/llm-gateway/deploy/full` (контейнеры `omnia-prod-*`), НЕ `infra/`. Прод git: `git fetch && git merge --ff-only origin/main` (не pull). Health-check: curl прод 200 ИЗВНЕ (с VPS NAT-hairpin даёт ложный 000).

## ЗАПРЕТЫ
- Стоимость/токены/бюджет — НЕ упоминать НИ В КАКОЙ форме.
- НЕ трогать клиентские аппы `kofeinia`/`legomagazin` и чужие проекты `signal-telekom`/`crm-sistema`; не ломать прод. Запрещённые действия (оплата, удаление пользовательских данных, права доступа) — не выполнять.
- Песочница: использовать ТОЛЬКО свои тест-проекты (слаг с префиксом `dogfood-`), удалять тест-контейнеры после прогона.
- Модели — всё через vsegpt; proxyapi не использовать.

---

## BLIND-SPOTS LEDGER (накопительный — слепые зоны генератора)
<!-- одна строка на подтверждённую слепую зону: ID | дата | описание | file:line | статус(open/fixed commit) -->
- H1 | 2026-06-16 | follow-up «переделай в приложение» не эскалирует stack (surgical-edit правит статику, discovery/stack_routing не пере-зовутся) | messages.py triage→edit ветка | OPEN (проверить прогоном #1)

## RUNS LOG (append-only — одна строка на прогон)
<!-- время MSK | сценарий | stack/gen_mode/модель | design-балл | тупняк | действие | PASS/FAIL -->
