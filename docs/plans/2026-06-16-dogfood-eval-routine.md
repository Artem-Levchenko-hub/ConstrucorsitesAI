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
- H1 | 2026-06-16 | follow-up «переделай в приложение» не эскалирует stack (surgical-edit правит статику, discovery/stack_routing не пере-зовутся) | messages.py:626,675 | **CONFIRMED** (run #1, code-path proof + live prod repro) → PROPOSAL ниже, фикс рискованный (деструктивный re-scaffold) → НЕ шипить вслепую
- BS-2 | 2026-06-16 | `decide_intent` не имеет «app-ification» интента: «вход / личный кабинет / база записей» не матчат ни _REBUILD, ни _STRUCTURAL keywords → CHEAP surgical-edit (auth/login-стемы намеренно исключены, чтобы не фолс-фолсить на «кнопка входа») | intent_triage.py:62,103 | CONFIRMED (run #1) — часть H1; xfail-репро в test_intent_triage.py
- BS-3 | 2026-06-16 | ЗЕРКАЛО H1: discovery ПЕРЕ-эскалирует инструмент с явным «без регистрации/входа/аккаунтов» в auth-стек nextjs_entities → калькулятор оказывается за /signin + ненужная БД; safety-net `_infer_stack_from_text` однонаправленный (только static→entities, никогда spa, не вето́ит LLM-pick) | discovery.py:838 | **FIXED 04bb7cf** (run #2: live-репро dogfood-mortgage-calc-68f79c + screenshot CTA→/signin; негативный safety-net `_explicit_no_backend`→spa, 3 теста-репро, задеплоено api/worker, VETO_FIRES verified live)
- BS-6 | 2026-06-16 | **E-commerce/storefront запрос схлопывается в authed-dashboard CRM** — нет публичного каталога (он за /signin), нет КОРЗИНЫ, нет ОФОРМЛЕНИЯ ЗАКАЗА, хотя бриф их явно называет. Entity writer SYSTEM_PROMPT.md жёстко зашивает «app home is /dashboard (binding)» + «functional app screens (dashboard/CRM/SaaS), not a landing» + «route per entity + a dashboard» → витрина с покупательской воронкой структурно недостижима. Bouquet=access:public, но публичного каталога-UI не генерится; CTA лендинга «В каталог»→/signin (тупик). Order=access:owner single-bouquetId (админ-CRUD), не customer-checkout | apps/orchestrator/templates/nextjs-entities/SYSTEM_PROMPT.md:82,96,118 | **CONFIRMED** (run #5: live-репро dogfood-flowershop-flora-6b00df — entities+routes+CTA+screenshot; BS-4 эскалация сработала end-to-end, но воронка отсутствует) → PROPOSAL P-EC, фикс = новый storefront-архетип (крупный) → НЕ шипить вслепую
- BS-6b (design) | 2026-06-16 | На entity-аппах writer красит страницу INLINE-хексом (`bg-[#450A0A] text-[#FAFAFA]` в page.tsx), но НЕ переопределяет `:root`-токены (globals.css `:root` остаётся дефолтным LIGHT: `--background oklch(0.99 0 0)`=white) → themed shadcn-компоненты рассинхронятся: `Button variant="outline"`=`bg-background`(white) + унаследованный near-white текст = НЕВИДИМАЯ вторичная CTA. Entity palette-guard HTML-only (07/08_palette_guard chars=0 на entity) → нет enforcement токенов на entity-поверхностях | globals.css:17 + nextjs-entities/SYSTEM_PROMPT.md:89 | CONFIRMED (run #5 screenshot: «Войти в кабинет» = пустой белый бокс) — часть design-фейла, фикс generator-prompt-level → не вслепую
- BS-4 | 2026-06-16 | ПЕРВЫЙ билд с пропущенным discovery (quiz/«просто сгенерируй» шлёт skip_clarify=true, либо select-mode) НЕ эскалирует stack: `switch_to_stack` живёт ТОЛЬКО в discovery-BUILD ветке, а discovery гейтится `interview_eligible = is_first_build and not skip_clarify` → явный app-запрос («CRM, вход, кабинет, база записей») собирается freeform-статикой с мёртвыми кнопками входа. Это ПЕРВО-билдовый близнец H1 (H1 = follow-up, остаётся PROPOSAL P-H1) | messages.py:706 | **FIXED 5878059** (run #3: live-репро dogfood-crm-zapis-d640f6 → template=blank/gen_mode=freeform; фикс — на first-build без discovery переиспользовать `_infer_stack_from_text`, is_first_build-гейт = недеструктивно в отличие от P-H1; verify dogfood-crm-verify-90b839 → template=nextjs_entities, реальный entity-CRM brief (clients/bookings/notes + auth-кабинет), задеплоено api/worker; 88 passed/1 xfail)
- BS-7 | 2026-06-16 | КОНСЬЮМЕР-БУКИНГ → /signin-стена: лендинг с «запись на X / бронирование Y» (без «без регистрации») форс-эскалируется в auth-gated nextjs_entities; ВСЕ конверсионные CTA («Записаться онлайн», «Записаться») ведут на /signin → клиент обязан ЗАВЕСТИ АККАУНТ, чтобы записаться на замену масла. `_BACKEND_SIGNALS` трактует lead-capture-слова («запись на», «бронирован») как proof нужды в аккаунтах/CRUD (discovery.py:90); `_infer_stack_from_text` всегда возвращает nextjs_entities (discovery.py:130), а негативный safety-net `_explicit_no_backend` спасает только дословное «без регистрации». Явный выбор «Лендинг» в quiz НЕ имеет веса в stack-решении. Новый триггер класса BS-3, который BS-3-downgrade не ловит | discovery.py:90,130,867-885 | **CONFIRMED** (run #5, live prod repro dogfood-autoservice-turbofix-342143: «Лендинг»+автосервис+«запись на ремонт онлайн» → nextjs_entities, page.tsx 5× href="/signin", screenshot в _routine/runs/; live `_infer_stack_from_text` «запись на ремонт»/«запись на стрижку»/«бронирование столика» → все nextjs_entities, «лендинг кофейни» → None) → PROPOSAL P-BS7 ниже; фикс двунаправленно-рискованный (сузить «запись на»/«бронирован» → недо-эскалация настоящих букинг-аппов) + архитектурный пробел (нет пути «static-лендинг + lead-form без клиентского auth») → НЕ шипить вслепую; acceptance-lock в test_stack_routing.py (3 xfail + 3 evidence)
- BS-5 | 2026-06-16 | tgbot/api стеки ОРФАНЫ от discovery: шаблоны `telegram-bot-aiogram` + `fastapi-postgres` полностью собраны и провижатся (stack_registry.py:63, prompt_builder `_TGBOT_STACK`/`_BACKEND_TEMPLATES`, schemas/project.py:46 tgbot→telegram-bot-aiogram), НО ни один NL-запрос их не достигает. discovery `_STACKS` = {static, fullstack, nextjs_entities, spa} и меню `_SYSTEM` предлагают LLM только эти 4; out-of-vocab pick → форс в static (discovery.py:843). Двойной разрыв: `_DISCOVERY_STACK_TO_TEMPLATE` (stack_routing.py:47) тоже без tgbot. Юзер просит бота → молча получает веб-апп | discovery.py:53,418-450 + stack_routing.py:47 | **CONFIRMED** (run #4, live prod repro: «сделай телеграм-бота для записи в барбершоп»→`nextjs_entities`; «telegram бот … без сайта»→`fullstack`) → PROPOSAL P-BS5 ниже; фикс крупный (вокаб+меню+routing-map+backend-only provisioning без web-preview+TELEGRAM_BOT_TOKEN secret-UX) → НЕ шипить вслепую; acceptance-lock в test_stack_routing.py (2 xfail + evidence)

## PROPOSAL P-H1 — эскалация стека на follow-up (2026-06-16, run #1)

**Проблема (доказана):** на follow-up `is_first_build=False` → discovery и `stack_routing.switch_to_stack` ВООБЩЕ не вызываются (gate `interview_eligible = is_first_build and …`, messages.py:626; switch только внутри first-build-ветки, messages.py:675). Параллельно `decide_intent` роутит «сделай настоящее приложение» в CHEAP (BS-2). Итог: статический проект НИКОГДА не станет контейнерным аппом, что бы юзер ни писал. Live-репро (prod-контейнер): все 4 формулировки app-ification → `CHEAP`.

**Почему не фикшу вслепую:** `switch_to_stack` → `repo_svc.init_repo` создаёт СВЕЖИЙ репо с parentless-коммитом и `_upload` ПЕРЕЗАПИСЫВАЕТ MinIO-ключ репо (repo.py:79-102). На first-build это ок (пустой starter), но на follow-up это СНОСИТ существующий сайт юзера + ломает rollback в таймлайне (старые snapshot-SHA исчезают из репо). Плюс нужна consent-UX (спросить юзера перед сменой стека) — это cross-zone (apps/web).

**План безопасного фикса (для отдельного прогона/согласования):**
1. `decide_intent` (или новый `detect_appification`) — распознать «сделать настоящее приложение / вход+кабинет+БД» на static-проекте. Тайтовый набор сигналов, тест-репро против фолс-фолса (см. xfail в test_intent_triage.py).
2. На таком follow-up + `project.template == 'static'` — НЕ surgical-edit, а **non-destructive** эскалация: `switch_to_stack`-вариант, который НЕ сносит историю (новый снапшот как child текущего; старый static остаётся rollback-абельным), ИЛИ consent-turn («это статика; пересоздать как приложение? старую версию сохраню в таймлайне») через существующий `_spawn_text_turn`.
3. Health-check на песочнице `dogfood-*`: static → app, кнопки живые, rollback к старому static работает.

**Acceptance-lock (уже в репо):** `apps/api/tests/test_intent_triage.py::test_appification_followup_should_escalate_not_surgical_edit` (xfail strict=False — XPASS, когда фикс приземлится).

## PROPOSAL P-BS5 — вернуть tgbot/api в NL-пайплайн (2026-06-16, run #4)

**Проблема (доказана):** `telegram-bot-aiogram` и `fastapi-postgres` — полноценные, провижабельные стеки (stack_registry.py:63, `is_fullstack` true, prompt_builder `_TGBOT_STACK` + `_BACKEND_TEMPLATES`, schemas/project.py:46), но НЕДОСТИЖИМЫ из любого пользовательского запроса. discovery — единственный, кто выбирает стек по NL — имеет `_STACKS = {static, fullstack, nextjs_entities, spa}` (discovery.py:53) и `_SYSTEM`-меню, которое предлагает LLM ровно эти 4 (формат build-JSON хардкодит `"stack":"static|spa|nextjs_entities|fullstack"`, discovery.py:450). Любой out-of-vocab pick форсится в `static` (discovery.py:843). Параллельно `_DISCOVERY_STACK_TO_TEMPLATE` (stack_routing.py:47) тоже без tgbot/api — даже если discovery вернёт "tgbot", routing вернёт None → стек не переключится. Live-репро (prod-контейнер `run_discovery(force_build=True)`): «сделай телеграм-бота для записи в барбершоп» → `nextjs_entities`; «telegram бот … без сайта» → `fullstack`. Единственное упоминание tgbot в NL-слое — инструкция nextjs-промпта «скажи юзеру: пересоздай как tgbot через омнию и ОСТАНОВИСЬ» (prompt_builder.py:1124-1128) — тупик: нет ни кнопки, ни discovery-пути это сделать.

**Почему не фикшу вслепую:**
1. Минимум 3 кодовых поверхности: discovery `_STACKS` + `_SYSTEM`-меню (+ детект-сигнал «бот/telegram»), `_DISCOVERY_STACK_TO_TEMPLATE`, и first-build provisioning, который должен принять backend-only шаблон.
2. **Нет web-preview:** tgbot/api — backend-only, нет `dev_url`-страницы. Таймлайн/preview-UX ожидает живой URL → бот покажет «сломанный» preview. Нужна отдельная trim-UX для бэкенд-стеков (логи/статус вместо iframe).
3. **TELEGRAM_BOT_TOKEN:** бот без токена — мёртв при старте (main.py:38 «missing TELEGRAM_BOT_TOKEN — set it in the Omnia secrets panel»). Авто-роутинг бота БЕЗ сбора токена = DOA, хуже чем веб-апп. Нужен secrets-UX (cross-zone apps/web) ДО того как роутить.

**План безопасного фикса (для отдельного прогона/согласования):**
1. Детект «телеграм-бот / aiogram / бот для X» (тайтовый, против фолс-фолса на «кнопка телеграма на сайте») → discovery предлагает tgbot как явный выбор/вопрос consent.
2. Добавить tgbot (и api) в `_STACKS` + `_SYSTEM`-меню + `_DISCOVERY_STACK_TO_TEMPLATE` ОДНОВРЕМЕННО (иначе двойной разрыв).
3. Backend-only preview-mode + secrets-turn (запросить TELEGRAM_BOT_TOKEN до запуска) через `_spawn_text_turn`/secrets-panel.
4. Health-check на песочнице `dogfood-tgbot-*`: запрос бота → tgbot-контейнер, preview не сломан, бот стартует с токеном.

**Acceptance-lock (уже в репо):** `apps/api/tests/test_stack_routing.py::test_tgbot_should_be_a_discovery_stack` + `::test_tgbot_should_route_to_template` (xfail strict=False — XPASS, когда фикс приземлится); `::test_tgbot_is_currently_unreachable_evidence` (зелёный сегодня).

## PROPOSAL P-EC — storefront/commerce-архетип (2026-06-16, run #5)

**Проблема (доказана, BS-6):** реальный prod-промпт «Магазин цветов «Флора»: каталог букетов, КОРЗИНА, оформление заказа с доставкой, отзывы, личный кабинет с историей» (skip_clarify=true) → BS-4 эскалация корректно подняла stack blank→nextjs_entities, entity-аппа собралась (Bouquet/Client/Order/Review + /signin + (app)/dashboard/{orders,profile,reviews}). НО покупательская воронка отсутствует: каталог за /signin, нет корзины, нет checkout. Причина — entity writer SYSTEM_PROMPT.md зашивает authed-dashboard как единственный архетип (`/dashboard` binding; «app screens, not a landing»; route-per-entity+dashboard). Bouquet=access:public, но публичный каталог-UI не генерится; CTA «В каталог»→/signin.

**Почему не фикшу вслепую:** добавить storefront-воронку — это НЕ prompt-твик. Нужны: (1) распознавание commerce/storefront-ниши (магазин/каталог/корзина/доставка/заказ) vs internal-tool; (2) публичные роуты `src/app/(shop)/{catalog,product/[id],cart,checkout}` помимо authed (app)/dashboard; (3) клиентская корзина (state) + checkout, пишущий Order для гостя/клиента; (4) kit-компоненты витрины (ProductGrid/ProductCard/CartDrawer/CheckoutForm) — часть уже есть (storefront-hero, gallery-grid), но cart/checkout/public-catalog нет. Это новый template-архетип + writer-инструкции + verify по нишам (магазин/доставка/услуги) → крупно и кросс-зонно (orchestrator templates + api system-prompt). Шип вслепую сломает текущие dashboard-аппы.

**План безопасного фикса (для отдельного прогона/согласования):**
1. Сигнал «storefront»: niche/brief содержит каталог+корзина+заказ/доставка → пометить gen как commerce.
2. Entity template: добавить опциональную public-shop ветку (catalog/product/cart/checkout) рядом с (app)/dashboard; CTA лендинга «В каталог» → публичный `/catalog`, НЕ /signin.
3. Order: гостевой/клиентский checkout-путь (не только owner-CRUD).
4. Verify на песочнице dogfood-shop-*: гость видит каталог без логина, добавляет в корзину, оформляет заказ; админ-dashboard остаётся для владельца.
**Дополнительно (BS-6b, отдельно):** entity-палитра — заставить writer переопределять `:root`-токены под brief-палитру (а не inline-хекс), ИЛИ детерминированный entity-palette-guard, инжектящий `:root{--primary…}` из brief → чинит невидимые/рассинхроненные shadcn-компоненты.
## PROPOSAL P-BS7 — консьюмер-букинг не должен запирать запись за клиентским auth (2026-06-16, run #5)

**Проблема (доказана live + кодом):** запрос «лендинг автосервиса … запись на ремонт онлайн» при ЯВНОМ выборе «Лендинг» в quiz → discovery собирает `nextjs_entities` (контейнерный entity-app со staff-кабинетом), и сгенерённый page.tsx делает ВСЕ конверсионные CTA `href="/signin"` (hero «Записаться онлайн», 3× прайс-карты «Записаться», CTA-band) — клиент обязан зарегистрировать аккаунт, чтобы записаться на услугу. Корень: `_BACKEND_SIGNALS` (discovery.py:90) содержит lead-capture-стемы «запись на», «бронирован», которые трактуются как нужда в аккаунтах/CRUD; `_infer_stack_from_text` (discovery.py:130, upgrade-net 867-870) форсит nextjs_entities; негативный net `_explicit_no_backend` (879-885) рескьюит ТОЛЬКО дословное «без регистрации». Live: «запись на ремонт» / «запись на стрижку» / «бронирование столика» → все `nextjs_entities`; «лендинг кофейни» → None. Явный quiz-pick «Лендинг» в stack-решении не участвует.

**Почему не фикшу вслепую:**
1. **Двунаправленный риск.** Просто вырезать/сузить «запись на»/«бронирован» из `_BACKEND_SIGNALS` → недо-эскалация настоящих букинг-/CRM-аппов («система записи с кабинетом мастера», «бронирование с личным кабинетом»), которые backend реально нужен. Сигнал амбивалентен: «запись на стрижку» (consumer lead) vs «управление записями» (staff CRUD) различаются контекстом, не стемом.
2. **Архитектурный пробел.** Правильный результат для консьюмер-лендинга с записью — public lead-capture форма (имя+телефон+услуга+время → staff), БЕЗ клиентского аккаунта. У Omnia нет стека/паттерна между `static` (нет backend для лида) и `nextjs_entities` (полный auth-gated entity-app). Лид-форме нужен куда-то слать заявку.
3. **Даже при правильном стеке** customer-facing «Записаться» не должен вести на /signin — это template/prompt-builder-уровень (writer сам выбрал href:"/signin"). Фикс рассыпан по поверхностям.

**План безопасного фикса (для отдельного прогона/согласования):**
1. Различить consumer-lead-booking vs staff-CRUD-booking: «запись на <услуга>» / «бронирование <ресурс>» на ЛЕНДИНГЕ без слов про кабинет/аккаунт/роли → lead-capture, не auth-stack. Тайтовый детект против фолс-фолса; учитывать явный quiz-pick «Лендинг» как сигнал-кап.
2. Дать lead-capture-путь: либо static-лендинг с формой → существующий backend для заявок (email/CRM-стаб), либо новый «landing+leadform»-вариант nextjs_entities, где CTA ведёт на public `/booking` форму, а НЕ на /signin (auth — только для staff-кабинета, отдельным входом).
3. Health-check на песочнице `dogfood-*`: запрос букинг-лендинга → primary CTA = живая форма записи (имя/телефон/услуга), НЕ /signin; заявка долетает.

**Acceptance-lock (уже в репо):** `apps/api/tests/test_stack_routing.py::test_consumer_booking_landing_should_not_force_customer_auth` (3 xfail strict=False — XPASS, когда фикс приземлится) + `::test_consumer_booking_landing_is_currently_force_escalated_evidence` (3 зелёных сегодня).

## RUNS LOG (append-only — одна строка на прогон)
<!-- время MSK | сценарий | stack/gen_mode/модель | design-балл | тупняк | действие | PASS/FAIL -->
- 2026-06-16 14:44 MSK | #5 сценарий-4 (free, реальный prod-промпт «Магазин цветов «Флора»: каталог+корзина+оформление+отзывы+кабинет», skip_clarify=true; rotation 4 mod 5) | nextjs_entities / freeform-render / deepseek-v4-pro (writer) | дизайн ~4/10 (broken hero img → gold-плейсхолдер; НЕВИДИМАЯ вторичная CTA white-on-white; dark-on-dark low contrast) | BS-6: e-commerce запрос схлопывается в authed-dashboard CRM — каталог за /signin, НЕТ корзины/checkout, хотя бриф их явно назвал (SYSTEM_PROMPT.md:82,96,118 зашивает «/dashboard binding, app not landing»). BS-4-эскалация сама сработала end-to-end ✓ | PROPOSAL P-EC (новый storefront-архетип, крупно+кросс-зонно → не шипить вслепую) + design-нота BS-6b | PASS (func FAIL пойман: entities+routes+CTA+screenshot+system-prompt; тупняк локализован file:line, доказан)
- 2026-06-16 13:02 MSK | #0 H1-репро | n/a (анализ кода + live decide_intent в prod-контейнере, генерация не запускалась) | n/a | H1 CONFIRMED: follow-up app-ification → CHEAP surgical-edit статики, stack-эскалация структурно недостижима (messages.py:626,675); 4/4 формулировки → CHEAP в prod | repro-тест (xfail) + PROPOSAL P-H1 (фикс деструктивный → не шипить вслепую) | PASS (тупняк локализован, доказан)
- 2026-06-16 14:22 MSK | #3 сценарий-1 (CRM с нуля, skip_clarify=true = quiz-путь) | PRE: blank/freeform/deepseek-v4-pro-thinking → POST: nextjs_entities/entity-app | ~8/10 (бриф: emerald+amber, Libre Franklin+Lora, нишевая утилитарность) | BS-4: first-build с пропущенным discovery не эскалирует stack → app-запрос = freeform-статика с мёртвыми кнопками входа (messages.py:706) | FIX 5878059 (на first-build без discovery переиспользовать `_infer_stack_from_text`; deploy api+worker; repro в test_stack_routing.py) — verify: тот же промпт → template flips blank→nextjs_entities, реальный CRM-бриф (clients/bookings/notes + auth-кабинет) | PASS. Сайд-нота: /api/models 500 (model config provider='google' ∉ ModelInfo literal) — отдельный пре-существующий баг, не фиксил (1 фикс/прогон)
- 2026-06-16 13:24 MSK | skipped: guard-3 sequential-lock fresh (_routine/dogfood.lock = 10:20:08Z, age 4 мин < 15) — параллельный прогон #1 в полёте, lock не снимал | n/a | n/a | yielded | SKIP
- 2026-06-16 14:07 MSK | #2 SPA-tool «калькулятор ипотеки с графиком» (rotation 2 mod 5; H1 уже CONFIRMED+locked → ротация) | nextjs_entities / freeform / deepseek-v4-pro-thinking | 7/10 (лендинг чистый монохром; но это лендинг, не калькулятор) | BS-3: «без регистрации» ×3 → LLM-discovery выбрала nextjs_entities → калькулятор за /signin + БД-история; safety-net однонаправленный (discovery.py:838) | fix 04bb7cf (негативный safety-net `_explicit_no_backend`→spa, 3 теста, деплой api/worker, health 200, VETO live-verified) | PASS (func FAIL пойман по скриншоту+логам+DB, фикс доставлен и доказан)
- 2026-06-16 14:46 MSK | #5 сценарий-1 вариант (лендинг автосервиса «ТурбоФикс» — реальная E2E-генерация через normal discovery path, skip_clarify=false, quiz «Лендинг»+«Строгое») | nextjs_entities / freeform / deepseek-v4-pro-thinking · preset=local-services | дизайн 7.5/10 (тёмный+crimson, нишевый automotive, иерархия ок; минусы: crimson-радиал чуть AI-generic, hero тесноват) · ФУНКЦ FAIL | BS-7: консьюмер-букинг «запись на ремонт» форс-эскалирован в auth-gated nextjs_entities, ВСЕ 5 CTA «Записаться» → /signin (клиент обязан зарегаться, чтобы записаться); discovery.py:90,130 — `_BACKEND_SIGNALS` «запись на»/«бронирован» = proof аккаунтов, negative-net ловит только «без регистрации», quiz-pick «Лендинг» без веса | acceptance-lock test_stack_routing.py (3 xfail + 3 evidence, 27 passed/5 xfailed) + PROPOSAL P-BS7 (фикс двунаправленно-рискованный + архитектурный пробел lead-form без клиентского auth → не шипить вслепую) | PASS (новая слепая зона: func FAIL пойман по rendered page.tsx + screenshot + live discovery, локализован, заперт тестом)
- 2026-06-16 14:22 MSK | #4 Telegram-бот «бот для записи в барбершоп» (scenario 3 — выбран при старте, когда RUNS=3; параллельный run #3 приземлился по ходу, сместив rotation; tgbot помечен «ранее orphaned» — проверено) | n/a (live `run_discovery(force_build=True)` в prod-контейнере; генерация не запускалась — стек недостижим by design) | n/a | BS-5: tgbot/api ОРФАНЫ — discovery `_STACKS`/`_SYSTEM`-меню (discovery.py:53,418-450) + `_DISCOVERY_STACK_TO_TEMPLATE` (stack_routing.py:47) без tgbot; запрос бота → `nextjs_entities`/`fullstack` (веб-апп), готовый telegram-bot-aiogram unreachable | acceptance-lock test_stack_routing.py (2 xfail + evidence) + PROPOSAL P-BS5 (фикс крупный: вокаб+routing+backend-preview+token-UX → не шипить вслепую) | PASS (тупняк локализован, live-доказан, заперт тестом)
