# Omnia.AI — путь к enterprise-оркестратору: тезисный план + оценка проекта

> Дата: 2026-06-29. Анализ на свежем коде после `git pull` (HEAD `0647b6e`, было отставание на 51 коммит).
> Метод: 8 параллельных читателей по подсистемам реального кода → сведение → точечная перепроверка ключевых утверждений.
> Каждый факт — с якорем `file:line`. Предыдущий доклад `2026-06-28-prezentaciya-analiz-produkta.md` устарел на 51 коммит; это его пересборка на актуальном HEAD.

---

## 0. Главный тезис (повторить трижды)

> **Сдвиг уже произошёл: раньше — «одношот LLM → regex делит вывод на файлы», сейчас — серверная агентная инженерия (ReAct-цикл с инструментами против живого Docker-контейнера).**
> Движок настоящий, не демо. Потолок до enterprise — **не модель и не генерация**, а три вещи: **изоляция (multi-tenant security)**, **зубы у ship-гейта для контейнерных апп**, и **коммерческая/операционная зрелость** (платежи, бэкапы, CI, мониторинг).
> Плюс горсть **дешёвых wiring-багов**, где готовый механизм просто не подключён (часы работы, ×-кратный выигрыш).

---

## 1. ДО → ПОСЛЕ (то, о чём ты спрашивал по памяти)

| | ДО (text-генератор) | ПОСЛЕ (агентная инженерия) — **уже дефолт** |
|---|---|---|
| Как строит | промпт → 1 оборот модели → regex парсит → файлы коммитятся | цикл `plan → act → observe → verify → fix until green` |
| Обратная связь | ноль (модель не видит ошибок) | видит typecheck, runtime-5xx, логи, скриншот, живые доки |
| Инструменты | нет | 11: `list_dir/read_file/grep/write_file/edit_file/build/bash/read_logs/runtime_check/see/done` (`agent_builder.py:68-71`) |
| Надёжность | «угадал/нет», ~40–60% | ~85–95% при достаточном бюджете шагов |
| Артефакт | сломанные файлы возможны | зелёный, проверенный, экспортируемый код |

**Ключевой переключатель инвертирован.** Раньше всё мощное стояло за флагами `False`. Коммит `e63cb5c` перевёл **24 флага `False→True`** в дефолтах кода — и HEAD это подтверждает. Агентный цикл (`use_agentic_builder=True`, `config.py:805`), green-before-done, gate-feedback self-heal, runtime functional-гейт, vision-аудит, edit auto-repair «до талого», реальный full-stack бэкенд по умолчанию — **всё ON**. Это уже не прототип.

---

## 2. Оценка проекта — честный scorecard по 8 срезам

| Срез | Статус | Вердикт одной строкой |
|---|---|---|
| **Агентный цикл** | 🟢 Сильный | Настоящий ReAct с EYES, цикл-брейкеры, эскалация, green-before-done, server-side backstop. Архитектура enterprise, остались тюнинг-зазоры. |
| **Механизм правок** | 🟡 Дотянуть | Сделал шаг от regex к агентному edit+auto-repair, но главный рычаг надёжности (forgiving-match) написан и НЕ подключён к живому пути. |
| **Гейты качества** | 🟠 Дыра | Freeform-лендинги имеют реальный блокирующий гейт. Контейнерные апп (самое ценное) — **ни одного блокирующего гейта**, всё advisory. |
| **Sandbox / оркестратор** | 🟠 Тёмный | Control-plane продакшн-уровня, но изоляция (gVisor/egress/netns/harden) **вся написана и выключена по дефолту**. |
| **Стеки / роутинг** | 🟡 Web-ядро | 4 веб-стека production-ready. FastAPI-API и Telegram-бот собраны, но НЕдоступны из роутера. Мобайл/десктоп/компилируемые — нет. |
| **Конфиг-флаги** | 🟢 Сильный | 24 флага ON и реально wired в heal-loop. 3 footguna намеренно OFF (обоснованно). |
| **Знания / Context7 / skills** | 🟡 Сломан флагман | Архитектура enterprise (live-docs, skill-инъекция, RAG), но `docs`-инструмент **недоступен из цикла** (1 строка), progressive disclosure не реализован. |
| **Enterprise-ops** | 🔴 Не готов | Нет платежей (ЮKassa), нет бэкапов, нет CI, нет метрик, health — заглушки, `/topup` — открытый эксплойт. |

**Сводный вывод:** ядро «инженер, который делает→проверяет→чинит» — реально и работает. До «продаём enterprise-результат» мешают не возможности генерации, а **периметр** (безопасность, гейт-зубы, деньги, durability) и несколько **неподключённых готовых механизмов**.

---

## 3. Что РЕАЛЬНО осталось — по приоритету усилия×эффект

### 🔴 БЛОКЕРЫ enterprise (нельзя продавать без этого)

1. **Изоляция арендатора в проде выключена.** Агент гоняет произвольный `bash` в dev-контейнере под `runc`, в общей сети, с открытым egress. gVisor, per-project netns, egress-allowlist, harden — **всё написано и fail-safe**, но дефолты небезопасны (`apps/orchestrator/.../core/config.py:45,50,62,78`). Открытый egress = вектор утечки общего MinIO-ключа из env (подтверждено). → Подготовить хост + флипнуть 4 env. **Дни.**
2. **Контейнерные апп шипятся без блокирующей проверки.** Functional+security E2E-гейт — единственное доказательство «работает и не течёт» — запускается **только для realtime** и при FAIL лишь дописывает предупреждение в чат, потом коммитит (`messages.py:2996-3052`). → Сделать финальный FAIL блокирующим + распространить на entities/fullstack/spa. **Дни–недели.**
3. **RLS в сгенерированных приложениях не существует** — только колонка `userId`, политик нет (`templates/nextjs-postgres-drizzle/src/lib/db/schema.ts:63`). Корпоративный клиент не примет апп, где сырой запрос видит чужие строки. → Фикс `scoped.ts` + `ENABLE/FORCE ROW LEVEL SECURITY` + policy + gate-проверка `pg_policies`. **Недели.**
4. **Денег нет.** ЮKassa отсутствует, `/topup` — самокредитование без rate-limit до 1M ₽ (`wallet.py:45-56`). И эксплойт, и невозможность принять оплату. → Закрыть `/topup` за admin немедленно (**часы**); интегрировать ЮKassa с webhook+идемпотентностью (**недели**).
5. **Нет бэкапов Postgres** ни платформы, ни юзер-данных. Потеря диска = потеря всего. → backup-сервис в compose (nightly `pg_dump`→MinIO + retention) + restore-runbook. **Дни.**

### 🟢 ДЕШЁВЫЕ wiring-победы (часы, готовое просто не подключено)

6. **`docs` (Context7) недоступен из цикла.** Флагман анти-галлюцинаций коммита `0647b6e` мёртв: `"docs"` нет в `_KNOWN_ACTIONS` (`agent_builder.py:68-70`), хотя handler (`:1127`) и промпт его рекламируют → `parse_action` отвергает каждый вызов как стол. **Подтверждено.** → +1 строка в frozenset + контракт-тест. **Часы.**
7. **Forgiving-matcher не подключён к живому edit_file** — это ×10-рычаг надёжности правок на дешёвой модели. Indent-tolerant `_match_span` написан (`file_extractor.py:957-1007`), но живой `edit_file` всё ещё byte-exact `search not in current` (`agent_builder.py:1159-1179`). → Вынести `_match_span` в общий хелпер и вызвать в `edit_file`. **Часы.**
8. **Эскалация модели теряется на продолжениях.** Auto-continue-сегменты и heal-заходы зовут `run_agent_build` БЕЗ `escalate_model` (`messages.py:2590,2670,3013`) → билд, которому в сегменте 1 нужна сильная модель, в сегментах 2–6 снова на дешёвой. → Пробросить escalated-состояние вперёд. **Часы.**
9. **role_gate / security_gate — мёртвый код при флагах ON.** `run_role_gate`/`assert_security_headers` не вызываются нигде (**подтверждено grep**), хотя `use_role_gate=True`/`use_security_gate=True`. «Secure from prompt 1» декларируется, но не исполняется. → Либо wire в container-gate-loop, либо выключить флаги, чтобы конфиг не врал. **Дни.**
10. **Безопасные включения без риска:** `sast_gate_blocking=True` (после A/B), `acceptance_gate_repair_passes=1` + `acceptance_taste_repair_passes=1` (анти-однообразие без auto-regenerate-спирали), `app_self_repair_passes=2`. **Часы.**

### 🟠 ЭФФЕКТИВНОСТЬ цикла (часы–дни)

11. **Бюджет шагов — плоская 40 на всё** (`config.py:810`): простой лендинг и realtime-мессенджер получают одинаково. → max_steps от стека+сложности (landing 20 / entities 40 / realtime,api 60).
12. **Нет prompt-cache** на статичной «голове» промпта — она переотправляется каждый шаг ×6 сегментов. → `cache_control` breakpoint для cache-capable моделей.
13. **Progressive disclosure не реализован** — `load_stack_skills` дампит ВСЕ тела скиллов в каждый промпт (`agent_builder.py:884-893`), против собственных INDEX-инструкций. → инжектить только INDEX, тело читать по матчу.

### ⚙️ ОПЕРАЦИОННАЯ зрелость (дни–недели)

14. Нет CI (`.github/workflows` отсутствует) — шаблоны мёрджатся без typecheck/test. 15. Нет Prometheus-метрик; Sentry только в orchestrator, не в `apps/api`. 16. Health-эндпойнты — заглушки без проб БД/Redis/Docker (`health.py:12`). 17. Шаблоны без тестов (0 файлов в 6 шаблонах). 18. Один VPS без оркестрации нод (~10–15 тяжёлых сборок = потолок); rate-limit на in-memory storage. 19. Кастом-домены (BYO) не поддержаны вовсе.

---

## 4. «Может ли писать на ВСЕХ стеках с максимальной эффективностью?»

**Прямой ответ: на вебе — да; «все стеки» — достижимо, но пока нет; ближайшие победы дешёвые.**

| Класс | Статус | Что нужно |
|---|---|---|
| Веб-SPA, full-stack Next+Postgres, entities, realtime | 🟢 Production-ready | — (это и есть enterprise-веб) |
| **FastAPI REST API**, **Telegram-бот** | 🟡 Собраны, но НЕдоступны | Один список: добавить `api`/`tgbot` в `discovery._STACKS` + intent-роутинг. **Дни.** |
| Go / Java-Spring / .NET REST | 🔴 Нет | Зеркалят `fastapi-postgres` (HTTP-на-3000 + Postgres) после phase7 generic-provision. **Дни/стек.** |
| Произвольный Docker (любой язык разом) | 🔴 Нет | Generic-стек: user-supplied `Dockerfile.dev` + `Process()`-readiness. **Недели.** |
| Скрипты/CLI (`code`-стек) | 🟡 Только генерит исходники | Дать runner: Process-контейнер + artifact-export. **Недели.** |
| Настоящий Windows `.exe` | 🟢 Есть (PyInstaller+NSIS) | — |
| Android `.apk` / iOS | 🔴 Нет кода | PWA→TWA (Bubblewrap) + Android SDK build-контейнер. **Недели — главный ценностный пробел.** |

**Что блокирует «все стеки эффективно»:** не модель, а **phase7 generic-provision** (readiness/env/migrate/log per-stack — спроектирован, не внедрён) — без него Python/бот-стеки второго сорта (фейковые health-серверы, нет миграций). После phase7 новые HTTP-стеки добавляются за дни; мобайл/десктоп — реальные недели и текущий потолок.

**Максимальная эффективность письма** упирается в пункты 6–13: forgiving-match, живой `docs`, непрерывная эскалация, бюджет-по-сложности, prompt-cache, progressive disclosure. Это и есть «×-кратные» рычаги harness'а, а не «подождём модель получше».

---

## 5. Фазовая дорожная карта (всё за `USE_*`, мгновенный откат, exit = живой E2E)

- **Фаза 0 — Дешёвые wiring-фиксы (дни).** `docs`→`_KNOWN_ACTIONS` + контракт-тест; `_match_span`→`edit_file`; эскалация на продолжениях; `/topup` за admin. Низкий риск, высокий эффект.
- **Фаза 1 — Sandbox hardening (БЛОКЕР широкого rollout).** runsc + harden + isolate_network + egress-proxy. Exit: попытка cross-tenant/egress блокируется (E2E).
- **Фаза 2 — Зубы гейтам.** Финальный functional FAIL = блок; обобщить гейт за пределы realtime; `sast_gate_blocking=True`; wire или выключить role/security гейты. Exit: контейнерный билд на «красном» НЕ шипится.
- **Фаза 3 — RLS + multi-tenant изоляция данных.** `scoped.ts` + FORCE RLS + policy + gate-проверка. Exit: чужой юзер не видит чужие строки (live isolation E2E).
- **Фаза 4 — Коммерция/durability.** ЮKassa (webhook+идемпотентность), бэкапы Postgres + restore-runbook.
- **Фаза 5 — Стеки.** Wire `api`/`tgbot`; внедрить phase7 generic-provision; Go/Java REST; generic-Dockerfile.
- **Фаза 6 — Ops-зрелость.** CI (matrix по шаблонам), Prometheus+Sentry везде, реальные health-пробы, тесты в шаблонах.
- **Фаза 7 — Эффективность.** Per-stack step-budget, prompt-cache, progressive disclosure, per-stack skill-coverage + version-pin.
- **Фаза 8 — Новые форматы.** Android `.apk` (PWA→TWA), затем Capacitor; масштаб на node-pool за порогом ~50 юзеров.

**Нельзя резать:** Фаза 1 (sandbox), Фаза 2 (гейт-зубы), Фаза 3 (RLS) — это и есть граница «прототип → enterprise».

---

## Приложение — ключевые якоря (для Q&A)

- Агентный цикл: `agent_builder.py:206` (run), `:68-71` (tools), `:382` (green-gate), `:253` (эскалация); вызовы `messages.py:2576` (сегменты).
- Правки: `agent_builder.py:1159-1179` (byte-exact live) vs `file_extractor.py:957-1007` (forgiving, неподключён); auto-repair `messages.py:2764-2870`.
- `docs` баг: `agent_builder.py:68-70` (нет в allowlist) vs `:1127` (handler) vs `:802` (промпт). **Подтверждён.**
- Гейты: `messages.py:2996-3052` (functional FAIL только warning); `role_gate.py:10` / `security_gate.py:46` (мёртвый код); `acceptance.py:271-344` (реальный freeform-блок).
- Sandbox: `apps/orchestrator/.../core/config.py:45,50,62,78` (runtime/harden/egress/netns — небезопасный дефолт); `runtime.py:541` («outbound is open»).
- Стеки: `discovery.py:54-74` (роутер; нет `api`/`tgbot`); `stack_registry.py:56-65`; `docs/plans/phase7-multistack-provision.md`.
- Флаги: `config.py:805,817,852,866,842,765,246` (ON); `:273,297,427` (footguns OFF); коммит `e63cb5c` (24 флипа).
- Enterprise-ops: `wallet.py:45-56` (topup-эксплойт); `billing.py:17-56` (атомарный токен-биллинг); `nginx_writer.py:208-279` (ACME-TLS); `health.py:12` (заглушка).
- Контекст фаз: `docs/plans/MASTER-PLAN-autonomous-engineer.md`.
