# V1.2 — Mobbin enterprise-паттерны → правила генератора

> **Что это.** Делверибл задачи **V1.2** (§5★ ВИЗИОНЕРСКИЙ ROADMAP, столп 1 «WOW-дизайн с 1 генерации»).
> Источник вкуса — top enterprise-паттерны с **Mobbin** (web), вытащенные через MCP в headless-сессии.
> Каждый паттерн распакован как **переиспользуемое ПРАВИЛО генератора** (не разовая копия — память `omnia_reference_to_rule`),
> с пометкой **где живёт правило**: `бриф` = текст в `prompt_builder._ENTITIES_UI` (model-guidance) ИЛИ
> `kit` = детерминированный примитив в `apps/orchestrator/templates/nextjs-entities` (model-independent, предпочтительнее).
> Каждый — со **статусом** (`есть` уже в ките / `net-new` отсутствует) и приоритетом для **V1.3** (вшивать по одному за тик).
>
> Сгенерено 2026-06-12. Цитаты `mobbin_url` кликабельны. Платформа везде web.

---

## Таблица «приём → правило»

| # | Паттерн | Класс-референс (Mobbin) | Что именно делает референс (приём) | Правило генератора | Где | Статус |
|---|---------|-------------------------|-------------------------------------|---------------------|-----|--------|
| 1 | **App-shell: левый nav-rail с группами секций** | Langdock, Cloudflare, Vapi, Vanta | Узкий левый рельс: бренд сверху, разделы СГРУППИРОВАНЫ заголовками (OVERVIEW / COMPLIANCE / TRUST), активный пункт подсвечен полосой, иконка+лейбл, аватар+план внизу | `AppShell` + сгруппированный `nav` (секции-заголовки, не плоский список при >5 пунктов); активный пункт = акцент-полоса; футер рельса = аватар+роль+план | kit (`AppShell`) + бриф | **есть** (рельс), nav-группы = усилить |
| 2 | **Dashboard: ряд KPI + график ниже** | Langdock, Quicken | KPI-карточки в один ряд (All-time users / conversations / messages) → bar/area-chart на всю ширину ниже | ряд `StatCard` (1 с `accent`) → `TrendArea`/`BarMini` под ними; дашборд с динамикой обязан показать ≥1 график | kit + бриф | **есть** |
| 3 | **Data-table: filter-tabs + удаляемые active-filter-chips + Filter/Sort** | Squarespace Orders, Relevance AI | Над таблицей: сегменты «All / Pending / Fulfilled / Canceled», ПЛЮС применённые фильтры висят **чипами с ×** (снять по одному), кнопки Filter / Sort-by, «N per page» | filter-tabs `есть`; **NET-NEW**: применённые фильтры рендерятся removable-чипами над таблицей (`activeFilters` → chip с onRemove), «Сбросить всё» | kit (`DataTable`/`CrudResource`) | **net-new** (active-chips) |
| 4 | **Settings / Account: суб-навигация + сгруппированные секции + явный «Сохранить»** | Time2book, Grammarly, 7shifts, Oyster | Отдельный раздел настроек: **суб-сайдбар или табы** (Account / Payments / Policies / Team / Integrations | General / Documents / Notifications / Permissions); форма = строки «лейбл+подсказка слева / инпут справа» ИЛИ карточки-группы (Profile photo, Login & Security); **явная кнопка «Save changes» снизу-справа**; «Delete account» изолирован внизу | **NET-NEW kit-примитивы**: `SettingsShell` (суб-nav) + `SettingsSection` (карточка-группа) + `FieldRow` (лейбл+helper+инпут); бриф: каждый апп с профилем юзера обязан иметь `/dashboard/settings` с группами Профиль / Безопасность / (Биллинг) / (Команда); явный submit, не auto-save-двусмысленность; destructive-зона внизу | kit + бриф | **net-new** ⭐ |
| 5 | **Setup-guide checklist (геймификация онбординга)** | Vanta «Starter Guide 6/20 tasks», Hootsuite «Getting Started 4/6 Explored», HoneyBook «Check off the steps to cha-ching» | Карточка на дашборде: прогресс-бар/кольцо «N/M выполнено», список шагов с галочками (done/pending), у каждого шага свой CTA-линк («Go to policy» / «Add bank info»), сворачивается, отдельная панель «Starter Guide Progress» с кольцами | **NET-NEW kit-примитив** `SetupChecklist` (steps[]: label, done, href/onClick) + прогресс «N/M» + кольцо; бриф: новый апп с пустыми данными показывает на дашборде чек-лист первых шагов (создать первую запись / заполнить профиль / пригласить), каждый шаг ведёт к действию. **Прямой удар по North-Star столпу «геймификация»** | kit + бриф | **net-new** ⭐ |
| 6 | **Empty-state: иллюстрация + тёплый домен-копирайт + ОДИН primary-CTA** | Klarna «Nothing saved → Find products», Quicken «No holdings → CONNECT ACCOUNT», HoneyBook «Enjoy your day! → ADD A NEW TASK», Typeform «No brand kits here yet → + New brand kit» | По центру: иллюстрация, **тёплый конкретный заголовок** (не «No data»), 1 строка пользы, **ровно одна primary-кнопка, создающая первую запись** | `EmptyState` уже есть; **усилить правилом**: copy = доменный и тёплый («Скоро здесь появятся товары», не «Пусто»), `action` ОБЯЗАН вести к созданию первой записи (не декоративный текст); пустой список ≠ вечные skeletons | бриф (+`EmptyState` есть) | **есть**, копи-правило net-new |
| 7 | **Онбординг chip-quiz «Question N of 6»** | Intercom (flow), Causal, Outseta | Модалка «Question 4 of 6»: вопрос + **чипы-варианты** («0–250 / 250–1,000 / … / Not sure») + Continue/Skip, прогресс-счётчик; welcome-модалка до квиза | эталон столпа 2 (живой онбординг-попап). Сверить с `DiscoveryChips`: счётчик прогресса «Вопрос N из M», «Не уверен»/«Другое»-чип, Skip; → задача **V2.1** | бриф/фронт (`DiscoveryChips`) | **есть** частично → V2 |
| 8 | **Command palette / глобальный поиск (⌘K)** | Linear, Vapi, Fey | Оверлей по ⌘K: «Type a command or search…», секции Actions / Recent / All Pages, строки с иконкой+правым шорткатом, навигация стрелками, «N results» | **NET-NEW kit-примитив** `CommandPalette` (⌘K): индексирует nav-маршруты + «Создать <сущность>» экшены; даёт enterprise-ощущение и скорость | kit | **net-new** |
| 9 | **Карточка записи (detail) по клику на строку** | (общий enterprise-паттерн; покрыт `CrudResource onRowClick`) | Клик по строке → детальная карточка/слайд-овер со всеми полями + «Изменить»/«Удалить» | `CrudResource` сам открывает карточку записи | kit | **есть** |
| 10 | **Плотность таблицы + показ/скрытие колонок + CSV-экспорт** | Squarespace, Notion/Linear/Airtable (классы) | Оператор уплотняет строки, прячет лишние колонки, выгружает CSV | `CrudResource`: `densityToggle` / `columnToggle` / кнопка «Экспорт» — все встроены | kit | **есть** |
| 11 | **Профиль-форма: двухколоночные ряды + карточки-группы + загрузка фото** | 7shifts «My Account», Oyster «Edit Company Information» | Поля в 2 колонки (First/Last name рядом), сгруппированы карточками (Profile photo + Upload, Login & Security, Emergency Contact); upload-picture с превью-аватаром | усиливает #4: `FieldRow`/`FieldGrid` (2-col на ≥sm), `kind:"image"` уже грузит через uploadFile; группировка карточками | kit (часть #4) | **net-new** (с #4) |
| 12 | **Trial/план-баннер + «N days left» + Upgrade** | Time2book «Free trial 8 days left / Upgrade», 7shifts «11 days left… View Plans», Vanta «Finish Starter Guide» | В футере рельса/топбаре: статус триала «N days left», прогресс, кнопка Upgrade/View Plans | бриф: если у аппа есть тарифы — статус-капсула плана в `AppShell` футере (для MVP — заглушка-капсула, не мёртвая кнопка) | бриф | **net-new** (низкий приоритет, MVP без оплаты) |

---

## Цитаты (mobbin_url)

- App-shell / dashboard: [Langdock](https://mobbin.com/screens/) · [Cloudflare] · [Vapi](https://mobbin.com/screens/593d7acd-2e16-4365-bcd6-02ce52f48f3b) · [Vanta](https://mobbin.com/screens/e2a14a43-34bd-4f02-a538-b83b83236dd1)
- Data-table: [Squarespace Orders] · [Relevance AI]
- Settings: [Time2book](https://mobbin.com/screens/edcc5782-4ee3-454d-80c3-7e946c5677ac) · [Grammarly](https://mobbin.com/screens/3457012c-4ff7-4ce6-b28c-9c6e4d94f10b) · [Oyster](https://mobbin.com/screens/4750709c-7207-4536-8fab-780b108ad5e9) · [7shifts](https://mobbin.com/screens/a4ee8811-ee9e-46a8-bbf7-f8172cac66a9)
- Setup-checklist: [Vanta Starter Guide](https://mobbin.com/screens/e2a14a43-34bd-4f02-a538-b83b83236dd1) · [Hootsuite Getting Started](https://mobbin.com/screens/678aa914-5611-432d-ba41-f01049932d28) · [HoneyBook steps](https://mobbin.com/screens/aeec59e0-46be-4928-a667-91c8ccd59d08)
- Empty-state: [Klarna](https://mobbin.com/screens/ab5556c1-f9bd-4e69-92fb-2c9e0b2edda3) · [Quicken](https://mobbin.com/screens/b8661d09-4bc5-42de-a026-f3d227fd10c9) · [HoneyBook](https://mobbin.com/screens/47f71d9f-0d87-4e19-a8b6-3669095e0cbe) · [Typeform](https://mobbin.com/screens/648d4700-8592-4be4-8b65-5931ac44fd17)
- Onboarding chip-quiz: [Intercom flow] · Causal · Outseta
- Command palette: [Linear](https://mobbin.com/screens/8a6d227b-63e6-483c-925f-d256d0989a10) · [Vapi](https://mobbin.com/screens/593d7acd-2e16-4365-bcd6-02ce52f48f3b) · [Fey](https://mobbin.com/screens/ff52ac90-4d18-4765-98da-df1e362a5ee1)

---

## Очередь V1.3 (вшивать по одному за тик, приоритет сверху)

Приоритет = (net-new) × (удар по North-Star) × (model-independent kit-примитив предпочтительнее брифа).

1. ~~**Settings / Account раздел** (#4 + #11)~~ ✅ **ЗАШИТО V1.3 slice 1/6 (2026-06-12, commit `04fb7b0`)** — `settings.tsx`: SettingsShell+SettingsSection+FieldRow+FieldGrid+DangerZone, экспорт из `omnia/index.ts`, бриф «▸ НАСТРОЙКИ / АККАУНТ» в `_ENTITIES_UI`. Браузер-E2E зелёный (модель собрала `/dashboard/settings` на ките, desktop+mobile, 0 ошибок). Остаточный нудж: бриф должен требовать кнопку variant=destructive внутри DangerZone (модель её опустила).
2. ~~**Setup-guide checklist** (#5)~~ ✅ **ЗАШИТО V1.3 slice 2/6 (2026-06-12, commit `14712ce`)** — `setup-checklist.tsx`: `SetupChecklist` (ProgressRing «N/M» + done/pending шаги + per-step CTA + reward-state), экспорт из барреля, бриф «▸ ОНБОРДИНГ-ЧЕК-ЛИСТ» в `_ENTITIES_UI`. Браузер-E2E зелёный: модель сама собрала CRM-дашборд с чек-листом 4 шагов из брифа, CTA рабочие, desktop+mobile, 0 ошибок.
3. **Active removable filter-chips** (#3) — над `DataTable`/`CrudResource` применённые фильтры висят чипами с × + «Сбросить всё». Дополняет уже зашитые filter-tabs; чистый kit.
4. **Command palette ⌘K** (#8) — `CommandPalette` индексирует nav-маршруты + «Создать <сущность>». Enterprise-скорость и ощущение; средняя сложность.
5. **Empty-state copy + CTA-wiring** (#6) — усиление брифа: тёплый доменный заголовок + обязательный primary-CTA создающий первую запись. Дёшево, высокий ROI на «0 тупиков».
6. **Trial/план-капсула** (#12) — низкий приоритет (MVP без оплаты), только не-мёртвая заглушка.

> Паттерны #1/#2/#9/#10 уже в ките — на V1.3 только лёгкое усиление брифом (nav-группы при >5 пунктов), не новый код.
