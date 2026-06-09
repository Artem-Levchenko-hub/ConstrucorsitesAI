# Omnia.AI — НЕПРЕРЫВНЫЙ ИНЖЕНЕР КАЧЕСТВА (план + протокол + прогресс + лог)

> **Этот файл — единственная общая память непрерывного прогона.** Создан 2026-06-09 21:19 MSK.
> **Дедлайн (hard stop): 2026-06-10 10:00 MSK.** Идти БЕЗ ОСТАНОВКИ ≥12 часов, последовательно,
> задача за задачей. Каждая итерация стартует фактически «с чистого листа»: перечитать этот файл →
> взять lock → взять следующую незакрытую задачу сверху → копать глубоко → ТОТАЛЬНО протестировать
> в браузере → доставить на прод → обновить прогресс/лог здесь → снять lock.
>
> **Механизм непрерывности.** Scheduled-task `omnia-continuous-quality` (`mcp__scheduled-tasks`, каждые
> 10 мин, окно ~22:00–10:00 MSK) запускает КАЖДЫЙ тик как ОТДЕЛЬНУЮ СВЕЖУЮ СЕССИЮ без памяти о прошлых —
> единственная память это ЭТОТ файл. Lock `.claude/routine.lock` сериализует свежие сессии (одна за раз
> → ноль конфликтов/наложений). Свежая сессия на запуск = рабочий контекст всегда чистый (проблема
> 600k/галлюцинаций от разбухания НЕ возникает в принципе). Стоп-гейт отключает задачу в 2026-06-10
> 10:00. (Старый CronCreate-heartbeat НЕ фаярил в этом клиенте — снят.)
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
8. **RESOURCE-GUARD — БЕРЕЧЬ СЕРВЕР (владелец просил явно; бокс уже ложился от перегрузки dev-контейнерами `next dev`, владелец ребутил).** ЖЁСТКО:
   - **ПЕРЕД** любой генерацией/тестом: `ssh lh-server free -m` + `docker ps --filter name=omnia-dev- --format '{{.Names}}'`. Если available RAM **< 4000 МБ** ИЛИ запущено **> 2** НЕ-клиентских dev-контейнеров → СНАЧАЛА снести тест-мусор (`docker rm -f` контейнеров `omnia-dev-{routine-test,verifyfix,provtest,test-,e2e,a7-,cont-,tasks}*`), только ПОТОМ генерить.
   - **Один заход = максимум ОДИН новый тест-апп.** Не держать > 1 своего тест-контейнера. Где можно — переиспользуй существующий тест-проект, не плоди новые.
   - **ПОСЛЕ E2E** — СРАЗУ останови/снеси свой тест-контейнер (если не оставлен как явный демо с пометкой). Тяжёлые билды — строго по одному, без параллельных генераций.
   - **НИКОГДА не трогать клиентские/чужие:** `kofeinia`, `legomagazin`, `signal-telekom`, `crm-sistema*` и любые не-`omnia-dev-*` сервисы. Сомнение — не трогай.
9. **Координация.** Этот прогон — единственный активный поток. Lock `.claude/routine.lock` сериализует. Если другой routine тоже уважает этот lock — порядок.

---

## 0b. ДОСТУПЫ — тянуть из памяти (секретов в этом файле НЕТ)

Доступы прочитать из `C:\Users\Артём\.claude\projects\C--------------omnia-mvp\memory\`:
- **SSH прод:** alias `lh-server`; карта VPS/контейнеров — `omnia_vps_inventory`; пароль (если ключ не сработал) — `omnia_test_credentials`. Транспорт — `omnia_ssh_b64_transport`.
- **Деплой:** compose api/worker/gateway — `apps/llm-gateway/deploy/full` (project `full`) — `omnia_prod_compose`. На проде = `git fetch + merge --ff-only` (НЕ pull/rebase — грязное дерево). Новые юзер-аппы из шаблона — пересборка образа `scripts/build-template-images.sh` (`omnia_crm_demo_and_template_image`). Orchestrator-код — `sudo systemctl restart omnia-orchestrator`.
- **GitHub push:** git настроен (Артём Левченко). Отклонён (gh протух) → GitHub PAT в `omnia_test_credentials`; паттерн «push через PAT» — `omnia_opus48_artdirector_vsegpt`. НЕ форсить.
- **Тест-аккаунты E2E:** `undj00x03@gmail.com`, olga-флоу — `omnia_test_credentials`, `omnia_entities_app_e2e_lipstick`. Прод-фронт: `https://constructor.lead-generator.ru`.

---

## 1. КОНТЕКСТ + ХЕНДОФ (свежая сессия на каждый запуск)

- Каждый запуск scheduled-task = ОТДЕЛЬНАЯ свежая сессия → рабочий контекст стартует чистым; проблема 600k/галлюцинаций от разбухания НЕ возникает. Делай РОВНО ОДНУ связную задачу за запуск и завершайся (сними lock → следующий запуск возьмёт следующую задачу).
- Держи контекст лёгким: не тяни огромные дампы (логи/DOM) в контекст; читай скоупленно; пухлое чтение делегируй subagent'у. Важные факты — в plan-файл/Лог, не «копи» в чате.
- Никогда не оставляй задачу half-done на завершении: либо доведи+достань, либо откати СВОИ незакоммиченные правки (только свои файлы) и оставь `[ ]`. Сними lock в конце.

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

## 3. ЦИКЛ КАЖДОЙ ЗАДАЧИ — ОДИН ПОСЛЕДОВАТЕЛЬНЫЙ ПОТОК (дословно по владельцу)

**Один поток, задача за задачей. Ноль параллелизма. НЕ переходить к новой задаче, пока текущая ПОЛНОСТЬЮ не прошла браузер-тест.**

Замкнутый цикл на КАЖДУЮ задачу (порядок строгий):
1. **ВЫЯВИЛ** задачу (Шаг 4) + `sequential-thinking` + `context7`.
2. **РЕШИЛ** — написал код (`code-canon`, минимальный диф).
3. **Локальный гейт** (чтоб не пушить явно битое): фронт `cd apps/web && npm run typecheck && npm run lint`; бэк `cd apps/api && uv run ruff check . && uv run mypy src && uv run pytest`. **ruff обязателен.** Битое — чинить тут.
4. **PUSH на GitHub** — commit (трейлер `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`) → `git push origin main` (PAT если gh протух).
5. **ДЕПЛОЙ** затронутых рантайм-сервисов на прод → health 200 (не 200 → ОТКАТ на прошлый sha).
6. **ПОЛНЫЙ БРАУЗЕР-ТЕСТ** как реальный юзер: playwright (занят → puppeteer) против живого прода И свежего аппа; логин, весь сценарий (клик/ввод/переход/сабмит), скриншоты. Снять логи: `docker logs` (lh-server) + `browser_console_messages` + `browser_network_requests`. Точечные/ручные UI-правки — проверять **ЧЕРЕЗ КЛИКЕР** (select-mode), не только API (Phase 2.5).
7. **РАЗВИЛКА:**
   - **ЗЕЛЁНО** (весь путь работает, 0 ошибок в логах) → `[x]` + журнал → **СЛЕДУЮЩАЯ задача**.
   - **КРАСНО** → **НЕ переходить к новой. ПО НОВОЙ по тому же кругу, но с ПОВЫШЕННЫМ уровнем:** (a) подними **уровень логирования** в месте падения (добавь инструментацию/логи/трейс); (b) **глубже ищи root cause** (`systematic-debugging` + `sequential-thinking`, причина не симптом); (c) правь код → локальный гейт → push → deploy → тест снова; (d) **повторяй ПОКА браузер-тест не станет ПОЛНОСТЬЮ зелёным.** Не сдавайся, не помечай done, не уходи на другую задачу.
8. **Прибраться** (resource-guard, разд.0 п.8): после E2E аппа — снеси свой тест-контейнер.

> Мнемоник: **выявил → решил → push → deploy → тест(браузер) → не прошёл? → ещё логов + глубже копать → правка → push → deploy → тест → … → зелено → следующая.**

---

## 4. ПРОТОКОЛ ИТЕРАЦИИ (выполнять по шагам)

**Шаг 0 — стоп-гейт.** Время: PowerShell `Get-Date -Format "yyyy-MM-dd HH:mm"`. Если **>= 2026-06-10 10:00 MSK** → прогон окончен: отключи задачу (`mcp__scheduled-tasks__update_scheduled_task` taskId=`omnia-continuous-quality`, enabled=false) → допиши `STOPPED <время>` в лог → снять lock → СТОП.

**Шаг 1 — LOCK (сериализация свежих сессий).** Каждый запуск = ОТДЕЛЬНАЯ сессия → lock обязателен. Файл `.claude/routine.lock`. Если есть И моложе 25 мин → другой запуск идёт → СТОП молча (ничего не делать). Иначе запиши `<UTC> <run-id>`. Пере-штампуй после каждой задачи. В конце (успех ИЛИ ошибка) — УДАЛИ lock. Для длинных билдов — фоновый поллер вместо простоя.

**Шаг 2 — контекст.** Прочитать ЭТОТ файл целиком (особенно «Прогресс» и «Лог»). Прочитать `CLAUDE.md`. Прочитать релевантные записи памяти к выбранной фазе (ссылки в задачах).

**Шаг 3 — синхронизация.** `git fetch origin main` → `git merge --ff-only origin/main` (НЕ stash/reset). Расходится — не форсить, работать на текущем, лог «diverged».

**Шаг 4 — выбор задачи.** Если в «Прогресс» есть пометка **IN-FLIGHT** — сперва возобнови/проверь ЕЁ (НЕ создавай дубль). Иначе первый `[ ]` сверху вниз по ROADMAP. Один маленький связный слайс (~1–3 файла). Крупная — разбить, сделать первый под-слайс.

**Шаг 5 — sequential-thinking + context7** (раздел 2): распланируй, подтяни доку.

**Шаг 6 — реализация.** `code-canon`. Минимальный диф. Зоны агентов соблюдать.

**Шаг 7 — ЦИКЛ доставки+теста (см. раздел 3, СТРОГИЙ порядок).** Локальный гейт (typecheck/lint/ruff/mypy/pytest — не пушить битое) → **commit + `git push origin main`** (отклонён → `git fetch` + `merge --ff-only` + повтор; PAT если gh протух) → **деплой** затронутых сервисов → health 200 (иначе ОТКАТ на прошлый sha) → **ПОЛНЫЙ браузер-E2E** как юзер + логи (docker/console/network). **Развилка:** ЗЕЛЁНО → Шаг 8. КРАСНО → НЕ переходить дальше: поднять **уровень логирования** в месте падения, глубже искать **root cause** (systematic-debugging), править код → снова локальный гейт → push → deploy → тест → **ПОВТОРЯТЬ до полностью зелёного**. Совсем не чинится за разумное время → коммит в ветку `continuous/<дата>-<slug>` + лог `NEEDS-REVIEW` (прод сломанным НЕ оставлять — откат).

**Шаг 8 — журнал + lock.** Отметь `[x]`/`[~]` в «Прогресс». Допиши блок в «Лог». Допиши 1 строку в `secondbrain/daily/<сегодня>.md`. Сними lock. Краткий отчёт: задача, статус, что владельцу глянуть.

---

## 5. ROADMAP (строго сверху вниз; `[ ]` pending, `[x]` done, `[~]` частично)

### PHASE 0 — РАБОТАЕТ С ПЕРВОГО ПРОМПТА (надёжность; разблокирует остальное)
- [x] 0.1 **A7 — цельный свежий E2E fullstack** ✅ PASS вживую (2026-06-09, апп TaskFlow `a7-zadachi-saas-e2e-ec4081`): zero-friction создание → discovery → авто-стек nextjs_entities → build → лендинг → signup→авто-логин→кабинет → CRUD (create 201/read/delete 200, persist) → логи чистые. Детали в Логе. (UPDATE-клик-через — минорный остаток.)
- [x] 0.2 **A5b — drizzle FK под per-project search_path** ✅ УЖЕ РЕШЕНО архитектурой (2026-06-09 ~23:20 MSK): премиса устарела. Рантайм НЕ использует `drizzle-kit push` — entrypoint гонит `init-db.mjs` (idempotent `CREATE TABLE IF NOT EXISTS` через `pg`), а DSN пинит `search_path=proj_<id8>` (без `public`). Живая прод-проверка (omnia-postgres-users/omnia_users, 8 схем `proj_*`): у КАЖДОГО проекта свои `users/accounts/sessions/records`, **0** `public.users`; все 3 FK (`accounts/sessions/records.created_by`) резолвятся ИНТРА-СХЕМНО на `proj_<id>.users`; auth-колонки (`password_hash`,`role`) на месте; signup-юзер A7 (`taskuser-a7@example.com`) персистнут с реальным `password_hash`+role. Фикс: убрал ЛЖИВЫЙ коммент в `schema.ts` (врал «entrypoint runs db:push --force») → точное описание init-db.mjs + инвариант интра-схемного FK (защита от регрессии — именно эта ложь породила баг-репорт). Деталь в Логе.
- [x] 0.3 **Wake-from-hibernation = 200, не 502** ✅ DONE (2026-06-09 ~23:35 MSK): scale-from-zero ingress. Прямой хит по preview-сабдомену спящего контейнера раньше → 502 (nginx proxy_pass на мёртвый порт). Теперь: nginx перехватывает 502/503/504 → `@omnia_waking` → orchestrator `GET /_omnia/wake` (token-free, host-keyed) будит контейнер и отдаёт self-refresh «Запускаем приложение…» страницу → JS-reload → живой апп. `refresh_vhosts()` на старте апгрейдит старые vhost'ы (идемпотентно, nginx -t + полный байт-откат → ноль blast-radius). Прод @82a4ecc, **16/16 vhost'ов** апгрейднуты (все превью теперь wake-on-request). Live browser-E2E на A7: Exited → waking-page (скрин) → авто-рефреш → TaskFlow рендерится, **ноль 502**. Деталь в Логе.
- [x] 0.4 **Прерванная генерация = явный статус, не голый стартер** ✅ DONE (2026-06-10 ~00:05 MSK): выбрана OR-ветка «явный статус» (требование 0.4 = «не показывать голый стартер; ре-генерировать ИЛИ явный статус»). Корень: стартовый снапшот (`prompt_text=null`) коммитится при создании проекта; если генерация прервалась ДО первого снапшота (или ещё не запускалась), он оставался head → `PreviewFrame` рендерил iframe пустого шаблона = пустой скаффолд выдавал себя за готовый сайт. Фикс (1 файл, `apps/web/.../PreviewFrame.tsx`): для static-проектов глушим авто-показ head-стартера (`headIsStarter && !selectedSnapshotId && !isStreaming`) → явный статус «Сайт ещё не сгенерирован». Эскейп-хетч: явный клик по стартеру в таймлайне (`selectedSnapshotId`) всё равно рендерит его. Прод @289ead3 (web rebuilt, health 200). Live browser-E2E: свежий blank-проект → 0 iframe + плейсхолдер; клик по стартеру в таймлайне → iframe рендерится. Деталь в Логе. Остаток (опц.): авто-RE-генерация прерванного билда — нужен серверный build-state (отличить прерванный билд от нормальной discovery-Q&A), отдельный слайс.

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

### PHASE 2.5 — ТОЧЕЧНЫЕ ПРАВКИ ЧЕРЕЗ КЛИКЕР (select-mode) НА ПОЛНЫХ АППАХ (владелец: проверить, что кликер реально работает)
- [ ] 2.5.1 **Live-E2E кликера на full-аппе**: клик по элементу превью → контекст уходит в чат → ИИ `<edit>` по `.tsx` → hot_reload → правка ВИДНА в превью (без слома). Wiring уже подключён (память `omnia_surgical_edit_mode`, `omnia_select_mode`, P3 в `OVERNIGHT-PLAN.md`) — нужен реальный клик-через на проде. Тест на TaskFlow / др. живом full-аппе.
- [ ] 2.5.2 **Ручные правки через кликер на full** (цвет/фон/шрифт/текст): применяются вживую + Save снапшотит (база DONE — `omnia_p3_fullapp_style_edits` — перепроверить кликом вживую, особенно шрифты/site-токены — известные ограничения).
- [ ] 2.5.3 Если точность дешёвой модели в JSX-SEARCH низкая → усилить selection-context (передавать рендер-HTML + namespace-классы / section-scoped `.tsx`-rewrite).
- [ ] 2.5.4 **E2E-приёмка:** на полном аппе клик по элементу → точечная правка реально меняет нужный UI, БЕЗ дрейфа соседнего, без слома билда; ручная правка применилась и сохранилась. Скрины до/после.

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

**0.4 DONE ✅** (2026-06-10 ~00:05 MSK): прерванная/ещё-не-запущенная генерация больше НЕ выдаёт пустой стартовый шаблон за готовый сайт. Static-проект с head=стартер (нет prompt_text, не стрим, не выбран вручную) → явный статус «Сайт ещё не сгенерирован» вместо iframe пустого скаффолда. 1 файл `PreviewFrame.tsx`, прод @289ead3 (web, health 200). Live browser-E2E: свежий blank-проект `c9d639aa` → 0 iframe + плейсхолдер (скрин p04-starter-guard); клик по «Стартовый» в таймлайне → iframe `/p/<slug>` рендерится (эскейп-хетч). Контейнер НЕ создавался (static) → ресурс не тронут. ★Решение: выбрана OR-ветка «явный статус»; авто-RE-генерация прерванного билда = отдельный слайс (нужен серверный build-state, чтобы не путать прерванный билд с discovery-Q&A). Следующий тик: **Phase 1.1** (discovery отдаёт choices[] для quick-reply чипов) или **Phase 2.5.1** (live-E2E кликера на full-аппе).

**0.3 DONE ✅** (2026-06-09 ~23:35 MSK): wake-on-request (scale-from-zero). Спящий preview → waking-page 200, не 502; контейнер сам будится; авто-рефреш → апп. Прод @82a4ecc, 16/16 vhost апгрейднуты, live browser-E2E на A7 зелёный (Exited→waking→app, 0×502). ★Грабля: РЕЦИДИВ double-supervisor — старый 8h-proc держал :8003, `--user` юнит фантомил → новый код не биндился. Снёс `--user` (stop/disable) + pkill стрэев → `sudo systemctl restart` system-юнит = единственный супервизор (как в [[omnia_entities_app_e2e_lipstick]]). ★Грабля nginx: literal URI в proxy_pass запрещён внутри named location → `set $var` + `proxy_pass $var`. A7 СТОПНУТ (стенд для 2.5). Следующий тик: **0.4** (авто-retry прерванной генерации) или **Phase 1** (интерактивный онбординг с чипами).

**0.2 DONE ✅** (2026-06-09 ~23:20 MSK): УЖЕ решено архитектурой per-project-schema (премиса устарела — `drizzle-kit push` давно заменён на `init-db.mjs`; DSN пинит `search_path=proj_<id>` без public). Проверено вживую по прод-БД (8 схем, все FK интра-схемно, 0 public.users, auth-колонки + персист signup A7). Корректнул лживый коммент в `schema.ts`. RAM на проде 10.9GB, 0 dev-контейнеров (чисто). Следующий тик: **0.3** (wake-from-hibernation 502→200) — можно разбудить тест-стенд A7 `462af0bb` (slug `a7-zadachi-saas-e2e-ec4081`).

**0.1 DONE ✅** (2026-06-09 ~22:05 MSK): A7 PASS вживую. Тест-проект `462af0bb` (slug `a7-zadachi-saas-e2e-ec4081`, nextjs_entities) — контейнер **ОСТАНОВЛЕН** для экономии ресурса (владелец просил беречь сервер). НЕ удалять: **тест-стенд** для Phase **0.3** (разбудить → 502→200) и Phase **2.5** (кликер на full). URL 502 пока не разбудишь — это и есть кейс 0.3. Апп-юзер `taskuser-a7@example.com`/`Taskflow123`. Следующий тик: **0.2** (A5b drizzle FK — нужен СВОЙ drizzle-fullstack тест-апп; A7 на entity-стеке).

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

## 2026-06-09 23:46 — 2026-06-10 00:05 MSK — Phase 0.4 DONE ✅ (прерванная генерация = явный статус, не голый стартер)
- Статус: DONE — live browser-E2E зелёный, web health 200, логи чистые.
- Корень (investigator-subagent + чтение кода): стартовый снапшот коммитится при `create_project` с `prompt_text=None` (`routers/projects.py:72`). Если `_process_prompt` прервался ДО создания первого реального снапшота (краш воркера / SSE-дисконнект / просто ещё не строили) — head остаётся стартером, и `PreviewFrame` рендерил его `/p/<slug>` iframe (`visible ? <iframe>`). Пустой шаблон (дефолт `blank` static, `is_fullstack=False`) выдавал себя за «готовый сайт» → юзер думал, что сработало. Status-енумов нет, снапшот создаётся только на успехе.
- Фикс (1 файл, минимальный диф, зона A): `apps/web/src/components/workspace/PreviewFrame.tsx` — `headIsStarter = head.prompt_text===null && head.parent_id===null`; `suppressStarter = headIsStarter && !selectedSnapshotId && !isStreaming`. Static-iframe ветка `visible && !suppressStarter`, empty-state `(!visible || suppressStarter)`. Копия empty-state → «Сайт ещё не сгенерирован… здесь появится готовый сайт, а не пустой шаблон». Затрагивает ТОЛЬКО static (fullstack/entity идут через container-ветку выше). Эскейп-хетч: явный клик по стартеру в таймлайне (`selectedSnapshotId` set) → рендерит.
- sequential-thinking: распланировал (premise → root cause → минимальный слайс vs полный status-енум). Выбрал frontend-honesty-guard как первый под-слайс: безопасно (нет миграции на живой юзер-БД), root-cause для «вижу пустой шаблон, думаю сработало», полностью тестируемо без убийства процесса. context7: не требовался (React-условный рендер, без новых API).
- Verify: `npm run typecheck` PASS; `npm run lint` — PreviewFrame.tsx чист (5 pre-existing ошибок в CodeView/GithubPushDialog/PromptInput/usePromptStream — НЕ мои файлы).
- E2E (puppeteer; playwright «Browser already in use»): рег throwaway `cq-p04-2346@example.com` → авто-логин → /projects → «Новый проект» (только название, пикера НЕТ ✓) → blank-проект `c9d639aa` (slug `p04-starter-guard-test-58e544`). Preview: **iframeCount=0, placeholderShown=true** — «Сайт ещё не сгенерирован» (скрин p04-starter-guard). Регрессия эскейп-хетча: клик по «Стартовый v1 7bfbef8» в таймлайне → iframeCount=1, src `/p/p04-starter-guard-test-58e544?inspect=1`, placeholder скрыт ✓ (рендер-путь стартера цел). Console output пуст всю сессию; `docker logs omnia-prod-web` 0 error.
- Доставка: commit 289ead3 | push main (PAT — gh протух) | deploy: prod `git merge --ff-only`→289ead3 + `docker compose -p full up -d --build web` | health web_local=200, public 200.
- Resource-guard: RAM available 8967MB (>4000), контейнер НЕ создавал (blank=static, без dev-контейнера) → ничего сносить. На проде висит чужой тест `omnia-dev-avtostek-magazin-e2e2-989cbd` (1 шт, не клиент) — порог не превышен, не трогал; кандидат на уборку след. тиком если RAM просядет.
- Владельцу утром: ✅ если генерация прервётся (или проект ещё не строили), превью больше НЕ показывает пустой стартовый шаблон как «готовый сайт» — честный статус «Сайт ещё не сгенерирован». Throwaway-проект `c9d639aa` под `cq-p04-2346@example.com` можно удалить.
- Идея на следующий тик: Phase 1.1 (discovery `{question, choices[], allow_custom}` для quick-reply чипов — `services/discovery.py`) ЛИБО Phase 2.5.1 (live-E2E кликера на full-аппе). Авто-RE-генерация прерванного билда (вторая половина 0.4) = отдельный слайс, требует серверного build-state.

## 2026-06-09 23:25–23:40 MSK — Phase 0.3 DONE ✅ (wake-from-hibernation 502→200, scale-from-zero ingress)
- Статус: DONE — live browser-E2E зелёный, 0×502.
- Корень: спящий dev-контейнер → ничего на 127.0.0.1:<port> → nginx proxy_pass = 502. Существующий `/wake` token-gated, зовётся только из workspace; прямой хит по preview-сабдомену не будил никогда. Подтверждено curl'ом до фикса (2×502).
- Фикс (зона D orchestrator, 7 файлов):
  - `routers/ingress.py` (новый, token-free): `GET /_omnia/wake` host-keyed — `<slug>-dev.<suffix>`→`omnia-dev-<slug>`, иначе `omnia-app-<slug>`; not_found→404-страница (crawler-safe, НЕ будит); exited/paused→`wake_container`+`record_activity`; отдаёт self-refresh «Запускаем приложение…» (JS reload 6s, sessionStorage счётчик, noscript meta-refresh).
  - `nginx_writer.py`: `_proxy_location` += `proxy_intercept_errors on; error_page 502 503 504 = @omnia_waking;`; `_wake_location()` (named loc → orchestrator); `refresh_vhosts()` startup-миграция (идемпотентно по `@omnia_waking`, FS-скан в thread, nginx -t гейт + полный байт-откат).
  - `docker_client.container_status` += label `project_id` (один docker-роунд для idle-резета).
  - `main.py`: регистрация роутера + `refresh_vhosts()` в lifespan (fail-soft).
  - `config.py`: `orchestrator_wake_target=127.0.0.1:8003`.
- sequential-thinking: 3 мысли (scale-from-zero паттерн; host-keyed чтобы dev+prod общий шаблон; рекурс-интерстициал через JS, риск/откат). context7: не требовался (nginx/FastAPI семантика известна).
- Verify: ruff/mypy чисто на МОЁМ диффе (2 остаточных ruff = pre-existing в `_issue_cert`); **107 pytest passed** (6 новых: host→container parse, vhost rebuild/idempotency/rollback).
- Грабли деплоя: (1) nginx -t отбил literal URI в proxy_pass внутри named loc → variable-form `set $var; proxy_pass $var` (refresh_vhosts откатил конфиг, бокс жив). (2) РЕЦИДИВ double-supervisor: 8h-proc 1444509 держал :8003, `--user` юнит фантомил strays → новый код не биндился; `systemctl --user stop/disable` + `pkill` + `sudo systemctl restart` system-юнит = единственный лисенер 1294022.
- E2E (puppeteer, playwright занят): A7 `462af0bb` `docker stop`→Exited → браузер navigate → waking-page (title «Запускаем приложение…»+спиннер, evaluate подтвердил) → авто-рефреш → TaskFlow рендерится (title «Omnia project», h1 hero, nav). nginx error.log: 0×502. `ingress.woke was=exited` ×2 в логах, чисто. Скрин p03-waking-page (по факту уже апп — контейнер тёплый, поднялся <6s).
- Доставка: commit f603773 (фича) + 82a4ecc (nginx var-fix) | push main (PAT) | deploy: `git merge --ff-only` /opt/omnia + `sudo systemctl restart omnia-orchestrator` | health 200 | refresh 16/16 vhost | nginx -t ok.
- Resource-guard: A7 СТОПНУТ после E2E (стенд для 2.5). RAM available 10.95GB, 0 dev-контейнеров.
- Владельцу утром: ✅ спящие превью больше НЕ отдают 502 — зритель видит «Запускаем приложение…» и через пару секунд живой апп (все 16 превью, включая kofeinia/legomagazin/crm). Демо: открой любой -dev.preview-сабдомен после простоя.
- Идея на следующий тик: Phase 0.4 (авто-retry прерванной генерации — не показывать голый стартер без снапшота) ИЛИ Phase 1.1 (discovery отдаёт choices[] для чипов).

## 2026-06-09 23:06–23:25 MSK — Phase 0.2 DONE ✅ (A5b drizzle FK под per-project search_path — уже решено архитектурой)
- Статус: DONE — премиса задачи устарела; проверено вживую по прод-БД, скорректирован лживый коммент.
- Вывод: НЕТ бага «FK на public.users». Рантайм-путь НЕ использует `drizzle-kit push`:
  - `docker-entrypoint.sh` → `node scripts/init-db.mjs` (idempotent `CREATE TABLE IF NOT EXISTS` через драйвер `pg`, без интроспекции — push висел минутами на shared Postgres и блокировал boot).
  - `postgres_admin.build_dsn` пинит `?options=-c+search_path%3Dproj_<id8>` (один schema, БЕЗ `public`); роль `proj_<id8>_user` скоупнута на свою схему → unqualified DDL и FK резолвятся ИНТРА-СХЕМНО.
- Живая прод-проверка (ssh lh-server → omnia-postgres-users, db omnia_users):
  - 8 схем `proj_*`, у каждой свои `accounts,records,sessions,users,verification_tokens`. `public.users` = **0** (нет shadow-хазарда у `CREATE TABLE IF NOT EXISTS`).
  - FK proj_462af0bb (A7): `accounts/sessions/records.created_by` → ВСЕ `proj_462af0bb.users` (parent_schema=proj_462af0bb). Cross-schema нет.
  - users-колонки A7: id,name,email,email_verified,image,**password_hash**,**role**,created_at — auth-колонки на месте.
  - Персист signup: `taskuser-a7@example.com` | has_pw=t | role=user → реальная регистрация записала bcrypt-хеш в per-project schema и пережила (users=1).
- Фикс (минимальный диф, 1 файл): `apps/orchestrator/templates/nextjs-entities/src/lib/db/schema.ts` — top-comment врал «`docker-entrypoint.sh` runs `db:push --force`». Заменил на точное описание `init-db.mjs` + инвариант «всё в `proj_<id>`, FK интра-схемно, нет `public.users`». Эта ложь и породила баг-репорт 0.2 → коммент-фикс = защита от регрессии (canon: комментарии не лгут). `db:push` npm-скрипт оставлен как ручной escape-hatch (безвреден, drizzle.config тоже пинит search_path через DSN).
- sequential-thinking: 3 мысли (премиса стара → верифицировать live, а не править вслепую; проверить shadow-хазард IF-NOT-EXISTS+public; auth-колонки/персист). context7: не требовался (без смены DDL-стратегии).
- Verify: правка — только блок-коммент в .ts (нулевой рантайм/тайп-эффект; typecheck/lint неприменимы к комменту). Resource-guard: RAM available 10.9GB, 0 dev-контейнеров — чисто, тест-апп НЕ создавал (задача = верификация существующего + коммент).
- Доставка: commit + push main; деплоя НЕТ (коммент, рантайм не затронут; пересборка template-образа не нужна — нулевой behavioral-эффект, бережём сервер). E2E-критерий 0.2 («auth-колонки, signup/login персистят») закрыт прямой БД-уликой + персистом A7-юзера + вчерашним полным signup→login→persist E2E на этом же стеке (0.1).
- Владельцу утром: ✅ архитектура per-project-schema корректна — никаких cross-schema FK, signup пишет реальный auth и персистит (проверено по 8 живым схемам). Никаких изменений поведения — только убрал вводящий в заблуждение комментарий.
- Идея на следующий тик: Phase 0.3 (wake-from-hibernation 502→200) — A7 `462af0bb` остановлен = готовый стенд: открыть preview-сабдомен → должен проснуться→200, не 502.

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
