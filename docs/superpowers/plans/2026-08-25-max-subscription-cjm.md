# Omnia MAX Subscription CJM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать автономный интерактивный HTML, который в формате исходного CJM проводит владельца бизнеса по быстрому понятному пути к Free, Pro или Business, заключает договор до первой реальной генерации и показывает управление выбранным тарифом.

**Architecture:** Новый файл создаётся рядом с оригиналом, чтобы исходный технический CJM оставался доступен для сравнения. Один HTML содержит CSS, демонстрационные данные, конечный автомат из десяти пользовательских точек и рендереры экранов; никакие данные не отправляются наружу. Состояние выбора тарифа, согласий, ошибки и прогресса хранится только в памяти страницы и может быть сброшено.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, локальный браузер, PowerShell-проверки, Playwright/in-app Browser для визуального и интерактивного smoke-теста.

**Spec:** `docs/superpowers/specs/2026-08-25-max-subscription-cjm-design.md`

## Global Constraints

- Итоговый файл: `C:\Users\79133\Downloads\omnia-max-cjm-client.html`; оригинал `C:\Users\79133\Downloads\omnia-max-cjm.html` не изменяется.
- Ровно 10 точек контакта и 5 фаз: «Знакомство», «Задача и владелец», «Тариф и договор», «Создание», «Запуск и управление».
- Free — 0 ₽, без карты и автосписаний; Pro — 1 490 ₽ за месяц; Business — 4 990 ₽ за месяц; дата снимка 25.08.2026 видна.
- Договорный шлюз предшествует первой генерации во всех трёх ветках.
- Акцепт оферты, согласие на персональные данные, реклама и автопродление представлены раздельно; реклама и автопродление изначально выключены.
- В пользовательском слое запрещены маршруты, компоненты, исходный код, API, backend, LLM, БД, серверы, деплой и внутренние статусы готовности.
- HTML не выполняет регистрацию, оплату, генерацию, сетевые запросы или заключение договора и явно помечен как демонстрация пути.
- На каждом шаге есть одно основное действие, понятный результат и восстановимая ошибка без потери брифа.
- Проверяемые размеры: 1440×900, 1024×768 и 390×844; на 390 px отсутствует горизонтальное переполнение.

---

### Task 1: Автономный каркас и карта пути

**Files:**
- Create: `C:\Users\79133\Downloads\omnia-max-cjm-client.html`
- Reference: `C:\Users\79133\Downloads\omnia-max-cjm.html`
- Reference: `docs/superpowers/specs/2026-08-25-max-subscription-cjm-design.md`

**Interfaces:**
- Consumes: утверждённые десять точек, тексты тарифов и юридические правила из спецификации.
- Produces: `TOUCHPOINTS`, `PHASES`, `PLANS`, `createInitialState()`, `renderApp(state)` и DOM-контейнер `#omnia-cjm-app` для следующих задач.

- [ ] **Step 1: Зафиксировать статические проверки до создания файла**

```powershell
$target = 'C:\Users\79133\Downloads\omnia-max-cjm-client.html'
if (Test-Path -LiteralPath $target) { throw 'Target must not exist before RED check' }
```

Expected: команда завершается ошибкой только если ранее созданный результат требует осознанного обновления; в чистом прогоне файл отсутствует.

- [ ] **Step 2: Создать один автономный документ**

В `<head>` определить светлую/тёмную палитру, адаптивную сетку, видимый `:focus-visible`, reduced-motion и стили карты, демонстрационного экрана, поясняющей панели и нижней навигации. В `<body>` создать только `#omnia-cjm-app`, а в `<script>` — неизменяемые справочники:

```js
const PHASES = [
  'Знакомство',
  'Задача и владелец',
  'Тариф и договор',
  'Создание',
  'Запуск и управление',
];

const PLANS = {
  free: { name: 'Free', price: 0, period: 'без платного периода' },
  pro: { name: 'Pro', price: 1490, period: '1 месяц' },
  business: { name: 'Business', price: 4990, period: '1 месяц' },
};
```

`TOUCHPOINTS` должен содержать ровно десять объектов с полями `id`, `phase`, `title`, `goal`, `promise`, `result`, `recovery`, `emotion`.

- [ ] **Step 3: Реализовать обзор и переход к точке**

`renderOverview(state)` рисует пять фаз, десять кликабельных карточек, показатели `10 / 5 / 3 / 1 / 0` и кнопку «Пройти весь путь». `openTouchpoint(index)` принимает целое `0..9`, меняет `state.currentIndex` и вызывает `renderApp(state)`.

- [ ] **Step 4: Выполнить статический smoke-тест**

```powershell
$html = Get-Content -Raw 'C:\Users\79133\Downloads\omnia-max-cjm-client.html'
if (($html | Select-String -AllMatches 'id:\s*''step-[0-9]+''').Matches.Count -ne 10) { throw 'Expected 10 touchpoints' }
if (($html | Select-String -AllMatches 'phase:\s*''[^'']+''').Matches.Count -ne 10) { throw 'Every touchpoint needs a phase' }
foreach ($needle in @('Free','Pro','Business','Демонстрация клиентского пути')) {
  if (-not $html.Contains($needle)) { throw "Missing $needle" }
}
```

Expected: PASS без вывода исключений.

### Task 2: Настоящий выбор тарифа и договорный шлюз

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-client.html`

**Interfaces:**
- Consumes: `PLANS`, `state.plan`, `state.consents`, `state.contractAccepted`, `openTouchpoint(index)`.
- Produces: `selectPlan(planId)`, `canAcceptContract(state)`, `acceptContract()`, `activatePlan()`, `renderPlanStep(state)`, `renderContractStep(state)`, `renderActivationStep(state)`.

- [ ] **Step 1: Добавить тестируемые инварианты в код страницы**

```js
function canAcceptContract(state) {
  return Boolean(state.plan && state.consents.offer && state.consents.personalData);
}

function nextCharge(state) {
  if (!state.plan || state.plan === 'free' || !state.consents.autoRenew) return null;
  return PLANS[state.plan].price;
}
```

- [ ] **Step 2: Реализовать экран сравнения**

Три равноправные карточки показывают цену, период, кому подходит, включённые возможности и ограничения. Рекомендация «Подходит по вашему брифу» объясняется размером команды и планом публикации; ни один тариф не отмечен заранее. После выбора фиксированная итоговая панель показывает «Сегодня», «Следующее списание» и кнопку продолжения.

- [ ] **Step 3: Реализовать договорный экран**

Резюме показывает заказчика, исполнителя как демонстрационные реквизиты из действующей оферты, тариф, сумму сегодня, период, состояние продления, отмену и права на материалы/результат. Четыре независимых элемента управления:

```js
state.consents = {
  offer: false,
  personalData: false,
  marketing: false,
  autoRenew: false,
};
```

`marketing` и `autoRenew` не влияют на `canAcceptContract`. `autoRenew` скрыт для Free. Изменение тарифа сбрасывает `offer`, `autoRenew` и `contractAccepted`, чтобы старая воля не переносилась на новые условия.

- [ ] **Step 4: Реализовать Free и платную активацию**

Free подтверждает «0 ₽, карта не нужна, автосписаний нет» и ведёт к генерации. Pro/Business показывают точную сумму, период и выбранное состояние продления; ветка «Оплата не завершена» предлагает «Попробовать снова» или «Выбрать Free», не стирая бриф.

- [ ] **Step 5: Проверить договорные инварианты**

```powershell
$html = Get-Content -Raw 'C:\Users\79133\Downloads\omnia-max-cjm-client.html'
foreach ($needle in @(
  'canAcceptContract',
  'offer: false',
  'personalData: false',
  'marketing: false',
  'autoRenew: false',
  'Карта не нужна',
  'Следующее списание',
  'Принять условия и начать Free',
  'Принять условия и перейти к оплате'
)) { if (-not $html.Contains($needle)) { throw "Missing invariant: $needle" } }
```

Expected: PASS без вывода исключений.

### Task 3: Быстрый путь генерации, результат и управление подпиской

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-client.html`

**Interfaces:**
- Consumes: `state.contractAccepted`, `state.activationComplete`, `state.generationStage`, `state.failureMode`.
- Produces: `renderGenerationStep(state)`, `advanceGeneration()`, `renderReviewStep(state)`, `renderManagementStep(state)`, `setFailureMode(mode)`, `resetDemo()`.

- [ ] **Step 1: Блокировать генерацию до договора и активации**

```js
function canStartGeneration(state) {
  return state.contractAccepted && state.activationComplete;
}
```

Попытка открыть шаг 8 напрямую показывает краткое резюме незавершённых действий и кнопку возврата к первому обязательному шагу.

- [ ] **Step 2: Реализовать понятную генерацию**

Показывать только четыре стадии: «Понимаем задачу», «Собираем основные экраны», «Проверяем ключевой сценарий», «Готовим результат к просмотру». Кнопка «Продолжить создание» двигает стадию вручную, а «Показать готовый результат» завершает демонстрацию без ожидания таймеров.

- [ ] **Step 3: Реализовать результат и запуск**

Экран результата содержит живой мобильный макет проекта и три действия: «Попросить правку», «Сохранить версию», «Подготовить запуск». Перед запуском показывается человеческий чек-лист реквизитов, цен, контактов, прав на материалы и данных будущих клиентов.

- [ ] **Step 4: Реализовать управление тарифом**

Экран показывает тариф, период, следующую дату/сумму либо «не будет», историю подтверждений и чек. Отдельные кнопки выключают автопродление, запрещают использование сохранённого способа оплаты и открывают запрос прекращения/возврата; каждое действие даёт подтверждение в `aria-live`.

- [ ] **Step 5: Реализовать аварийные ветки и восстановление**

`failureMode` принимает `none`, `email`, `owner`, `offer`, `consent`, `payment`, `generation`, `revoked`. Панель «Если не получилось» объясняет одну ошибку и одно следующее действие. `resetDemo()` возвращает новый `createInitialState()`.

- [ ] **Step 6: Проверить отсутствие технического слоя**

```powershell
$html = Get-Content -Raw 'C:\Users\79133\Downloads\omnia-max-cjm-client.html'
$forbidden = @('PROJECT_SOURCE_FILES','backstage:','implemented','partial','health-check','snapshot','webhook','VPS','API/LLM/БД')
foreach ($needle in $forbidden) {
  if ($html.Contains($needle)) { throw "Forbidden customer-facing technical marker: $needle" }
}
```

Expected: PASS без вывода исключений.

### Task 4: Доступность, браузерная проверка и отчёт

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-client.html`
- Modify: `otchet/data.json`

**Interfaces:**
- Consumes: законченный автономный HTML и гипотезу `H116`.
- Produces: проверенный CJM, обновлённые evidence/status/score/impact `H116`, актуальный шаг `V8` и завершённый delivery report.

- [ ] **Step 1: Проверить синтаксис и автономность**

Извлечь встроенный JavaScript между `<script>` и `</script>` и проверить `new Function(script)` в Node. Проверить, что `http://`, `https://`, `<script src=`, `<link rel="stylesheet"` отсутствуют вне явных текстовых юридических ссылок; юридические ссылки должны открываться только по действию пользователя, а интерфейс не должен выполнять fetch/XHR.

- [ ] **Step 2: Пройти интерактивный smoke-тест**

В браузере открыть локальный файл и проверить три пути:

1. Free: бриф → владелец → Free → обязательные согласия → активация → генерация → результат.
2. Pro: Pro → без автопродления → оплата → генерация → управление.
3. Business: Business → отдельное автопродление → оплата → отключение продления → отзыв способа оплаты.

Дополнительно вызвать ошибку оплаты и ошибку генерации, затем вернуться в happy path без повторного заполнения брифа.

- [ ] **Step 3: Проверить адаптивность и клавиатуру**

На 1440×900, 1024×768 и 390×844 проверить обзор, тарифы, договор, оплату, генерацию и управление. Для каждой ширины убедиться, что `document.documentElement.scrollWidth <= document.documentElement.clientWidth`. Tab проходит интерактивные элементы в визуальном порядке, Escape закрывает диалог, выбранный тариф различим текстом и иконкой.

- [ ] **Step 4: Обновить живой отчёт**

В `otchet/data.json` перевести `H116` из `open` в `testing` после локального браузерного прогона, заполнить evidence точными проверенными состояниями и выставить score только по фактам. Переключить шаг V8 «Клиентский CJM подписки...» в `true` только если все критерии HTML выполнены; поднять `meta.updated` и `meta.version`.

- [ ] **Step 5: Проверить итоговый diff и доставить**

```powershell
Get-Content -Raw otchet/data.json | ConvertFrom-Json | Out-Null
git diff --check
git status --short --branch
```

Закоммитить только план и `otchet/data.json`; внешний HTML приложить пользователю отдельной абсолютной ссылкой. Push выполнить в `origin/main`, затем обновить публичный `/otchet` без runtime-compose и подтвердить HTTP 200. Если GitHub снова отклонит push, остановить доставку и сообщить точную ошибку, не называя задачу опубликованной.

## Self-review

- Spec coverage: все 10 точек, 5 фаз, три тарифа, договор до генерации, раздельные согласия, ошибки, управление, юридические ограничения и визуальные размеры привязаны к конкретным задачам.
- Placeholder scan: план не содержит незаполненных маркеров или неопределённых обработчиков.
- Type consistency: `state.plan`, `state.consents`, `state.contractAccepted`, `state.activationComplete`, `state.generationStage`, `state.failureMode` и функции переходов имеют одинаковые имена во всех задачах.
- Scope: оригинальный HTML сохраняется; production-приложение, реальные платежи и юридические документы не изменяются.
