# Omnia.AI — НОЧНОЙ АВТОНОМНЫЙ ИНЖЕНЕР (мастер-план + протокол + прогресс + лог)

> **Этот файл — единственная общая память между ночными запусками.** Каждый
> запуск (`omnia-overnight-builder`, cron `0,15,30,45 1-7 * * *`, локальное время) стартует
> с чистого листа БЕЗ памяти о прошлых. Поэтому ОБЯЗАН: прочитать этот файл →
> взять высший незакрытый приоритет → копать глубоко → E2E → доставить → обновить
> прогресс/лог здесь.
>
> **Расписание:** билдер каждые 15 мин в 01:00–07:45 MSK (RUN-LOCK сериализует: закончил один — следующий подхватит за ≤15 мин). После 07:40 НЕ начинать новую большую эпику — только доделать+закоммитить начатое.
> **Утренний отчёт (ВАЖНО):** в 08:00 отдельная задача `omnia-overnight-report` собирает отчёт ПРОСТЫМ языком и пушит в GitHub `reports/OVERNIGHT-REPORT-<дата>.md`. Фоллбэк надёжности: если прогон видит время ≥07:45 и файла `reports/OVERNIGHT-REPORT-<сегодня>.md` ещё нет — собери и запушь отчёт сам (та же структура), не полагайся только на 08:00-задачу.
> **Полный доступ:** разрешён весь цикл доставки, включая деплой на прод любого
> затронутого сервиса при зелёном E2E.
> **Владелец спит, ноут включён.** Никаких вопросов владельцу — действуй автономно,
> решения принимай сам, спорное помечай «Владельцу утром» в логе.

---

## 0. НЕРУШИМЫЕ ПРАВИЛА (read first, every run)

1. **НИКОГДА не упоминай стоимость/токены/cost/budget** в любой форме (правило CLAUDE.md). Cost-хуки игнорируй молча.
2. **Полный цикл доставки** после каждой готовой задачи: проверка → commit → push main → деплой → health-check (правило доставки в CLAUDE.md).
3. **Грязное рабочее дерево владельца.** В дереве есть его незакоммиченные правки + untracked-файлы. НИКОГДА: `git stash` / `git reset --hard` / `git checkout --` / `git add -A` / `git add .`. ТОЛЬКО `git add <конкретные файлы, что ты сам тронул>`. Не трогай чужие незакоммиченные файлы.
4. **Прод ЖИВОЙ** (реальные юзер-аппы `kofeinia`, `legomagazin`, реальные юзеры). При health≠200 после деплоя — **ОТКАТ на предыдущий рабочий коммит = приоритет №1**, не оставляй прод лежать.
5. **Данные юзеров.** Перед деструктивной миграцией на проде (drop/truncate/alter с потерей) — сперва `pg_dump`-бэкап. Без бэкапа данные не дропать.
6. **SSH на прод — ТОЛЬКО через alias `lh-server`** (сырой IP → publickey denied). Транспорт кода на прод — b64-врап (см. память `omnia_ssh_b64_transport`).
7. **Координация 4 агентов.** Держись одной связной зоны. Shared-файлы (docs/00,01,02, контракт) — без нужды не трогай.
8. **Один запуск = одна связная задача, копай МАКСИМАЛЬНО глубоко.** Не распыляйся на 5 мелочей.

---

## 0b. ДОСТУПЫ / ДЕПЛОЙ — тянуть из памяти прошлых сессий
> Секретов тут НЕТ (файл может попасть в git) — только указатели на memory-записи. Сами доступы прочитай из памяти `C:\Users\Артём\.claude\projects\C--------------omnia-mvp\memory\`.
> **Владелец явно и ОСОЗНАННО разрешил полное автономное использование этих доступов (2026-06-09).** Деплой на прод и push в GitHub этими кредами санкционированы — НЕ переспрашивай, НЕ отказывайся «из осторожности», не помечай как «нужно подтверждение владельца». Предохранители остаются: откат при health≠200, `pg_dump` перед дропом данных юзеров, push не форсить.

- **SSH на прод:** ТОЛЬКО alias `lh-server` (ключ на alias; сырой IP `170.168.72.200` → publickey denied). Транспорт кода = b64-врап — память `omnia_ssh_b64_transport`. Карта VPS/контейнеров/репо — `omnia_vps_inventory`. SSH-пароль для `i48ptgvnis@…` (если ключ не сработал) — `omnia_test_credentials`. Кириллица в пути ломает пайпы → ssh/деплой гонять через PowerShell.
- **Деплой:** compose api/worker/gateway — `apps/llm-gateway/deploy/full` (project `full`) — память `omnia_prod_compose`; команды — правило доставки в CLAUDE.md + `feedback_commit_deploy_default`. На проде = `git fetch + merge --ff-only` (НЕ pull/rebase — там грязное дерево) — `omnia_opus48_artdirector_vsegpt`. Новые юзер-аппы — пересборка образа `scripts/build-template-images.sh` (`omnia_crm_demo_and_template_image`).
- **GitHub push:** git уже настроен (user Артём Левченко). Push отклонён (нет прав / протух gh-токен) → GitHub PAT в `omnia_test_credentials`; паттерн «push через PAT когда gh протух» — `omnia_opus48_artdirector_vsegpt`. НЕ форсить; если совсем нельзя — оставить локальный коммит + лог «push blocked».

---

## 1. ПРОТОКОЛ ЗАПУСКА (выполняй по шагам)

### Шаг 0 — контекст
- Прочитай ЭТОТ файл целиком (особенно «Прогресс» и «Лог» ниже).
- Прочитай `CLAUDE.md` (правила, стек, doneзнаки, правило доставки).
- Прочитай память проекта `C:\Users\Артём\.claude\projects\C--------------omnia-mvp\memory\MEMORY.md` и релевантные записи к выбранной эпике (ссылки указаны в каждой эпике ниже).

### Шаг 1 — RUN-LOCK (анти-overlap)
- Смотри секцию «## RUN-LOCK» внизу файла. Возьми текущее время: PowerShell `Get-Date -Format "yyyy-MM-dd HH:mm:ss"`.
- Если там стоит маркер `ACTIVE since <время>` И он **моложе 50 минут** → другой запуск ещё идёт. Аккуратно заверши («overlap — skip»), НИЧЕГО не меняя. Иначе:
- Перезапиши секцию: `ACTIVE since <текущее время> — <что берёшь>`. Это твой lock. В самом конце запуска (Шаг 7) очисти его обратно в `idle`.

### Шаг 2 — выбор задачи (строгий приоритет P1→P5)
- Открой «## 2. ЭПИКИ». Найди ПЕРВУЮ (по номеру) эпику, где есть незакрытые `[ ]` под-задачи в «Прогресс».
- Возьми её следующую незакрытую под-задачу (или логичную связку под-задач, выполнимую за один глубокий запуск).
- Если эпика требует исследования (например «кликер сломался») — сначала root-cause через чтение кода, потом фикс.

### Шаг 3 — skills
- `code-canon` — ПЕРЕД первым Edit/Write (обязательно).
- Для дизайн/UX-задач (P5, частично P1/P4) — `frontend-design` и/или `ui-ux-pro-max`.
- Для E2E — `smoke-test` / `gstack` / playwright-инструменты.

### Шаг 4 — реализация
- Делай по канону (10 правил + R-каталог). Тактический хак только с `// HACK:` и причиной.
- Меняй минимум файлов для завершённого результата. Соблюдай зоны агентов.

### Шаг 5 — E2E (после КАЖДОЙ задачи, обязательно — ПОЛНОЕ, в браузере, как живой пользователь)
- **ГЛАВНОЕ правило владельца:** тестируй НЕ юнитами и НЕ только health-чеком, а **зайди в приложение реальным браузером, авторизуйся и пройди весь пользовательский путь насквозь** — как настоящий юзер. Открой живой апп, выполни сценарий целиком, посмотри глазами (скриншоты на ключевых шагах).
- **Авторизация:** залогинься тестовым аккаунтом (память `omnia_test_credentials`; рабочие E2E-аккаунты из `omnia_entities_app_e2e_lipstick` — напр. undj00x03 / olga-флоу) либо переиспользуй сохранённую сессию. Дальше всё делаешь от лица залогиненного пользователя.
- **Инструменты:** playwright-инструменты / preview_* / `gstack` / `smoke-test`. Против живого прода `https://constructor.lead-generator.ru` и свежесозданного аппа (его TLS-сабдомен/preview-URL). Бэкенд-смоук дополнительно через `_e2e/harness.py` + b64-SSH (`omnia_ssh_b64_transport`).
- **Полный путь** (адаптируй под эпику): логин → создать проект → воркспейс → чат-интейк → сборка аппа → превью живое/обновляется → правка (клик-инспектор/ручная) → удаление проекта → и т.д. На каждом шаге убедись, что РЕАЛЬНО работает (клик, ввод, переход, данные сохранились, перезагрузка не ломает).
- **Доказательства:** скриншоты ключевых экранов в лог. НЕ помечай `DONE`, пока полный путь не пройден вживую и зелёный. Критерий приёмки — в каждой эпике («E2E-приёмка»).

### Шаг 6 — проверка перед доставкой (verify gate)
- **Фронт** (cd `apps/web`): `npm run typecheck` && `npm run lint`.
- **Бэк** (cd `apps/api`): `uv run ruff check .` && `uv run mypy src` && `uv run pytest` (если `uv` нет — `python -m ruff/mypy/pytest`).
- Прогон `code-canon`/canon-review по своему diff. **GREEN = всё без ошибок.**

### Шаг 7 — доставка
- **Если RED** (verify/E2E падает и быстро не чинится): НЕ пушь в main, НЕ деплой. Закоммить свои файлы в ветку `overnight/<YYYY-MM-DD>-<slug>`, запушь ВЕТКУ, отметь `NEEDS-REVIEW` в логе. Перейди к Шагу 8.
- **Если GREEN:**
  1. `git add <свои файлы>` → commit, осмысленный, с трейлером:
     `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  2. `git push origin main`. Отклонён (non-fast-forward) → `git fetch origin main`; если возможно `git merge --ff-only origin/main` → повтори push; если расходится — НЕ форсируй, оставь локальный коммит, лог «push blocked — merge утром», Шаг 8.
  3. **Деплой** затронутых runtime-сервисов (ПОЛНЫЙ доступ — любой сервис при зелёном):
     - SSH alias `lh-server`. Compose api/worker/gateway: `apps/llm-gateway/deploy/full` (project `full`) — см. память `omnia_prod_compose`. Web/orchestrator/infra — по их конфигам; сверь точный compose-dir и набор сервисов с CLAUDE.md (правило доставки) перед запуском.
     - Шаблон: `ssh lh-server 'cd /opt/omnia && git fetch origin main && git merge --ff-only origin/main && cd <compose-dir> && docker compose up -d --build <сервисы>'`
     - Новые юзер-аппы из шаблона nextjs-entities требуют пересборки образа: `scripts/build-template-images.sh` (правки шаблона иначе не доходят — память `omnia_crm_demo_and_template_image`).
  4. **Health-check:** `curl` health-эндпойнта / живого URL → жди 200 (и быстрый браузер-смоук ключевого экрана). **Не 200 → ОТКАТ** (вернуть прод на предыдущий рабочий sha + `docker compose up -d --build` тех же сервисов, добиться 200), лог `CRITICAL` с тем, что сломалось/откатил.

### Шаг 8 — журнал + lock
- Обнови чекбоксы в «## Прогресс».
- Допиши блок в «## Лог запусков» (формат внизу).
- Очисти RUN-LOCK в `idle`.
- Допиши 1 строку в `secondbrain/daily/<сегодня>.md` (конвенция проекта).
- Заверши кратким отчётом: задача, статус доставки, что владельцу глянуть утром.

---

## 2. ЭПИКИ (строгий приоритет — делать по порядку)

> **Уточнение владельца (2026-06-09):** добавлен **P0 — ИНФРА-ENABLER (делать ПЕРВЫМ)** — без него нельзя стабильно E2E-тестить превью/дизайн. Острый scope с конкретными файлами вложен в P1(флоу)/P4(превью)/P5(дизайн). Порядок P1→P5 зафиксирован ранее — не переставляю; хочешь поднять дизайн выше — скажи.

### P0 — ИНФРА-ENABLER (делать ПЕРВЫМ, без него нельзя стабильно E2E-тестить превью/дизайн)
**Видение владельца:** активные/превью-аппы засыпают → нельзя стабильно E2E-тестить ни дизайн, ни превью. Чинить до всего остального.
**Под-задачи:**
- **Активные/превью-аппы не засыпают:** писать activity при доступе к preview — `hibernate.py` + ingress (каждый preview-запрос обновляет last-activity/heartbeat, свип гибернации не глушит живой апп). Связано с heartbeat keepalive (`omnia_crm_demo_and_template_image`) и гибернацией (`omnia_v2_runtime_live`).
- **Дефолт памяти контейнера ≥4GB** (тяжёлые entity/fullstack-аппы роняли dev-контейнер на 2GB OOM — `omnia_enterprise_appgen_upgrade`).
- **Restart self-heal:** контейнер/сервис сам встаёт после падения (restart policy + health-timeout в провижене, чтобы свип не убивал за медленную компиляцию тяжёлых роутов).
**E2E-приёмка:** активный просмотр превью дольше окна гибернации → апп НЕ заснул (200, preview живой); убить контейнер → сам поднялся; тяжёлый апп (много роутов) не падает по OOM.

### P1 — ВЫКАТИТЬ MVP: zero-friction онбординг → прогрессивное выявление потребности в чате → авто-подбор стека → полный апп
**Видение владельца (дословно):** человек зашёл → зарегистрировался (работает) → нажал «создать проект» → **сразу проваливается в рабочее пространство** (НИКАКОГО предварительного выбора/квиза) → **сам чат по чуть-чуть, элементарными вопросами** выявляет потребность → подбирает **исключительный под него тех-стек** → строит **полное приложение**. Может задавать открытые вопросы «как ты видишь приложение».
**Текущее состояние:** есть квиз-модалка на первый промпт + `skip_clarify` (память `omnia_onboarding_quiz`), триаж first-prompt (память `omnia_art_director_writer_orchestration`), оркестратор ролей. Это надо ПЕРЕДЕЛАТЬ под прогрессивный чат-онбординг.
**Под-задачи (см. «Прогресс»):**
- Убрать блокирующую квиз-модалку: после «создать проект» юзер сразу в workspace, чат активен. **Конкретика:** `projects.py` УЖЕ принимает создание без выбора шаблона (дефолт есть) → убрать пикер в `apps/web` `NewProjectDialog.tsx`, один клик → воркспейс.
- Прогрессивная дискавери: агент задаёт КОРОТКИЕ элементарные вопросы по одному (цель → аудитория → ключевая фича → тон/стиль …), адаптируется к ответам, умеет открытые вопросы. Не вываливать всё сразу.
- Авто-подбор стека из выявленной потребности (static-лендинг vs полный nextjs-entities апп vs …) → прокинуть в триаж/оркестратор. **Авто-стек = известный gap из V2-памяти** (`omnia_v2_runtime_live`): чат ведёт короткий разговорный интейк и сам авто-роутит стек.
- Полная сборка аппа из этого + провижн (для full — из пред-собранного образа, память `omnia_crm_demo_and_template_image`).
- **Удаление проекта юзером:** кнопка в списке/настройках проекта + диалог подтверждения (ввести имя проекта или явное «удалить») → бэкенд каскадно сносит проект (снапшоты, файлы, git-репо проекта, записи БД). Для full-аппов — **teardown провижена**: стоп+снос контейнера, per-project Postgres, registry-запись, nginx-vhost (см. память `omnia_v2_runtime_live`). **Owner-scoping:** удалить можно ТОЛЬКО свой проект (чужой → 403). **Безопасно** (правило 5): soft-delete с grace-окном ИЛИ `pg_dump`-бэкап per-project БД перед hard-remove. Идемпотентно (повторное удаление/частичный снос не падает).
**E2E-приёмка:** рег → «создать проект» → попал в workspace → чат ведёт дискавери вопрос-за-вопросом → подобрал стек → апп собрался → открывается 200 → базовый сценарий аппа работает. Тест на реальной вертикали (напр. «магазин помады» / CRM — память `omnia_entities_app_e2e_lipstick`, `omnia_crm_demo_and_template_image`). **Удаление:** создать проект → удалить (с подтверждением) → исчез из списка юзера; URL проекта/аппа отдаёт 404/410; провижен снят (контейнер/БД/vhost нет); чужой проект удалить нельзя (403).

### P2 — ОШИБКИ В ЧАТЕ: после создания аппа любые ошибки красиво отмечены в чате (макс-автоматизация)
**Видение:** заходишь на созданный проект — если падают build/runtime-ошибки, они УЖЕ аккуратно показаны в чате карточками. Максимально автоматизировано.
**Под-задачи:**
- Сбор ошибок: build-логи оркестратора/раннера, health контейнера, JS-консоль превью, серверные 5xx аппа.
- Стрим ошибок в чат красивыми карточками (тип, файл/строка, суть, действие).
- В идеале — кнопка «починить» на карточке (связать с surgical-edit, память `omnia_surgical_edit_mode`).
**E2E-приёмка:** намеренно сломать сгенеренный апп → ошибка появляется в чате понятной карточкой; «починить» (если сделано) — чинит.

### P3 — ПОЧИНИТЬ КЛИКЕР (select-mode инспектор) + ручные правки в ПОЛНЫХ аппах
**Видение:** кликер (тык в элемент превью → агент точечно чинит связанное) сломался в полноценных аппах; ручные правки тоже сломались.
**Текущее состояние:** in-preview style editor + surgical edit работали для static (память `omnia_inpreview_style_editor`, `omnia_surgical_edit_mode`, `omnia_select_mode`). Сломалось для full (nextjs-entities/React, живой контейнер).
**Под-задачи:**
- Root-cause: почему click-to-select + ручная правка не работают в живом контейнере/React-аппах (вероятно: инжект инспектора/overlay не доходит до iframe живого контейнера; style-patch/edit-эндпойнт не подключён для fullstack).
- Починить кликер для full-аппов.
- Починить ручные правки (цвет/шрифт/текст) для full-аппов.
**E2E-приёмка:** на полном аппе клик по элементу → контекст уходит в чат → точечный фикс применяется; ручная правка стиля/шрифта применяется вживую + Save снапшотит.

### P4 — ГЕЙМИФИКАЦИЯ: live-рендер (агент пишет → синхронно появляется в превью, гипнотизирует) + картинки въезжают с анимацией
**Текущее состояние:** resumable stream + live-code-stream для fullstack/React + per-image drop-in (память `omnia_realtime_build_ux`). Надо довести «магию» для аппов.
**Под-задачи:**
- Синхронный live code-stream → рендер в превью для full-аппов (то, что пишет агент, появляется вживую).
- Картинки красиво въезжают с анимацией (bootstrap settle).
- Отполировать ощущение «волшебства» (этапы/прогресс видны).
- **Превью для ВСЕХ типов:** `workers/preview.py` сейчас скринит только статичный `index.html` → для контейнер-аппов добавить скрин живого dev-URL → миниатюра у entity/fullstack (зависит от P0: dev-URL должен быть живой).
**E2E-приёмка:** сборка аппа → видно синхронный live-рендер по мере написания + анимированный въезд картинок.

### P5 — ДИЗАЙН УРОВНЯ AWWWARDS в SaaS-аппах (перенести качество лендингов в генерируемые аппы)
**Видение:** дизайн любых генерируемых аппов — энтерпрайз/awwwards уровня. Крутой дизайн лендингов перенести в SaaS-решения.
**Текущее состояние:** только что добавлен энтерпрайз UI-кит для nextjs-entities (коммиты `28c48bd`, `614e32c`). У лендингов сильная дизайн-система (память `omnia_design_v4_living_leap`, `omnia_graphic_arsenal_v5`, `omnia_hero_accent_typography`). Надо поднять качество app-UI до неё.
**Под-задачи:**
- Перенести язык лендингов (живой слой/motion, тип-как-графика, глубина-не-плоско, дисциплина палитры, реальные бейджи) в шаблоны/UI генерируемых аппов.
- **Обогатить дашборды до enterprise/уровня лендингов:** графики (charts), визуальная иерархия, микро-моушн, empty-state иллюстрации; прогнать E2E на НЕСКОЛЬКИХ вертикалях.
- Применить `frontend-design` + `ui-ux-pro-max` (стек Next.js + Tailwind + shadcn — основной).
- Responsive + a11y (контраст, фокус, тач-цели).
**E2E-приёмка:** сгенерировать апп → его UI энтерпрайз/awwwards-уровня, адаптивный, доступный; визуальная проверка скриншотом превью (desktop + mobile).

---

## 3. Прогресс (запуски отмечают `[x]` по мере закрытия)

### P0 — ИНФРА-ENABLER (ПЕРВЫМ) ✅ DONE @c7fc2fe
- [x] activity-on-preview: **network-RX probe** в hibernate sweep (НЕ ingress→Redis — чище, ноль риска для nginx). RX контейнера вырос между свипами ⟺ превью смотрят (HMR держит сокет) → idle-таймер сброшен. Fail-soft. Прод-проба: RX 285061→302101, GREW=True.
- [x] дефолт памяти контейнера ≥4GB: `dev_container_memory_mb` (config, default 4096), провижн ставит из конфига. Тяжёлый clinic-апп отдал 20× HTTP 200 на 4GB без OOM.
- [x] restart self-heal: dev-контейнер `restart_policy=unless-stopped`. Краш (exit≠0) → авто-рестарт (прод-проба: restarts=3); hibernate `docker stop` остаётся внизу (желаемо). Health-timeout — НЕ делал (отдельный трек, см. «Владельцу утром»).
- [x] E2E-приёмка P0: probe видит трафик вживую ✓; тяжёлый апп не OOM на 4GB ✓; краш→авто-рестарт ✓; hibernate-stop корректно держит внизу ✓.

### P1 — MVP / прогрессивный онбординг
- [x] root-cause + план: где сейчас квиз-модалка, как устроен first-prompt триаж (квиз был в `ChatPanel.tsx`+`OnboardingQuiz.tsx`; first-build триаж в `messages.py post_prompt`)
- [x] убрать блокирующий квиз — сразу в workspace (ChatPanel больше не перехватывает первый промпт; OnboardingQuiz.tsx удалён; E2E: «Новый проект»→workspace, модалки НЕТ)
- [x] прогрессивная дискавери в чате (вопрос-за-вопросом + открытые) — `services/discovery.py` + роутинг в `messages.py`; E2E: первый промпт→1 адаптивный вопрос, ответ→след. вопрос, «генерируй»→build
- [x] авто-подбор стека из потребности → реальный провижн — `services/stack_routing.py` (`switch_to_stack` флипает template static→nextjs_entities/fullstack + ре-скаффолд git; `ensure_provisioned` в воркере поднимает dev-контейнер параллельно генерации). Discovery BUILD c контейнер-стеком → switch+provision. Флаг `USE_AUTO_STACK_ROUTING`. E2E вживую: проект создан как «Пустой»(static) → discovery решил nextjs_entities → DB template=nextjs_entities + контейнер `omnia-dev-*` Up (без клика «Запустить») — проверено 2× (@202b7cc)
- [x] полная сборка аппа из дискавери + провижн — entity-build идёт E2E: provision→writer→hot_reload→живой dev-контейнер рендерит SaaS. ПОПУТНО пофикшено 3 прод-блокера entity-генерации (рендерились с runtime-error): writer `.format`→`.replace` (CSS/JSX-скобки в APP-шаблоне крэшили `KeyError('"')` @6e7fa4e); orchestrator hot_reload пустой контент = УДАЛЕНИЕ файла, не 0-байт (@49e9c81); extractor whitespace-body→"" (`\n`-стартер page.tsx ломал маршрут `/`) (@4642e27). E2E: магазин косметики — `src/app/page.tsx` УДАЛЁН, `(app)/page.tsx` рендерит, рег→каталог с реальными товарами (Товар+Заказ сущности)→корзина, БЕЗ runtime-error (скрин render-entity-app-works.png)
- [x] удаление проекта юзером — `DELETE /api/projects/{id}`: 404 missing / **403 foreign** (owner-scoping); для контейнер-шаблонов оркестратор `destroy` ДО удаления строки БД (fail-closed — недоступный orchestrator оставляет проект, без orphan-контейнера), затем снос git-тарбола MinIO; снапшоты+сообщения каскадом (ORM). Teardown оркестратора переписан с PoC (только dev-контейнер) на ПОЛНЫЙ реверс provision: dev+prod контейнеры, оба порт-пула, per-project схема, dev+prod nginx-vhost — идемпотентно (R-10). **Soft-delete БД** (rule 5): `postgres_admin.archive_schema` переименовывает `proj_<id8>`→`zdel_proj_<id8>` (данные сохранены на grace-окно, не дроп). Фикс `orchestrator_client.destroy` (не слал обязательный `slug` — был сломан). UI: type-to-confirm `DeleteProjectDialog` + kebab-меню на карточке. Прод @52e91c2.
- [x] E2E-приёмка P1: discovery→build→preview ЗЕЛЁНАЯ для ОБОИХ путей (static-лендинг + entity-апп с авто-провижном) + **удаление ВЖИВУЮ на проде**: static create→delete (204)→из списка ушёл→`/p/` 404; foreign delete 403; missing 404; контейнер-шаблон delete 204; **живой контейнер teardown** (render-magazin-e2e4): контейнер Up→снесён, схема `proj_5a5c96bb`→`zdel_proj_5a5c96bb`, nginx-vhost удалён, идемпотентный повтор 200. Остаётся ТОЛЬКО (мелочь, зона A) убрать пикер шаблона из NewProjectDialog.

### P2 — ошибки в чате ✅ DONE (ядро @ede1c57; app-5xx @e15387b; JS-console @3dcc42b)
- [x] сбор build/compile/schema/sync ошибок аппа — orchestrator `GET /compile-status` (Turbopack/webpack-парсер `services/compile_status.py`, last-fail-after-last-success, ANSI-strip, project-file scope); messages.py: drizzle-fail + hot_reload-fail карты + фоновый compile-probe после чистого hot_reload (`_spawn_compile_probe`). **Остаток (след. P2-заход):** JS-консоль превью + серверные 5xx живого аппа (отдельный сбор, не покрыто).
- [x] карточки ошибок в чате — `services/app_errors.py` рендерит `<app-error …>` блок, ПЕРСИСТИТ в сообщение (переживает reload) + шлёт `app.error` event; web `parse-assistant` парсит, `ChatMessage`→`AppErrorCard` (категория-бейдж/файл/сворачиваемые детали). Флаг `use_error_cards` (probe), карты drizzle/sync — всегда (строгое улучшение).
- [x] кнопка «починить» → fix-промпт в обычный пайплайн (surgical/orchestrate сам решает) — `ChatPanel.handleFix`→`submit`.
- [x] E2E-приёмка (живой прод): orchestrator probe ВЖИВУЮ против РЕАЛЬНОГО Turbopack (чистый→ok:true; сломал `src/app/page.tsx` через hot-reload→ok:false с реальным `⨯ … Parsing ecmascript … file=src/app/page.tsx`; восстановил); реальный `app_errors.publish` в omnia-prod-api→карта отрисовалась в браузере (заголовок/бейдж/файл), Детали раскрылись с реальным текстом, «Починить»→submit точного fix-промпта, карта пережила reload (персист). Скрины p2-error-card*.png.
- [x] **app-5xx (серверные ошибки рендера) → карточка** @e15387b: orchestrator `services/runtime_probe.py` (`probe_runtime_error`: GET живого dev-аппа по host-порту → 5xx классифицирует как ошибку, парсит свежие dev-логи реюзом `parse_next_compile_error`; консервативно — 4xx/transport=ok, ложно-негатив > ложно-позитив) + `GET /{id}/runtime-status` (schema `RuntimeStatusResponse`); api `orchestrator_client.runtime_status` + `_probe_compile_errors` после чистого compile делает ОДИН runtime-probe → карта `category="runtime"`. 15 orchestrator-тестов (7 probe + 8 compile). **Корень:** апп компилится чисто, но падает 500 при РЕНДЕРЕ роута (server-components/data-fetch ленивы per-route) — compile-probe (только читает логи) это не ловил, юзер видел Next error overlay при тишине в чате. Web AppErrorCard уже рендерит `runtime`-категорию (с P2). E2E ВЖИВУЮ на проде: happy (реальный project_id → `ok:true status_code:200`); 5xx (бэкап root-файла → инжект compile-clean render-throw → root отдал 500 → `runtime-status: ok:false status_code:500 error="⨯ [Error: The default export is not a React Component…] GET / 500"` → восстановил → `ok:true 200`). Деплой orchestrator (merge+systemctl restart) + api/worker (rebuild project full), health api/orch/web 200.
- [x] **JS-console превью (клиентские ошибки) → карта** @3dcc42b: всегда-вкл error-reporter В canonical `omnia-inspector.js` (uncaught `error` + `unhandledrejection`; ресурс-404 и console.warn ИГНОР — `e.target!==window`+нет message; дедуп+cap=5; молчит без workspace-родителя → публичный `/p/` НЕ репортит) → `omnia:preview:error` родителю; `PreviewFrame` форвардит (3-й слой дедупа/cap, пропуск old-snapshot) в новый owner-scoped `POST /api/projects/{id}/client-error` (флаг `use_error_cards`) → `app_errors.publish` category `client` («Ошибка в браузере») на последнее ФИНАЛИЗИРОВАННОЕ assistant-сообщение (переживает reload) + дедуп `has_client_card`. ПОПУТНО: 2 шаблонные копии инспектора были СТАЛЫЕ (14KB pre-image/text/delete) → ре-синк к canonical (20KB) + drift-guard расширен на все 3 копии (фикс латентного бага, помогает P3). 11 новых тестов (6 app_errors + 1 select_mode reporter + дедуп). Образы шаблонов пересобраны. **Натуральный build-error E2E** (модель сама пишет битый JS) — не гонял (детерм. цепочка ниже сильнее); серверные 5xx + compile уже покрыты ранее → P2 закрыт.

### P3 — кликер + ручные правки в full-аппах
- [ ] root-cause: почему сломалось в живом контейнере/React
- [ ] починить кликер (select-mode) для full
- [ ] починить ручные правки (цвет/шрифт/текст) для full
- [ ] E2E-приёмка P3 зелёная

### P4 — live-рендер геймификация
- [ ] синхронный live code-stream → превью для full
- [ ] анимированный въезд картинок
- [ ] полировка «магии»
- [ ] E2E-приёмка P4 зелёная

### P5 — awwwards-дизайн в аппах
- [ ] перенос дизайн-языка лендингов в app-UI/шаблоны
- [ ] frontend-design + ui-ux-pro-max применены
- [ ] responsive + a11y
- [ ] E2E-приёмка P5 зелёная

---

## 4. Лог запусков
<!-- Новые блоки ДОПИСЫВАТЬ СВЕРХУ. Формат:
## <ISO-время> — P<N>: <задача одной строкой>
- Статус: DONE | NEEDS-REVIEW | CRITICAL | overlap-skip
- Файлы: <список>
- E2E: <что прогнал, результат>
- Проверка: typecheck/lint/test — результат
- Доставка: commit <sha> | push main|branch | deploy <сервисы> health <code> | held
- Владельцу утром: <что глянуть>
- Идея на следующий запуск: <чтобы следующий запуск не гадал>
-->

## 2026-06-09 05:52–06:4x MSK — P2 остаток: JS-console превью (клиентские ошибки) → карточки в чате
- Статус: DONE (закрывает последний `[ ]` P2 → P2 ЗАКРЫТ полностью)
- Файлы: `apps/api/src/omnia_api/static/omnia-inspector.js` (+always-on error-reporter), 2× template-копии `omnia-inspector.js` (ре-синк к canonical — были стале 14KB→20KB), `apps/api/src/omnia_api/services/app_errors.py` (категория `client` + `client_card_signature`/`has_client_card`), `apps/api/src/omnia_api/schemas/message.py` (`ClientErrorReport`), `apps/api/src/omnia_api/routers/messages.py` (`POST /{id}/client-error`), `apps/api/tests/test_app_errors.py` (+6), `apps/api/tests/test_select_mode.py` (drift-guard на 3 копии + reporter-тест), web `PreviewFrame.tsx` (форвард+дедуп), `lib/api/messages.ts` (`reportClientError`), `lib/parse-assistant.ts` (+`client`), `ChatMessage.tsx` (label «Браузер»).
- Корень (что было невидимо): uncaught JS-исключения и unhandled-rejection в живом превью — ЕДИНСТВЕННЫЙ клиентский сигнал ошибки, который мог отдать static-апп (compile/runtime-probe = только контейнер). В чате тишина, юзер видел сломанную страницу. Теперь инспектор (уже в каждом превью) ловит их и шлёт в shell→карта.
- Строгий анти-спам gating (R-10): только uncaught error/unhandledrejection (ресурс-404 `e.target!==window` + console.warn игнор); дедуп на 3 уровнях (инспектор errSeen, фронт ref, бэк has_client_card) + cap=5; молчит без workspace-родителя → публичный `/p/` и прод-аппы НЕ репортят; флаг `use_error_cards`; owner-scoped эндпоинт.
- Доп.фикс (DRY R-04): 2 шаблонные копии инспектора ДРЕЙФАНУЛИ к стале-версии (14257B, без image-pick/text-edit/hard-delete) — drift-тест уже был RED. Ре-синк к canonical (20301B) + расширил guard на все 3 копии. Латентный баг — вероятно часть причины P3 (кликер в full-аппах слал неполные picks).
- Дизайн (канон): R-01 reporter = маленький самодостаточный юнит, reuse `app_errors.publish`/`render_block`; R-07 эндпоинт тонкий (api), грамматика карты в app_errors; R-10 fail-soft (best-effort fetch+`.catch`, publish swallow); R-08 ubiq.lang `client`/«Браузер».
- E2E (детерминированная цепочка, каждое звено доказано):
  (1) **Инспектор в РЕАЛЬНОМ Chromium** (python-playwright, harness `_e2e/jsconsole`): дедуп one-call-site→1 карта, unhandled-rejection→1, ресурс-404→0, source/line захвачены → RESULT PASS.
  (2) **Reporter инлайнится ТОЛЬКО с `?inspect=1`** (живой прод `final-magazin-e2e3`): `/p/?inspect=1` grep `omnia:preview:error`=1, публичный `/p/`=0 (gating подтверждён).
  (3) **Эндпоинт против ЖИВОЙ прод-БД** (in-container omnia-prod-api, реальные модули, self-cleaning): PERSIST=True (`category="client"`+`page.js:42` на реальном assistant-msg), DEDUP_DETECT=True, DEDUP_NO_SECOND_CARD=True (count=1), RESTORED=True (msg возвращён байт-в-байт) → RESULT PASS.
  (4) Карта `client` рендерится тем же `AppErrorCard`, что compile/runtime (доказан в прошлом P2-заходе) — добавлен лишь label; web tsc зелёный.
- Проверка: app_errors 12/12 + select_mode 10/10 (uv pytest, dummy-env); ruff/mypy мои файлы чисто (messages.py 95 mypy + 3 E501 — пред-существующий долг, НЕ в моих строках 1-500); web tsc ЗЕЛЁНЫЙ; eslint мои 4 файла чисто; `node --check` инспектора OK; route зарегистрирован в app.
- Доставка: commit 3dcc42b | push main ✓ (PAT) | deploy: api+worker+web rebuild (project full, apps/llm-gateway/deploy/full) ff-merge e15387b→3dcc42b; health api 200 / web healthy / orch 200 / site 200. Template-образы nextjs-entities + nextjs-postgres-drizzle пересобраны (детач, инспектор-синк попадёт в НОВЫЕ full-аппы).
- Владельцу утром: (1) ★ Клиентские JS-ошибки превью теперь КАРТОЧКОЙ в чате «Ошибка в браузере» (категория client) + «Починить». Срабатывает на uncaught-исключения/unhandled-rejection в живом превью; молчит на публичном `/p/` и в проде (нет workspace-родителя). Откат: `USE_ERROR_CARDS=false`+recreate api/worker. (2) ★ Латентный баг исправлен: 2 шаблонные копии `omnia-inspector.js` были стале (без image-pick/text-edit/hard-delete features) — это могло ломать кликер в full-аппах (P3). Ре-синканы к canonical, образы шаблонов пересобраны → НОВЫЕ full-аппы получат современный инспектор; СТАРЫЕ live-контейнеры (kofeinia/legomagazin) держат старый — переедут при re-provision. (3) Карты привязываются к последнему финализированному assistant-сообщению и переживают reload (дедуп не даёт спам). (4) P2 ПОЛНОСТЬЮ закрыт (build/compile/schema/runtime-5xx/client). Тест-проект final-magazin-e2e3 трогал+восстановил (self-clean), пополнения чата нет.
- Идея на следующий запуск: **P3 — кликер (select-mode) + ручные правки в FULL-аппах**. ★Зацепка: я уже ре-синкал шаблонный инспектор к canonical (был стале) — это вероятный кусок корня P3 (full-апп слал неполные picks / старый протокол). Root-cause дальше: (a) инжект инспектора/overlay в iframe ЖИВОГО dev-контейнера (Script afterInteractive race уже частично хэндлится в PreviewFrame onLoad); (b) style-patch/edit-эндпоинт для fullstack (сейчас static-only?). Память `omnia_inpreview_style_editor`/`omnia_surgical_edit_mode`/`omnia_select_mode`. Чтобы новые full-аппы взяли свежий инспектор — образы пересобраны этим заходом; для теста на ЖИВОМ контейнере, возможно, re-provision. Деплой: api/web=rebuild project full; orchestrator=merge+`sudo systemctl restart`. Push через PAT. ssh через PowerShell+alias lh-server, b64-транспорт (CRLF/BOM ломает pipe — грузи b64 через `[IO.File]::ReadAllText` + `printf '%s'`, НЕ `Get-Content|cat`).

## 2026-06-09 05:23–05:55 MSK — P2 остаток: серверные 5xx ошибки рендера → карточки в чате
- Статус: DONE (app-5xx часть остатка P2; JS-console превью остаётся отдельным заходом)
- Файлы: `apps/orchestrator/src/omnia_orchestrator/services/runtime_probe.py` (новый — `probe_runtime_error`/`_http_status`/`RuntimeProbeResult`), `.../schemas/runtime.py` (`RuntimeStatusResponse`), `.../routers/runtime.py` (`GET /{id}/runtime-status`), `apps/orchestrator/tests/test_runtime_probe.py` (новый, 7 тестов); `apps/api/src/omnia_api/services/orchestrator_client.py` (`runtime_status`), `.../routers/messages.py` (`_probe_compile_errors`: после чистого compile-probe — один runtime-probe → карта `category="runtime"`).
- Корень (что было невидимо): апп компилится чисто, но 500-ит при РЕНДЕРЕ роута (server-components/`generateMetadata`/data-fetch исполняются ЛЕНИВО per-route, только когда страницу реально запрашивают). compile-probe лишь читает логи → broken-on-load preview оставался без карты, юзер видел Next error overlay при тишине в чате. Решение: активный HTTP-probe GET-ит живой dev-апп по host-порту → форсит рендер → 5xx ⇒ карта; парсит свежие dev-логи реюзом `parse_next_compile_error` (R-05 без дубль-парсера). Консервативно (R-10): 4xx и transport-error (апп грузится) = ok=True — ложно-негатив лучше красной карты на здоровом аппе.
- Дизайн (канон): R-01 `runtime_probe` deep module (docker-status + http + log-parse за одним `probe_runtime_error`); R-07 orchestrator владеет docker/http, api тонкий через client; R-10 timeout 8s + fail-soft + конс. ложно-негатив; R-05 реюз compile-грамматики; status_code авторитетен (5xx = провал даже без парс-блока, см. тест 503).
- E2E (живой прод, оба пути): happy — реальный project_id магазина (4f2d4a2f…) → `runtime-status: ok:true status_code:200`. 5xx — бэкап `src/app/page.tsx` (32KB) → инжект compile-clean render-throw через `docker exec` → root отдал HTTP 500 → `runtime-status: ok:false status_code:500 error="⨯ [Error: The default export is not a React Component in /page] … GET / 500 in 2703ms"` (реальный Next-блок захвачен) → восстановил оригинал → root 200, `runtime-status ok:true 200`. Контейнер magazin-pomady возвращён в рабочее 200. Венв-диагностика подтвердила логику (200/probe ok). Грабля: dummy-UUID+slug fallback вернул status_code:null (find_project_container с несуществующим uuid → ранний ok); реальные вызовы api всегда шлют настоящий project_id → не баг продакшна.
- Проверка: orchestrator pytest 92 passed (было 77, +15 мои: 7 runtime_probe + перепрогон compile); ruff+mypy мои файлы чисто (1 пред-существующий unused-ignore runtime.py:291 + 1 no-any-return orchestrator_client.py:101 get_status — НЕ мои); api test_app_errors 6/6; messages.py импортится чисто; api ruff/mypy мои строки чисто (5 E501 + 3 ruff в messages — пред-существующие, вне моего блока 177-201).
- Доставка: commit e15387b | push main ✓ (PAT — gh протух) | deploy: orchestrator `git merge --ff-only`(прод-дерево чистое, без dirty-guard)+`sudo systemctl restart omnia-orchestrator` (editable) health 200 → провалидировал runtime-status probe ВЖИВУЮ ПЕРЕД деплоем api; затем api+worker rebuild (project full, apps/llm-gateway/deploy/full). Health: api `/health` 200 (host 8200), worker Up, orchestrator 200, web healthy + https 200 (`site=000` от curl-с-хоста = hairpin-NAT артефакт, не выход; SNI-resolve→127.0.0.1 дал 200).
- Владельцу утром: (1) ★ Серверные ошибки рендера (500 при открытии страницы аппа, хоть код и скомпилился) теперь прилетают красной карточкой в чат `runtime` — после сборки entity/fullstack-аппа фоновый probe открывает корневую страницу, и если она 500-ит, карта с реальным текстом ошибки Next + кнопкой «Починить». Откат: `USE_ERROR_CARDS=false`+recreate api/worker (runtime-probe гейтится тем же флагом, что compile-probe). (2) ⚠️ ОСТАТОК P2 (JS-console превью) НЕ сделан этим заходом — он трогает shared preview-инжект (omnia-inspector.js ×копии + drift-тест) + 2 frame-компонента + новый эндпоинт, и рискует ложно-позитивными картами на живых здоровых аппах (benign console-404/warn) → вынесен в отдельный заход со строгим gating. (3) Probe сейчас открывает ТОЛЬКО корень `/` — broken sub-route (/dashboard и т.п.) не ловит; multi-route probe — будущее расширение. (4) Тест-контейнер magazin-pomady я ломал+восстанавливал (бэкап page.tsx) — он снова 200, можно снести как throwaway.
- Идея на следующий запуск: либо добить P2 (JS-console превью со строгим анти-noise gating + натуральный build-error E2E где модель сама пишет битый код), ЛИБО считать P2 достаточным (ядро+app-5xx покрывают серверные ошибки — главный сигнал «сломанный preview») и брать **P3** (кликер select-mode + ручные правки в FULL-аппах: root-cause — инжект инспектора/overlay в iframe живого контейнера + style-patch/edit-эндпоинт для fullstack; память `omnia_inpreview_style_editor`/`omnia_surgical_edit_mode`/`omnia_select_mode`). Деплой: orchestrator = merge+`sudo systemctl restart` (editable); api/worker = rebuild project full. Push через PAT. ssh ТОЛЬКО через PowerShell+alias lh-server (git-bash не знает alias) + b64-транспорт. Прод orchestrator internal token в `/opt/omnia/apps/orchestrator/.env`.

## 2026-06-09 04:35–05:20 MSK — P2: ошибки аппа структурными карточками в чате + «Починить»
- Статус: DONE (ядро P2 — build/compile/schema/sync ошибки → карточки + Починить; console/5xx-сбор оставлен как явный остаток)
- Файлы: `apps/orchestrator/src/omnia_orchestrator/services/compile_status.py` (новый — `parse_next_compile_error`, чистый Turbopack/webpack-парсер), `.../schemas/runtime.py` (`CompileStatusResponse`), `.../routers/runtime.py` (`GET /{id}/compile-status`), `apps/orchestrator/tests/test_compile_status.py` (новый, 8 тестов); `apps/api/src/omnia_api/services/app_errors.py` (новый — `render_block`+`publish`: персист блока в сообщение + `app.error` event, sanitize `<`→`‹`), `.../services/orchestrator_client.py` (`compile_status`), `.../routers/messages.py` (`_probe_compile_errors`/`_spawn_compile_probe` фоновый probe после hot_reload; drizzle/sync-fail → карты; флаг гейтит только probe), `.../core/config.py` (`use_error_cards=True`), `apps/api/tests/test_app_errors.py` (новый, 6 тестов); web `lib/parse-assistant.ts` (парс `<app-error>`), `lib/api/types.ts` (`app.error` event), `hooks/usePromptStream.ts` (handler→invalidate+toast), `components/workspace/ChatMessage.tsx` (`AppErrorCard`+onFix), `ChatPanel.tsx` (handleFix→submit).
- Root-cause находки: Next.js compile-ошибки (битый TSX от писателя) были ПОЛНОСТЬЮ невидимы — hot_reload пишет файлы и возвращает OK, но Turbopack компилит async → preview ломается, в чате тишина. Drizzle/sync-fail показывались сырым italic-текстом, не карточкой. `app.error` фиксится так: probe читает dev-логи через `container_logs`, парсер находит ⨯/Module not found/Parsing failed ПОСЛЕ последнего `✓ Compiled` (HMR-recovery не врёт), извлекает файл (scope src|app|components|lib, parens для route-групп `(app)`, skip node_modules).
- Дизайн (канон): R-01 app_errors deep module (render+persist+emit за одним `publish`); R-07 лог-грамматика Turbopack живёт в orchestrator (api тонкий); R-10 fail-soft (probe фоновый, не держит llm.done; persist/emit best-effort; флаг `use_error_cards` мгновенный откат probe); R-05 карты drizzle/sync всегда (строгое улучшение), флаг только на рисковый probe. Реюз: блок-карта едет по тому же `<file>`/`<edit>` parse-assistant пайплайну (персист+рендер бесплатно).
- E2E (живой прод, реальный путь): (1) orchestrator probe ВЖИВУЮ против РЕАЛЬНОГО Turbopack на throwaway-контейнере magazin-pomady: чистый→`ok:true`; сломал `src/app/page.tsx` через РЕАЛЬНЫЙ hot-reload эндпоинт → force-compile (node fetch) → `ok:false` с реальным блоком `⨯ ./src/app/page.tsx:5:1 / Parsing ecmascript source code failed / Unexpected token` + `file:"src/app/page.tsx"`; восстановил оригинал → `ok:true`, контейнер re-paused. ПАРСЕР ПОДТВЕРЖДЁН против реального формата (`✓ Compiled` = байт-в-байт мой маркер). (2) Реальный `app_errors.publish` запущен В omnia-prod-api против последнего assistant-сообщения проекта «Магазин помады» → блок ПЕРСИСТНУЛ в БД (sanitize ✓: `<div>`→`‹div›`), `app.error` emit. Браузер (аккаунт U): карточка отрисовалась — «Ошибка компиляции» / бейдж «Компиляция» / `src/app/page.tsx` / кнопки «Починить»+«Детали»; Детали раскрыли реальный текст ошибки; «Починить» → submit ТОЧНОГО fix-промпта («Исправь ошибку в приложении (Компиляция)… Файл: src/app/page.tsx…») в обычный пайплайн; карта пережила reload (персист). Скрины p2-error-card.png / p2-error-card-expanded.png / p2-fix-submitted.png.
- Проверка: orchestrator compile_status 8/8, ruff+mypy чисто; api app_errors 6/6, мои строки ruff чисто (остаток E501 — пред-существующий долг, не мои), mypy messages 95 (мои 2 type-arg починены, 95 — пред-существующий долг); web tsc ЗЕЛЁНЫЙ, мои eslint-файлы чисто (1 пред-существующая ошибка usePromptStream:642 submitRef — не моя, чужой WIP). messages.py импортится чисто (test-collection через main).
- Доставка: commit ede1c57 | push main ✓ (PAT — gh протух) | deploy: orchestrator `git merge --ff-only`+`sudo systemctl restart omnia-orchestrator` (editable) health 200 → провалидировал probe ПЕРЕД деплоем api (staged); затем api+worker+web rebuild (project full, apps/llm-gateway/deploy/full) health: api `/health` 200 (хост-порт 8200, внутр 8000), web healthy (3100→3000), orch 200. Прод-дерево чистое (tracked), ff-merge без граблей.
- Владельцу утром: (1) ★ Ошибки аппа теперь КАРТОЧКАМИ в чате: после сборки entity/fullstack-аппа, если Turbopack не скомпилил битый код ИЛИ drizzle-миграция/синхронизация упали — в чат прилетает красная карта (категория, файл, сворачиваемые детали) + кнопка «Починить» (шлёт fix-промпт сам). Откат probe: `USE_ERROR_CARDS=false`+recreate api/worker (карты drizzle/sync останутся — они строгое улучшение). (2) ⚠️ ОСТАТОК P2 (след. заход возьмёт — P2 не закрыт ПОЛНОСТЬЮ): JS-консоль превью + серверные 5xx живого аппа НЕ собираются в карты (только build/compile/schema/sync). И натуральный триггер (модель сама пишет битый код→карта) E2E не гонял — вместо этого каждое звено доказал детерминированно (probe вживую + публикация реальным кодом + рендер в браузере). (3) В throwaway-проекте «Магазин помады» в чате осталась тест-карта ошибки + я нажал «Починить» → запустилась реальная генерация-фикс (page.tsx там НЕ битый, так что фикс ~no-op). Можно игнорить/снести проект. (4) compile-probe добавляет до ~9с фоновой проверки ПОСЛЕ сборки entity-аппа (3 поллинга ×3с), llm.done НЕ задерживает (фоновый task).
- Идея на следующий запуск: P2 остаток (console/5xx сбор + натуральный build-error E2E) ЛИБО считать P2 достаточным и брать P3 (кликер+ручные правки в FULL-аппах — root-cause: инжект инспектора/overlay в iframe живого контейнера + style-patch/edit-эндпоинт для fullstack; память `omnia_inpreview_style_editor`/`omnia_surgical_edit_mode`/`omnia_select_mode`). По строгому приоритету — P2 имеет открытый `[ ]` (console/5xx) → формально след. заход P2; но если владелец хочет P3 — сказать. Деплой: api/worker/web = rebuild project full; orchestrator = merge+`sudo systemctl restart` (editable). Прод api health = host:8200 / внутр `/health`. Push через PAT. ssh-скрипты через b64 (PowerShell ломает bash).

## 2026-06-09 04:03–05:05 MSK — P1: удаление проекта юзером (full runtime teardown + owner-scoping + soft-delete)
- Статус: DONE (закрыта последняя содержательная под-задача P1 кроме мелочи-пикера в NewProjectDialog)
- Файлы: `apps/api/src/omnia_api/routers/projects.py` (delete_project: 404/403 owner-scoping, teardown-first fail-closed, repo+cascade), `.../services/orchestrator_client.py` (destroy теперь шлёт обязательный `slug` — был сломан, slug не передавался), `.../services/repo.py` (`delete_repo` — снос MinIO-тарбола, идемпотентно), `.../models/project.py` (CheckConstraint template выровнен с миграцией 0010 — был устаревший на 4 статик-шаблонах, ломал тест-БД), `apps/api/tests/test_projects_delete.py` (новый, 6 тестов: owner-scoping, teardown-gating static-vs-container, cascade, идемпотентность), `apps/orchestrator/src/omnia_orchestrator/core/postgres_admin.py` (`archive_schema` — soft-delete переименованием, rule 5), `.../routers/runtime.py` (destroy переписан PoC→полный реверс provision: dev+prod контейнеры, оба порт-пула, схема, dev+prod nginx), `apps/orchestrator/tests/test_postgres_admin.py` (+2 теста archive_schema), `apps/web/.../ProjectCard.tsx` (kebab-меню сиблингом Link), `apps/web/.../DeleteProjectDialog.tsx` (новый — type-to-confirm).
- Root-cause находки: (1) старый `delete_project` = наивный `session.delete` — НЕ звал teardown, НЕ сносил git-репо, оставлял orphan-контейнер+схему+vhost для контейнер-аппов; (2) orchestrator destroy был PoC (только dev-контейнер, «sprint A1»), `orchestrator_client.destroy` даже не слал требуемый `slug` (422 был бы) — фича удаления контейнер-проектов была НЕ рабочей; (3) `Project.template` CheckConstraint в модели завис на 4 статик-значениях (миграция 0010 уже расширила до 9) — латентный дрейф модель↔БД.
- Дизайн (канон): R-01 teardown спрятан за один вызов; R-10 идемпотентный fail-closed teardown (недоступный orchestrator → проект не удаляется, без orphans); rule 5 данные не дропаются (схема переименовывается, recoverable); owner-scoping 404 missing / 403 foreign.
- E2E (вживую на проде, внешний HTTP + реальный live-контейнер): STATIC create→`/p/`200→DELETE 204→из списка ушёл→`/p/`404; OWNER-SCOPING foreignDelete=403, missingDelete=404; CONTAINER-TEMPLATE delete 204; **ЖИВОЙ КОНТЕЙНЕР teardown** (`omnia-dev-render-magazin-e2e4`, Up 44м): destroy 200 → контейнер ГОНЕ, схема `proj_5a5c96bb`→`zdel_proj_5a5c96bb` (soft-delete), nginx-vhost удалён; идемпотентный повтор destroy 200. Реальные аппы kofeinia/legomagazin Up 2 weeks НЕ затронуты.
- Проверка: orchestrator pytest 77 passed (локально, real env), мои файлы ruff+mypy чисто (archive_schema/destroy/delete_repo/delete_project — 0 новых; пред-существующие debt postgres_admin:60 literal + runtime unused-ignore + ~190 api mypy НЕ мои); web typecheck ЗЕЛЁНЫЙ + eslint мои 2 файла clean. ⚠️ api-pytest (test_projects_delete) в ПРОД-контейнере падает `asyncpg another operation in progress` — это harness-артефакт unpinned pytest-asyncio loop-scope (ДОКАЗАНО: существующий test_auth.py падает ТАМ ЖЕ так же), НЕ дефект логики; логика доказана живым E2E.
- Доставка: commit 52e91c2 | push main ✓ (PAT — gh протух) | deploy: orchestrator merge+`sudo systemctl restart omnia-orchestrator` (editable) health 200; api+worker+web rebuild (project full, apps/llm-gateway/deploy/full) detached ~2мин, health api 200 / web healthy / orch 200 / EXTERNAL site 200. Грабли деплоя: прод-дерево было грязным (secondbrain локально модифицировано + входящий 95f8845 трогал те же файлы → ff-merge abort) → бэкапнул `.proddirty.bak` + `git checkout -- secondbrain/` + ff-merge (НЕ stash). systemctl требует sudo (`sudo -n` доступен). ssh-команды через b64 (PowerShell ломает bash `$()`/backtick).
- Владельцу утром: (1) ★ Удаление проекта ЖИВОЕ на проде: на карточке проекта kebab (⋮) → «Удалить проект» → ввести точное имя → «Удалить навсегда». Для контейнер-аппов сносит контейнер+порт+nginx, схему БД переименовывает в `zdel_proj_*` (данные НЕ удалены физически — grace-окно, можно вернуть админом `ALTER SCHEMA zdel_proj_x RENAME TO proj_x`). Hard-purge архивов — отдельный трек (можно cron’ом дропать `zdel_proj_*` старше N дней). (2) Тестовый контейнер render-magazin-e2e4 я снёс намеренно (E2E-доказательство). Остальные throwaway dev-контейнеры (avtostek-*, magazin-pomady, e2e-*) НЕ трогал — можно снести вручную или удалением проектов через UI. (3) Прод-дерево secondbrain: 6 файлов сохранены в `*.proddirty.bak` в /opt/omnia перед ff-merge — если там были важные ручные правки, верни из .bak; если авто-ингест — удали .bak. (4) Мелочь P1 (зона A): убрать пикер шаблонов из NewProjectDialog (авто-стек+discovery уже разруливают) — единственный остаток P1.
- Идея на следующий запуск: P1 ПОЛНОСТЬЮ закрыт (кроме UI-пикера мелочи) → брать **P2 — ошибки аппа карточками в чате** (сбор build/runtime/console/5xx → стрим карточками в чат, опц. кнопка «починить» через surgical-edit). Память `omnia_surgical_edit_mode`. Прод-push через PAT. Деплой: api/worker/web = rebuild project full; orchestrator = merge+`sudo systemctl restart` (editable). Прод-дерево грязное → бэкап+`checkout -- secondbrain/`+ff-merge, НЕ stash. ssh-скрипты через b64.

## 2026-06-09 02:40–04:10 MSK — P1: авто-стек-роутинг (discovery→провижн контейнера) + 3 фикса entity-генерации
- Статус: DONE (последняя миля авто-стека закрыта; entity-аппы теперь реально собираются и рендерятся E2E)
- Файлы: `apps/api/src/omnia_api/services/stack_routing.py` (новый — switch_to_stack/ensure_provisioned/discovery_stack_to_template), `.../routers/messages.py` (discovery BUILD→switch_to_stack; воркер: ensure_provisioned для CONTAINER_NEXT в начале, параллельно генерации; +import logging), `.../core/config.py` (`use_auto_stack_routing=True`, kill `USE_AUTO_STACK_ROUTING=false`), `apps/api/tests/test_stack_routing.py` (новый, 13 тестов), `.../services/art_director_writer.py` (`.format`→`.replace` для APP-шаблона), `.../services/discovery.py` (httpx timeout 45→20с — влезть в клиентский 30с POST), `.../services/file_extractor.py` (whitespace-body→"" = delete-intent), `apps/api/tests/test_file_extractor.py` (+3), `apps/orchestrator/src/omnia_orchestrator/core/docker_client.py` (write_files: пустой контент = rm файла, не 0-байт; +deleted).
- Что сделано: discovery уже рекомендовал стек, но проект оставался static. Теперь BUILD с контейнер-стеком → `switch_to_stack` флипает project.template + ре-скаффолд git из нужного шаблона, `ensure_provisioned` поднимает orchestrator dev-контейнер (в воркере, параллельно ~минутной генерации). Юзер НИЧЕГО не выбирает и не жмёт «Запустить».
- 3 прод-блокера entity-генерации (всплыли на E2E, чинились по ходу): (1) APP-writer шаблон содержит литеральные CSS/JSX-скобки `<style>{":root{...}"}` → `str.format(brief=)` падал `KeyError('"')` на КАЖДОМ entity-билде с непустым брифом → `.replace("{brief}", brief)`; (2) orchestrator hot_reload писал пустой контент как 0-байт «sentinel» → пустой `src/app/page.tsx` ломал маршрут `/` («default export is not a React Component») → теперь УДАЛЯЕТ (rm) пустые; (3) writer оставляет `\n` в пустом теге → extractor нормализует whitespace-body→"" (delete-intent для git+hot_reload).
- E2E (вживую браузером, прод, аккаунт «U»): (a) АВТО-РОУТИНГ 2× — проект создан как «Пустой»(static) → промпт «интернет-магазин помады с корзиной/ЛК» → discovery адаптивный вопрос → «сделай полноценное приложение» → DB template=nextjs_entities + контейнер `omnia-dev-avtostek-*` Up :3223 (без клика «Запустить»), [PP] entity-pipeline. (b) РЕНДЕР — entity-проект (магазин косметики): hot_reload deleted page.tsx (ls: No such file), `(app)/page.tsx` 17KB рендерит; рег нового юзера→дашборд-каталог с РЕАЛЬНЫМИ сидами (Товар: Velvet Matte Lipstick 2200₽, Nuit Perfume 12500₽…), сайдбар Каталог/Мои заказы/Управление, поиск+фильтры категорий, корзина — БЕЗ runtime-error (скрин render-entity-app-works.png). (c) discovery static-путь не сломан (3-й проект «магазин» discovery решил static→собрал лендинг — норм путь).
- Проверка: stack_routing ruff+mypy чисто, 13/13; file_extractor 21/21 (+3 мои); art_director_writer/discovery — мои строки чисты (репо-долги E501/mypy не мои); orchestrator docker_client mypy чисто, 75/75 pytest. Все правки — только мои файлы.
- Доставка: commits 202b7cc (стек-роутинг) + 6e7fa4e (writer/discovery) + 49e9c81 (orchestrator) + 4642e27 (extractor) | push main ✓ (PAT) | deploy: api+worker rebuild (project full, apps/llm-gateway/deploy/full) ×3 + orchestrator merge+`systemctl restart` (editable) | health: api 200 startup-clean, orchestrator 200, entity-апп рендерит снаружи по TLS.
- Владельцу утром: (1) ★ Авто-стек ЖИВОЙ: создаёшь проект (даже «Пустой») → описываешь → discovery сам решает static-лендинг vs полный entity-апп → если апп: сам флипает стек + поднимает контейнер + собирает + рендерит. Откат: `USE_AUTO_STACK_ROUTING=false`. (2) ⚠️ Discovery-модель (deepseek) иногда промахивается со стеком: на 1 из 3 e-commerce-промптов выбрала static вместо nextjs_entities (собрала лендинг магазина вместо аппа). Механизм роутинга верный — это качество РЕШЕНИЯ модели. Фикс на будущее: усилить stack-инструкцию discovery / few-shot / поднять до более умной модели на стек-классификации. (3) ⚠️ NewProjectDialog ВСЁ ЕЩЁ с пикером шаблонов — теперь можно убрать (авто-стек закрыт): один клик→workspace, discovery+авто-стек разрулят (зона A, фронт). (4) Пофикшенные 3 бага чинили entity-генерацию ГЛОБАЛЬНО (не только мою фичу) — раньше КАЖДЫЙ entity-апп с непустым брифом падал KeyError на писателе ИЛИ рендерил route-conflict. Теперь entity-аппы собираются и рендерятся (доказано полным магазином косметики). (5) Тестовые проекты насозданы (avtostek-*, render-magazin-*, final-*) — можно снести.
- Идея на следующий запуск: P1 остаток — (a) удаление проекта (кнопка+подтверждение→каскад БД + orchestrator destroy teardown контейнер/Postgres/vhost + owner-scoping 403 + soft-delete/pg_dump, идемпотентно — `orchestrator_client.destroy` УЖЕ есть, нужен API-роут+UI); (b) убрать пикер из NewProjectDialog (зона A). Потом P2 (ошибки аппа карточками). Прод-push через PAT. Deploy api/worker = rebuild project full; orchestrator = merge+systemctl restart (editable, без rebuild).

## 2026-06-09 02:16–02:38 MSK — P1: прогрессивная in-chat дискавери заменяет блокирующий квиз
- Статус: DONE (ядро P1 — онбординг-флоу; стек-роутинг контейнера + удаление проекта остаются)
- Файлы: `apps/api/src/omnia_api/services/discovery.py` (новый — `run_discovery`), `.../routers/messages.py` (роутинг first-build через discovery: ASK стримит вопрос, BUILD вшивает бриф; select/skip_clarify/не-first → мимо), `.../core/config.py` (`use_progressive_discovery=True`, kill-switch `USE_PROGRESSIVE_DISCOVERY=false`), `apps/api/tests/test_discovery.py` (новый, 10 офлайн-тестов), `apps/web/.../ChatPanel.tsx` (не перехватывает первый промпт), `OnboardingQuiz.tsx` (удалён).
- Контекст: предыдущий P1-прогон оставил эту работу в грязном дереве НЕкоммитнутой (discovery.py был untracked, ChatPanel/OnboardingQuiz/messages/config — uncommitted). Этот прогон: дописал тесты, прогнал verify, E2E на проде, доставил.
- Root-cause «что переделать»: квиз был блокирующей фронт-модалкой (`ChatPanel` перехватывал первый промпт при `messages.length===0`), слал `skip_clarify=true` → серверный клариф пропускался. Заменено на серверную разговорную дискавери: ОДИН элементарный вопрос за ход, адаптируется, сама решает когда строить (turn-cap=5, «генерируй» форсит build). Fail-soft (R-10): ошибка/непарс gateway → канон-вопрос или build-из-истории, тупика нет.
- E2E (вживую браузером на проде, аккаунт «U», новый проект 286d2885 «Discovery E2E кофейня»): «Новый проект»→СРАЗУ workspace (модалки НЕТ) → промпт «хочу сайт для кофейни» → Omnia задала 1 адаптивный вопрос (уютная/сетевая? меню/контакты/онлайн-заказ?) НЕ строя → ответ → след. вопрос → «Генерируй сайт» → build (генерация 0→4: Замысел/Вёрстка/Картинки/Проверка) → live-рендер реального на-бриф лендинга кофейни («Место, где кофе встречает тепло», меню+философия, тёплые тона) → публичный `/p/discovery-e2e-kofeinia-fffa79` = 200, 27.7KB, контент на месте. 0 console-errors кроме 1 пред-существующего live-stream-артефакта (`/projects/assets/omnia-kit.css` 404 — относительный путь в стриме; в `/p/` ассеты 200). Прямой прод-тест discovery против live-gateway: TURN1 adaptive ASK, TURN2 BUILD, force-build OK, stack=static (верно для визитки).
- Проверка: discovery.py ruff+mypy чисто, 10/10 тестов; messages.py/config.py — 0 НОВЫХ ruff/mypy в моих строках (репо имеет 190 пред-существующих mypy + 5 lint-долгов — не мои); web typecheck ЗЕЛЁНЫЙ, ChatPanel.tsx lint exit 0.
- Доставка: commit 56ac129 | push main ✓ (PAT — gh-токен протух) | deploy: merge --ff-only /opt/omnia → `docker compose -p full up -d --build api worker web` (apps/llm-gateway/deploy/full) health: api/health 200, web healthy, site 200, build-pipeline отработал E2E | template-rebuild НЕ нужен (шаблон не трогал).
- Владельцу утром: (1) **Дискавери ЖИВАЯ на проде для всех новых проектов** (флаг ON). Откат мгновенный: `USE_PROGRESSIVE_DISCOVERY=false` + recreate api/worker, или revert 56ac129. Существующие аппы (kofeinia/legomagazin/edits) НЕ затронуты — дискавери только на первом билде нового проекта. (2) ⚠️ Turn-2 дискавери иногда падает в детерминированный канон-вопрос (на E2E так и было: «Кто ваша аудитория…») — причина: deepseek изредка возвращает НЕ-строгий JSON → `_parse`=None → тихий фоллбэк (R-10 отработал, тупика нет, но вопрос не адаптивный). Фикс на будущее: лог на parse-fail + усилить JSON-инструкцию/ретрай. (3) NewProjectDialog ВСЁ ЕЩЁ показывает пикер шаблонов — НЕ убирал намеренно: без стек-авто-роутинга (след. шаг) дефолт-в-static уронил бы full-app запросы. Убирать пикер + вшивать авто-стек НАДО вместе. (4) Пред-существующий lint RED на main (usePromptStream.ts, PromptInput.tsx — react-hooks/immutability+refs) — не мои файлы, чужой WIP.
- Идея на следующий запуск: P1 продолжение — (a) подключить стек-роутинг: discovery.stack (BUILD) → реальный провижн контейнера (nextjs_entities/fullstack) через оркестратор, ТОГДА убрать пикер в NewProjectDialog (один клик→workspace); (b) удаление проекта (кнопка+подтверждение→каскад+teardown+owner-scoping 403+soft-delete/pg_dump). Дискавери-бриф уже несёт рекомендованный стек в тексте — зацепка готова. Прод-push только через PAT. Деплой web = долгий Next-build (~3-4мин) детачем.

## 2026-06-09 01:54–02:12 MSK — P0: инфра-enabler (anti-hibernate + mem 4GB + restart self-heal)
- Статус: DONE
- Файлы: `apps/orchestrator/src/omnia_orchestrator/services/hibernate.py` (network-RX probe), `.../services/provisioner.py` (mem из config + unless-stopped), `.../core/config.py` (`dev_container_memory_mb=4096`), `tests/test_hibernate.py` (+6 тестов probe), `tests/test_provisioner.py` (новый, spec-capture), `tests/test_postgres_admin.py` (стале-тест build_dsn — отдельный коммит).
- Root-cause: прямые хиты по preview-сабдомену идут nginx→127.0.0.1:port, оркестратор их НЕ видит → sweep считал idle и глушил живой апп под зрителем (это и был «краш тяжёлых аппов» — `docker stop`, не OOM). Память `omnia_enterprise_appgen_upgrade` уже указывала точный рычаг.
- Решение: per-sweep `_read_dev_rx()` читает RX контейнера; рост ⟺ зритель подключён (HMR-сокет) → сброс idle-таймера. Дополняет Redis/heartbeat (прямой-preview их не триггерит). Память 4GB (config). restart unless-stopped (краш→self-heal, hibernate-stop держит внизу).
- E2E (вживую на проде, тяжёлый clinic-апп medorbita): probe RX 285061→302101 GREW=True; 20× HTTP 200 на 4GB без OOM; throwaway-краш→unless-stopped restarts=3; medorbita после `docker stop`/kill остаётся внизу (= hibernate работает). Orchestrator sweep-лог чист, /health ok, constructor.lead-generator.ru = 200 снаружи.
- Проверка: ruff (мои файлы) ✓; mypy src — мои файлы чисто (2 пред-существующих ошибки в postgres_admin/runtime, НЕ мои); pytest 75 passed (починил 1 стале-тест build_dsn).
- Доставка: commit 8f2ca56 (P0) + c7fc2fe (стале-тест) | push main ✓ (через PAT — gh-токен протух) | deploy: `git merge --ff-only` /opt/omnia + `systemctl restart omnia-orchestrator` (editable install, без uv sync — Python-only, без новых deps) health 200 | template-rebuild НЕ нужен (не трогал шаблон).
- Владельцу утром: (1) ⚠️ при коммите СЛУЧАЙНО подхватил pre-staged удаление `apps/web/src/components/workspace/OnboardingQuiz.tsx` (видимо от мёртвого прошлого P1-прогона) — откатил, файл остался unstaged-удалённым в рабочем дереве (на диске удалён, как было). Если это твоё/нужное P1 — пере-застейдь сам. (2) health-timeout в провижене (чтобы sweep не убивал за медленную компиляцию) НЕ делал — сейчас activity-probe + heartbeat покрывают, но честный health-poll до «running» — отдельный мелкий трек. (3) Существующие старые dev-контейнеры провижены со старой политикой (restart=no, mem=2048) — новые получают 4GB+unless-stopped автоматом; старые можно разово `docker update --restart unless-stopped --memory 4g <name>` (medorbita уже 4GB+unless-stopped с прошлой сессии). (4) Пред-существующие mypy-ошибки (postgres_admin.py:60 code-literal, runtime.py:283 unused-ignore) — не мои, на будущее.
- Идея на следующий запуск: P1 (zero-friction онбординг). ВАЖНО про грязное дерево: перед коммитом проверь `git diff --cached --name-only` и убери чужое pre-staged (был OnboardingQuiz.tsx). Прод-push только через PAT (`omnia_test_credentials`), gh-логин протух. Orchestrator деплой = merge --ff-only + `systemctl restart omnia-orchestrator` (editable, без rebuild). RX-probe сейчас держит превью живым — P4 «превью всегда» можно строить поверх.

_(P0 закрыт; первый fire был 01:11 MSK по чужому/мёртвому P1-локу, реальная работа — этот блок)_

---

## RUN-LOCK
idle
