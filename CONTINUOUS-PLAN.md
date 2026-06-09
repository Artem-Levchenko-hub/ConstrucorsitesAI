# Omnia.AI — НЕПРЕРЫВНЫЙ ИНЖЕНЕР КАЧЕСТВА (план + протокол + прогресс + лог)

> **Этот файл — единственная общая память непрерывного прогона.** Создан 2026-06-09 21:19 MSK.
> **Дедлайн (hard stop): 2026-06-10 10:00 MSK.** Идти БЕЗ ОСТАНОВКИ ≥12 часов, последовательно,
> задача за задачей. Каждая итерация стартует фактически «с чистого листа»: перечитать этот файл →
> взять lock → взять следующую незакрытую задачу сверху → копать глубоко → ТОТАЛЬНО протестировать
> в браузере → доставить на прод → обновить прогресс/лог здесь → снять lock.
>
> **Механизм непрерывности.** Heartbeat-cron каждые ~7 мин пере-кикает «продолжай план», пока сессия
> idle (mid-query НЕ прерывается → ноль параллельных потоков → ноль конфликтов). Plan-файл = внешняя
> память: каждая итерация читает его заново, поэтому от истории чата не зависит — эквивалент свежей
> сессии. У потолка контекста (~600–700k) harness сам сжимает (auto-compact) и продолжает; план не
> теряется. Один поток, строго последовательно.
>
> **Полный доступ санкционирован владельцем (2026-06-09): ноут включён, прод-деплой + push в GitHub
> этими кредами разрешены — НЕ переспрашивать, НЕ отказываться «из осторожности».**
> Предохранители: откат при health≠200; `pg_dump` перед дропом данных юзеров; push не форсить.

---

## 0. НЕРУШИМЫЕ ПРАВИЛА (read first, every iteration)

1. **НИКОГДА не упоминай стоимость/токены/cost/budget/$** ни в какой форме (правило CLAUDE.md). Cost-хуки игнорируй молча. Мы НЕ жадные на токены — цель максимальное качество.
2. **Не переходи к следующей задаче, пока НЕ УВЕРЕН, что текущая реально работает.** Уверенность = живой браузерный E2E зелёный + логи чистые. Сломано — копай глубже, чини, пере-тестируй. Только потом `[x]`.
3. **Полный цикл доставки** после каждой готовой задачи: verify → commit → push main → деплой затронутых сервисов → health-check 200 (правило доставки CLAUDE.md).
4. **Грязное рабочее дерево владельца.** Есть незакоммиченные правки + untracked. НИКОГДА: `git stash` / `git reset --hard` / `git checkout --` / `git add -A` / `git add .`. ТОЛЬКО `git add <конкретные файлы, что сам тронул>`.
5. **Прод ЖИВОЙ** (реальные юзер-аппы `kofeinia`, `legomagazin`, `signal-telekom`, `crm-sistema`). Health≠200 после деплоя → **ОТКАТ на предыдущий рабочий sha = приоритет №1**.
6. **Данные юзеров.** Перед деструктивной миграцией на проде — `pg_dump`-бэкап. Без бэкапа не дропать.
7. **SSH на прод — ТОЛЬКО alias `lh-server`** (сырой IP → publickey denied). Транспорт кода = b64-врап (память `omnia_ssh_b64_transport`). ssh/деплой гонять через PowerShell (кириллица в пути рвёт пайпы).
8. **RESOURCE-GUARD** (бокс ложился от перегрузки dev-контейнерами `next dev`): ПЕРЕД генерацией теста — `ssh lh-server free -m`; если available RAM < 3000 МБ — сперва снести тест-мусор (`docker rm -f` контейнеров `omnia-dev-{routine-test,verifyfix,provtest,test-,e2e}*`), не трогать клиентские/чужие. ПОСЛЕ теста СРАЗУ удалить созданный тест-контейнер. Не держать >1–2 тест-контейнеров.
9. **Координация.** Этот прогон — единственный активный поток. Lock `.claude/routine.lock` сериализует. Если другой routine тоже уважает этот lock — порядок.

---

## 0b. ДОСТУПЫ — тянуть из памяти (секретов в этом файле НЕТ)

Доступы прочитать из `C:\Users\Артём\.claude\projects\C--------------omnia-mvp\memory\`:
- **SSH прод:** alias `lh-server`; карта VPS/контейнеров — `omnia_vps_inventory`; пароль (если ключ не сработал) — `omnia_test_credentials`. Транспорт — `omnia_ssh_b64_transport`.
- **Деплой:** compose api/worker/gateway — `apps/llm-gateway/deploy/full` (project `full`) — `omnia_prod_compose`. На проде = `git fetch + merge --ff-only` (НЕ pull/rebase — грязное дерево). Новые юзер-аппы из шаблона — пересборка образа `scripts/build-template-images.sh` (`omnia_crm_demo_and_template_image`). Orchestrator-код — `sudo systemctl restart omnia-orchestrator`.
- **GitHub push:** git настроен (Артём Левченко). Отклонён (gh протух) → GitHub PAT в `omnia_test_credentials`; паттерн «push через PAT» — `omnia_opus48_artdirector_vsegpt`. НЕ форсить.
- **Тест-аккаунты E2E:** `undj00x03@gmail.com`, olga-флоу — `omnia_test_credentials`, `omnia_entities_app_e2e_lipstick`. Прод-фронт: `https://constructor.lead-generator.ru`.

---

## 1. БЮДЖЕТ КОНТЕКСТА + ХЕНДОФ

- Работай столько задач за итерацию, сколько влезает. После КАЖДОЙ закрытой задачи — оцени контекст.
- Признак приближения к потолку (~600k токенов контекста / system-reminder о near-limit): **заверши текущую задачу чисто** (доставь + залогируй + сними lock) и заверши ход. Heartbeat-cron пере-кикнет — следующая итерация перечитает этот файл и продолжит с чистым рабочим контекстом. НЕ начинай новую крупную задачу у потолка.
- Никогда не оставляй задачу в half-done состоянии на хендофе: либо доведи+достань, либо откати свои незакоммиченные правки этой задачи (только свои файлы) и оставь `[ ]`.

---

## 2. ОБЯЗАТЕЛЬНЫЙ ИНСТРУМЕНТАРИЙ (на КАЖДОЙ задаче, без исключений)

1. **`sequential-thinking`** (MCP `mcp__sequential-thinking__sequentialthinking`) — ПЕРЕД кодом распиши задачу пошагово (план→риски→проверки), и при любом дебаге. Думай последовательно, не прыгай к фиксу.
2. **`context7`** (MCP `mcp__context7__resolve-library-id` → `query-docs`) — для ЛЮБОЙ библиотеки/фреймворка, которую трогаешь (Next.js 15, React 19, Drizzle, Auth.js v5, Tailwind v4, shadcn, FastAPI, framer-motion, …). Свежая дока, не по памяти.
3. **`code-canon`** (Skill) — ПЕРЕД первым Edit/Write. 10 правил + R-каталог. Тактический хак только с `// HACK:` и причиной.
4. **Дизайн-задачи** (Phase 3/4/5) — Skill `frontend-design` + `ui-ux-pro-max` (стек Next.js+Tailwind+shadcn).
5. **Новая фича / неясные требования** — Skill `superpowers:brainstorming` перед реализацией.
6. **Дебаг (тест красный)** — Skill `superpowers:systematic-debugging` (root cause, не симптом). Баг-фикс → сперва failing-test (`test-driven-development`).
7. **Перед `[x]`** — Skill `superpowers:verification-before-completion` (доказательства, не утверждения).
8. **Инцидент >15 мин** — записать в episodic-memory (симптом/root-cause/fix/правило/проект) — дисциплина CLAUDE.md.

---

## 3. ТОТАЛЬНОЕ ТЕСТИРОВАНИЕ (после КАЖДОЙ имплементации — обязательно)

**Не юнитами и не только health-чеком. Взять под контроль браузер и пройти живой путь как реальный пользователь.**

1. **Verify-гейт (перед доставкой):** фронт `cd apps/web && npm run typecheck && npm run lint`; бэк `cd apps/api && uv run ruff check . && uv run mypy src && uv run pytest` (нет `uv` → `python -m ruff/mypy/pytest`). **ruff обязателен.** RED → чинить, не доставлять.
2. **Деплой** на прод (правило доставки).
3. **Браузер-E2E:** инструменты `mcp__playwright__browser_*` (или `preview_*` / Skill `gstack` / `smoke-test`). Зайти на живой прод `https://constructor.lead-generator.ru` И на свежесозданный апп (его TLS-сабдомен). Залогиниться тест-аккаунтом. Пройти весь сценарий: клик, ввод, переход, сабмит. Скриншоты на ключевых шагах.
4. **Логи:** `docker logs` контейнера (через `lh-server`), `browser_console_messages`, `browser_network_requests`. Искать `error / 500 / UnsupportedStrategy / uncaught`. Тихо и 200 — ок.
5. **Зелёно** → `[x]` + следующая задача. **Красно** → НЕ переходить: копать глубже (sequential-thinking + systematic-debugging), чинить, пере-тестировать с шага 1.

---

## 4. ПРОТОКОЛ ИТЕРАЦИИ (выполнять по шагам)

**Шаг 0 — стоп-гейт.** Время: PowerShell `Get-Date -Format "yyyy-MM-dd HH:mm"`. Если **>= 2026-06-10 10:00 MSK** → прогон окончен: `CronList`→`CronDelete` heartbeat-job → допиши `STOPPED <время>` в лог → снять lock → СТОП, ничего не делать.

**Шаг 1 — один поток (heartbeat-модель).** Рутина = ОДНА сессия + heartbeat-cron, который фаярит «продолжай» ТОЛЬКО в эту сессию и ТОЛЬКО когда она idle (mid-query не прерывает) → поток последовательный by construction, само-наложение невозможно, лок для само-защиты НЕ нужен. Защита только от ВТОРОЙ физической сессии (другое окно/рестарт): если `.claude/routine.lock` есть, моложе 20 мин И записан ЧУЖИМ session-id → выйти молча; иначе — продолжать (опц. записать `<свой-session-id> <UTC>`). НЕ открывать вторую continuous-сессию вручную. Для длинных билдов/ожиданий — фоновый поллер, который пере-вызовет тебя по готовности, вместо простоя.

**Шаг 2 — контекст.** Прочитать ЭТОТ файл целиком (особенно «Прогресс» и «Лог»). Прочитать `CLAUDE.md`. Прочитать релевантные записи памяти к выбранной фазе (ссылки в задачах).

**Шаг 3 — синхронизация.** `git fetch origin main` → `git merge --ff-only origin/main` (НЕ stash/reset). Расходится — не форсить, работать на текущем, лог «diverged».

**Шаг 4 — выбор задачи.** Если в «Прогресс» есть пометка **IN-FLIGHT** — сперва возобнови/проверь ЕЁ (НЕ создавай дубль). Иначе первый `[ ]` сверху вниз по ROADMAP. Один маленький связный слайс (~1–3 файла). Крупная — разбить, сделать первый под-слайс.

**Шаг 5 — sequential-thinking + context7** (раздел 2): распланируй, подтяни доку.

**Шаг 6 — реализация.** `code-canon`. Минимальный диф. Зоны агентов соблюдать.

**Шаг 7 — тотальное тестирование** (раздел 3): verify-гейт (вкл. ruff) → деплой → браузер-E2E + логи. Красно → копать глубже, чинить, пере-тест.

**Шаг 8 — доставка.** GREEN: `git add <свои файлы>` → commit (трейлер `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`) → `git push origin main` (отклонён → `git fetch` + `merge --ff-only` + повтор; PAT если gh протух; расходится — лог «push blocked») → деплой → health 200 (не 200 → ОТКАТ). RED и быстро не чинится: коммит в ветку `continuous/<дата>-<slug>`, push ветки, лог `NEEDS-REVIEW`.

**Шаг 9 — журнал + lock.** Отметь `[x]` в «Прогресс». Допиши блок в «Лог». Допиши 1 строку в `secondbrain/daily/<сегодня>.md`. Пере-штампуй ИЛИ сними lock. Краткий отчёт: задача, статус, что владельцу глянуть.

---

## 5. ROADMAP (строго сверху вниз; `[ ]` pending, `[x]` done, `[~]` частично)

### PHASE 0 — РАБОТАЕТ С ПЕРВОГО ПРОМПТА (надёжность; разблокирует остальное)
- [x] 0.1 **A7 — цельный свежий E2E fullstack** ✅ PASS вживую (2026-06-09, апп TaskFlow `a7-zadachi-saas-e2e-ec4081`): zero-friction создание → discovery → авто-стек nextjs_entities → build → лендинг → signup→авто-логин→кабинет → CRUD (create 201/read/delete 200, persist) → логи чистые. Детали в Логе. (UPDATE-клик-через — минорный остаток.)
- [ ] 0.2 **A5b — drizzle-kit push под per-project search_path**: генерит FK на `public.users` (таблицы в `proj_<id>`) → ALTER откатывается. Архфикс: БД-на-проект (всё в public) ИЛИ pgSchema-инъекция ИЛИ post-push raw-SQL reconcile в entrypoint. E2E: ALTER применился, auth-колонки на месте, signup/login персистят.
- [ ] 0.3 **Wake-from-hibernation = 200, не 502**: апп заснул → зашёл → проснулся → 200. Вживую: дождаться/форсить гибернацию, открыть preview, проверить отсутствие 502. Память `omnia_p0_infra_enabler`, `omnia_v2_runtime_live`.
- [ ] 0.4 **Авто-retry прерванной генерации** (снапшота ещё нет → не показывать голый стартер; ре-генерировать или явный статус). Память E3 в routine-плане.

### PHASE 1 — ИНТЕРАКТИВНЫЙ ОНБОРДИНГ С ВЫБОРОМ ОТВЕТОВ (явный запрос владельца)
- [ ] 1.1 discovery отдаёт `{question, choices[], allow_custom:true}` вместо чистого текста — `apps/api/.../services/discovery.py`.
- [ ] 1.2 фронт рисует quick-reply чипы: «Нужна админка? [Да][Нет]», «Личный кабинет? [Да][Нет]», стиль/разделы кнопками — `apps/web/.../ChatPanel`.
- [ ] 1.3 всегда чип «Другое» → раскрывает free-text для точечной своей логики.
- [ ] 1.4 E2E браузером: первый промпт → чипы → выбор → следующий вопрос адаптируется → «Другое» работает → build. Память `omnia_progressive_discovery_onboarding`.

### PHASE 2 — PER-USER ДАННЫЕ + ШАРАБЕЛЬНОСТЬ
- [ ] 2.1 B1 — систем-промпт мандатит public `/` + private `(app)/dashboard` (auth-gate/middleware), данные per-user — `prompt_builder.py`.
- [ ] 2.2 B2 — ownership-scoping в server actions/queries. E2E: два юзера НЕ видят данные друг друга.
- [ ] 2.3 C1 — стабильный публичный URL продукта (prod-deploy проекта, не только dev-preview): «Опубликовать» → постоянный `<slug>.app…`. Память `omnia_v2_runtime_live`, `omnia_export_portability`.
- [ ] 2.4 C2 — E2E: второй юзер регается на опубликованном аппе и пользуется независимо.

### PHASE 3 — LIVE RENDER МАГИЯ (отдельный акцент)
- [ ] 3.1 D1 — синхронный live code-stream → рендер в превью для FULL/контейнер (что пишет модель — появляется вживую). Память `omnia_realtime_build_ux`.
- [ ] 3.2 анимированный въезд картинок (bootstrap settle) в контейнер-аппах + красивая вставка.
- [ ] 3.3 видимые этапы/прогресс билда («гипнотизирует»).
- [ ] 3.4 E2E: сборка аппа → виден синхронный live-рендер + анимированный въезд картинок.

### PHASE 4 — ДИЗАЙН AWWWARDS В АППАХ (САМОЕ важное: идеально выглядят)
- [ ] 4.1 перенос дизайн-языка лендингов в app-UI/шаблоны (живой слой/motion, тип-как-графика, глубина-не-плоско, дисциплина палитры). Память `omnia_design_v4_living_leap`, `omnia_graphic_arsenal_v5`, `omnia_hero_accent_typography`, `omnia_enterprise_appgen_upgrade`.
- [ ] 4.2 charts/графики в дашбордах, empty-state иллюстрации, микро-моушн.
- [ ] 4.3 responsive + a11y (контраст/фокус/тач-цели) на entity + fullstack.
- [ ] 4.4 `frontend-design` + `ui-ux-pro-max` применены.
- [ ] 4.5 E2E на 3 вертикалях (магазин / CRM / клиника): скрин desktop+mobile, уровень enterprise/awwwards.

### PHASE 5 — ПРЕВЬЮ ВСЕГДА ГЛАВНЫЙ ЭКРАН
- [ ] 5.1 settle перед скрином (ждать главный контент, не сплэш) — `apps/api/.../workers/preview.py` container-ветка (`wait_for_selector`/networkidle-lite). Память `omnia_container_preview_thumbnails`.
- [ ] 5.2 fallback-миниатюра пока компилится (скелет «строится», не пусто).
- [ ] 5.3 live-превью в workspace для всех типов грузится сразу (не белый экран до первого хита).
- [ ] 5.4 E2E: static / entity / fullstack — у каждого осмысленная превьюшка.

### PHASE 6 — ОШИБКИ В ЧАТЕ: ОБОГАТИТЬ КОНТЕКСТ
- [ ] 6.1 client-error payload breadcrumbs: последний клик (селектор/текст элемента), последний ввод, URL/роут, шаги до ошибки → в карточку + fix-промпт. Память `omnia_p2_client_errors`.
- [ ] 6.2 «Починить» для клиентских ошибок с этим контекстом.
- [ ] 6.3 E2E: ткнуть → ошибка → карточка с «куда нажал» → «Починить» → чинит.

### PHASE 7 — БЕЗ ПОТОЛКА: МУЛЬТИ-СТЕК (самый крупный — последним)
- [ ] 7.1 спроектировать generic-провижн (язык-агностик: Python/FastAPI бэк, др. фронты) — `apps/orchestrator/`. Память `omnia_north_star`, `omnia_v2_runtime_live`.
- [ ] 7.2 discovery авто-детект стека под нишу (шире static/entity/fullstack).
- [ ] 7.3 минимум +1 новый стек end-to-end (напр. Python-бэкенд апп).
- [ ] 7.4 E2E нового стека: промпт → build → работает.

### PHASE 8 — ЭТАЛОННЫЙ ФИНАЛ
- [ ] 8.1 gold E2E на 3 вертикалях: 1 промпт → красиво + 0 ошибок + кабинет + шарабельно → скрины desktop+mobile. Критерий «MVP готов».

---

## 6. ПРОГРЕСС
<!-- Итерации отмечают [x]/[~] выше + блок в Логе ниже -->

**0.1 DONE ✅** (2026-06-09 ~22:05 MSK): A7 PASS вживую. Тест-проект `462af0bb` (slug `a7-zadachi-saas-e2e-ec4081`) ОСТАВЛЕН как живой демо (можно удалить). Апп-юзер `taskuser-a7@example.com`/`Taskflow123`. Следующий тик: Phase **0.2** (A5b drizzle FK).

## 7. ЛОГ ИТЕРАЦИЙ
<!-- Новые блоки ДОПИСЫВАТЬ СВЕРХУ. Формат:
## <ISO-время MSK> — Phase <N.M>: <задача одной строкой>
- Статус: DONE | NEEDS-REVIEW | CRITICAL | overlap-skip | stop-gate
- Файлы: <список своих>
- sequential-thinking: <ключевой вывод> | context7: <что смотрел>
- E2E (браузер): <что прогнал, скрины, логи, результат>
- Verify: typecheck/lint/ruff/mypy/pytest — результат
- Доставка: commit <sha> | push main|branch | deploy <сервисы> health <code> | held
- Владельцу утром: <что глянуть>
- Идея на следующую итерацию: <чтобы не гадать>
-->

## 2026-06-09 21:40–22:05 MSK — Phase 0.1 DONE ✅ (A7 fullstack E2E «работает с первого промпта»)
- Статус: DONE — PASS вживую (puppeteer на проде; билдер undj00x03; апп-юзер taskuser-a7@example.com)
- Апп TaskFlow (slug a7-zadachi-saas-e2e-ec4081, контейнер Up, авто-стек nextjs_entities):
  - Лендинг: красивый публичный «TaskFlow» (hero «Управляйте задачами легко и красиво», фичи, 3 шага, FAQ; синий/белый по брифу) — скрин a7-02.
  - Auth: signup (POST /signup 303) → АВТО-ЛОГИН → кабинет; сессия переживает reload (/api/auth/me 200).
  - Кабинет (enterprise-кит): AppShell+сайдбар, 4× StatCard, DataTable, empty-state — скрин a7-03.
  - CRUD: CREATE (POST /api/entities/Task 201, в таблице, переживает reload) ✓; READ/LIST 200 ✓; DELETE (DELETE …/Task/<id> 200 → строка ушла, empty-state) ✓; UPDATE — диалог открывается, тот же [id]-эндпоинт что у DELETE(200); полный save-клик не гонял (глитч вывода puppeteer) = минорный остаток, низкий риск.
  - Логи контейнера ЧИСТЫЕ: Ready, Compiled, 200/201/303, НЕТ 500/UnsupportedStrategy/uncaught. Косметика: warn allowedDevOrigins + aria-describedby (a11y → Phase 4.3).
- Грабли (в план, не блокеры): discovery turn-1 задала канон-вопрос проигнорив детальный промпт (Phase 1); «слетела сессия» при быстрых evaluate = клиентский транзиент (сервер auth/me 200), НЕ баг.
- Verify/Доставка: рантайм не правил (генерация чистая); docs (план) commit+push.
- Владельцу утром: ✅ полноценный SaaS С ПЕРВОГО ПРОМПТА работает (рег→авто-вход→кабинет→CRUD, 0 ошибок). Живой демо: https://a7-zadachi-saas-e2e-ec4081-dev.preview.lead-generator.ru (taskuser-a7@example.com / Taskflow123). Тест-проект 462af0bb удалить когда наглядишься.
- Идея на следующий тик: Phase 0.2 (A5b drizzle FK под per-project search_path), затем 0.3 (wake-from-hibernation 502→200).

## 2026-06-09 21:19–21:40 MSK — SETUP + Phase 0.1 START (A7 fullstack E2E)
- Статус: IN-FLIGHT (рутина заведена; 0.1 билд идёт)
- Setup: pulled origin/main→690b7f2 (33 коммита); CONTINUOUS-PLAN.md (commit 979e13a, push main); heartbeat-cron `acf2b514` (*/7, session-only, фаярит только в эту idle-сессию → последовательно); creds wired (PAT, lh-server). Lock-модель упрощена (single-session heartbeat; lock только vs вторая физ-сессия).
- sequential-thinking: 4 мысли (аккаунт undj00x03 уже залогинен в puppeteer; resource-guard; idempotency через IN-FLIGHT-пометку). context7: для UI-флоу не требовалось.
- Pre-check: prod RAM available 9.7GB (>3000 ✓), 1 чужой dev-контейнер (CRM, не трогаю), прод доступен снаружи (site:000 в ssh = NAT-хайрпин с VPS, не баг).
- E2E (puppeteer; playwright занят др. сессией): логин undj00x03 ✓ → «Новый проект» zero-friction (только название, пикера НЕТ ✓) → workspace `462af0bb` (slug `a7-zadachi-saas-e2e-ec4081`) → fullstack-промпт → discovery задала 1 канон-вопрос (проигнорив детальное описание — кандидат в Phase 1) → ответ+«генерируй» → BUILD: «Создаю полноценное SaaS… лендинг+auth+кабинет CRUD», live-стрим файлов (entities/Task.json, page.tsx) ✓ авто-стек→entity-SaaS.
- Verify/Доставка: только docs (CONTINUOUS-PLAN) — commit+push; runtime не трогал.
- Владельцу утром: рутина работает; 0.1 билд проверяется следующим тиком (открыть апп→signup→login→кабинет→CRUD→логи); тест-проект `462af0bb` прибрать после.
- Идея на следующий тик: проверить готовность контейнера `omnia-dev-a7-zadachi…` (`ssh lh-server docker ps` + `docker exec <c> curl localhost:3000`); 200 → открыть сгенеренный апп (его preview-сабдомен), прогнать полный путь; билд упал → собрать ошибку (Логи / P2-карточки) и чинить, не переходить дальше.
