# Продолжение рефакторинга, 13 сентября 2026

Владелец поручил продолжить прежний план и убрать остаточные дубли.
BASE: `deddd35bd41f9f424b52226b3e8bde5e8423b770`; локальный checkout,
`origin/main` и production совпали. Пять сторонних серверных документов сохранены.

## Сверка плана

Ранее доставлены Task2/P01, Task4, Task5 и anime extension, Task8,
Task9 cancellation, Task10A/10B, Task11 transport/preview/message cache,
Task3 usage buckets. Их реализации присутствуют; повторное выполнение не нужно.
Доказательства и ограничения прежних поставок остаются в handoff и scope review.

Task3 SQL SUM не эквивалентен текущему последовательному сложению float.
Task6/7 меняют модельный ввод и требуют отдельной проверки результата генерации
владельцем. Task12 пока не имеет подтверждённой причины задержки: повторные
проверки после Docker effects нельзя удалять по одному внешнему сходству.
Task13 не закрывает всю матрицу пользовательских сценариев автоматически.
Упоминание MAX restore409 в старом плане историческое: последующие поставки
адаптивного восстановления определяют актуальное поведение, которое сохраняем.

## Пакет 1: неиспользуемый сборщик catalog

Тип R/Task13. Единственный изменённый production файл —
`apps/api/src/omnia_api/services/prompt_builder.py`.
Удалены `_CATALOG_SYSTEM_PROMPT`, `_build_catalog_system_prompt`,
`_build_catalog_messages` и устаревшее описание совместимого shim.
Действующий владелец catalog instructions — `services/lean_prompt.py`;
его вызывают `build_messages` и router messages. Новых слоёв нет.

Поиск всех tracked consumers нашёл только определения, внутренние вызовы
удалённого блока и исторические документы. Общие `_compute_skill_brief`,
`_format_selection_block` и `HISTORY_LIMIT` остались у прежнего владельца.
Рабочие инструкции, маршрутизация и пользовательские строки не менялись.
Net production source: **−95 строк**; tests/fixtures считаются отдельно.

До удаления заморожены SHA256 полных сериализованных payload реального
`build_messages`: catalog/freeform/plain/edit × шесть template IDs × RU/EN.
Текущий mixed-language запрос, история, выделение и память проекта входят в fixture.
Skill lookup отключён в fixture для детерминизма; это не модельная приёмка.
48 payload совпадают до/после; вместе со смежными suites **160 passed**.
Первая попытка baseline без обязательных test env дала четыре setup failures;
повтор с синтетическими DATABASE_URL/JWT_SECRET прошёл до изменения production.
Никаких подключений к production DB или модельных генераций не было.
Полные API Ruff и mypy (283 файла) прошли.

Команда focused gate из `apps/api`:

```sh
uv run --frozen python -X utf8 -m pytest -o addopts='' -q tests/test_prompt_routing_baseline.py tests/test_prompt_builder.py tests/test_lean_prompt.py tests/test_generation_mode.py tests/test_prompt_language.py
```

Независимое Astra review: No findings; отдельно подтверждено AST-равенство
оставшегося кода и всех 48 payload с исходным commit.
CI `34758726269`: все семь jobs success, полный API **3563 passed /12 skipped /8 xfailed**.
Тот же focused gate в собранном Linux image без сети: **160 passed**.
Поставлен `f43c7f227d1bb8d9fca50797aedabaee66f58c67`, image
`sha256:6315986fc756457ae9d3fdfff56456fb57033e7f2666edac50c764805d14be65`.
API/worker/generation-worker используют эту ревизию; web и orchestrator не
перезапускались. Public API6/6, web200, POST405, exact source/image и сохранение
пяти dirty документов подтверждены. H151 опубликован, public version83,
147 прежних записей сохранены, HTTP readback совпал.

Два сбоя проверок поставки сохранены в receipt: первая попытка читала краткий
`/health`, ожидая поля полного `/api/health`, поэтому вернула прежние образы.
После проверки старых образов/health восстановлены только два исходных nginx
vhost. Повторная поставка переключила runtime успешно; немедленная проверка
POST405 после reload nginx потребовала отдельного повторного чтения.
Скрипт исправлен: `/api/health`, предварительный health и bounded ожидание reload.
Финальное отдельное подтверждение всех условий записано в
`/opt/omnia-runtime/release-evidence/refactor-f43c7f227d1bb8d9fca50797aedabaee66f58c67-retry1/supplemental-verification.json`.
Ошибки скрипта не объявляются успешными запусками; успешность runtime подтверждена
отдельно. Изменения пользовательских данных для проверок не выполнялись.

Canonical Git scan `apps/{api,orchestrator,web}/src`, без tests/migrations,
py/ts/tsx/js/css: 642 файла, **7773024 → 7768391 bytes**, **161088 → 160993 строк**.
Групп полностью одинаковых файлов от512 bytes нет в обоих деревьях.
Это не доказательство отсутствия дублирования внутри функций.
Откат кода — обычный revert этого пакета; данные и схема не изменяются.

## Пакет 2: один блок подготовки legacy runtime

Task9, BASE `f43c7f227d1bb8d9fca50797aedabaee66f58c67`.
Два одинаковых блока в `_process_prompt` заменены локальным
`_provision_legacy_runtime_with_progress`. Порядок двух событий и provisioning,
внешние gates и установка deferred ready-флага после успеха сохранены.
Net production: **−24 строки /−1138 UTF-8 bytes**.

Новые 15 characterization cases вызывают настоящий `_process_prompt`,
`_record_agent_step` и `_prepare_max_runtime_context`; external IO заменён fakes,
socket connect запрещён. Покрыты оба пути, точные payload, ошибки каждого await,
deferred once/retry и blank/imported/empty-slug gates. BEFORE15passed;
AFTER со stack routing и тремя Cell negative cases: **83 passed /2 прежних xfail**.
Полные API Ruff/mypy283 прошли. Независимое Astra review: No findings; после
разворачивания helper AST совпадает с BASE, отдельно повторены BEFORE/AFTER15.
CI `34760360496`: все семь jobs success, API **3578 passed /12 skipped /8 xfailed**,
orchestrator **1641 passed /31 skipped /15 xfailed**. Изолированный API image:
**80 passed /2 xfailed**. Поставлен `0dacd38fe255258c2f2435f7a0b49232cfee4147`,
image `sha256:aeea5e394e9e9b77bc838356454c6f679059be6a5a535b9212147af175fc0d87`.
Deploy exit0; API/worker/generation-worker подтвердили exact revision/image,
public API6/6, web200 и POST405. Web/controller и пять серверных документов
сохранены. H152 опубликован: public version84, 148 прежних записей сохранены.
Receipt: `/opt/omnia-runtime/release-evidence/refactor-0dacd38fe255258c2f2435f7a0b49232cfee4147/supplemental-verification.json`.

## Пакет 3: один источник публичных JS четырёх шаблонов

Task5 extension, BASE `0dacd38fe255258c2f2435f7a0b49232cfee4147`.
`omnia-inspector.js`, `omnia-brief-narration.js`, `omnia-remix-cta.js` хранятся
в одном `templates/shared-public` с data-only manifest для MAX, entities,
postgres-drizzle и realtime. Убраны девять лишних копий. Net production,
включая helper/integrations/scripts, без tests/CI/docs: **−207366 bytes /−4903 строки**.

Штатный CLI `python3 scripts/materialize-template.py TEMPLATE DESTINATION`
создаёт самостоятельное дерево в новом/пустом каталоге. Изменены все выявленные
seed/build/export consumers: provisioning, image freshness/build, production
builder, Project Cell pristine comparison, boundary QA reader, API export,
bulk/smoke scripts. Пользовательский overlay остаётся поверх шаблона; missing-only
seed сохраняет существующие, включая пустые, файлы. API читает только общие данные,
не импортирует orchestrator. Dockerfiles и managed MAX kit/security не менялись.
Сырые source-каталоги теперь требуют материализации; прямые рецепты обновлены.

Полные 309 tracked path/hash/mode записей четырёх исходных деревьев заморожены
до изменений. Исключения — только три явно перечисленных README с новыми рецептами;
восемь Dockerfiles и остальные runtime bytes/modes должны совпадать.
Локальный gate: **112 orchestrator +48 API passed**; три DB-dependent API cases
не запускались локально и входят в полный CI. Ruff, mypy API283/orchestrator87,
shell syntax и diff check прошли. Проверены реальные callers и ошибки,
конкурентная пересборка, время жизни build context при cancellation, пользовательские
overlays, Project Cell seed/pristine и реальные участки shell commands с fake Docker.

Браузер BEFORE/AFTER: четыре шаблона × desktop1440/mobile390, **96 групп проверок**
и **232 локальных HTTP-запроса** на каждый прогон, ошибок нет. Совпали selection
payload, postMessage/origin checks, narration/replay, Remix navigation и геометрия.
Все 12 AFTER physical files совпали с frozen Windows CRLF golden; Git LF baseline
отличается только окончаниями строк. Файлы не нормализовались для сокрытия разницы.
Это поведение JS в локальном harness, не бизнес-приёмка сгенерированного приложения.

BEFORE Docker: восемь dev/prod images, **24 HTTP asset hash checks** прошли,
live template tags не изменились. Первая попытка QA не дошла до сборки из-за
неподдерживаемого `tarfile` API старого серверного Python; исправлен QA script.
Основной повтор прошёл семь образов, но пропустил штатный шаг создания `drizzle`
в prod context. После добавления того же шага, который уже есть в builder,
отдельный drizzle-prod retry прошёл. Исходные failed receipts сохранены;
Dockerfiles и product code для исправления QA не менялись.

Независимое review обнаружило пропущенные readers Project Cell/boundary QA и
рецепты ещё на этапе проектирования; они включены до удаления копий. В review
реализации исправлена отсутствующая установка зависимостей в realtime README,
после чего пять затронутых golden/CLI tests прошли. Исправлена также новая CI
проверка экспорта: её ожидания независимо фиксируют восемь прежних исключений
`.omnia`, а не меняют API ради полного orchestrator tree. Семь export tests,
включая реальный smoke script вне checkout на чистом временном mounted tree,
прошли. Независимое финальное Astra review: **No findings**; отдельно проверены
12 исходных JS, 309 hashes/modes, восемь Dockerfiles и 301 экспортируемый файл.
Полный CI, Linux image/mounted-export smoke, AFTER Docker и production delivery
пока ожидаются.

Первый CI `34762926416` выявил ещё одну устаревшую предпосылку registry test:
все каталоги `templates` считались стеками. Data-only `shared-public` теперь
явно исключён, строгая проверка всех остальных каталогов сохранена. До исправления
orchestrator: **1 failed /1665 passed /31 skipped /15 xfailed**; после локально
все **28 registry tests passed**. Production source этим исправлением не меняется.
Готовый API image `sha256:1b9d83b6ed00d932ffa7d842fbf43df1552a3a852a1135e8f6a483285caee97b`
успешно проверил mounted export четырёх шаблонов вне checkout без сети.
Отдельный canonical Git/LF browser AFTER также подтвердил все 12 SHA и 96 групп
поведения: Windows archive запускался с `-c core.autocrlf=false`, без изменения
конфигурации Git или исходных файлов. Поставка ждёт зелёного CI исправленной ревизии.

## Следующие подтверждённые кандидаты

1. Task9: три одинаковых чтения MAX config и рендера starter files в `messages.py`.
   Общий helper должен заново читать config при каждом вызове; rollback и ошибки
   остаются у callers. Кэширование не входит в пакет.
2. Task13: четыре одинаковых raw snapshot mapping. Общий конкретный owner в
   `schemas/snapshot.py`; отдельный event JSON с `.isoformat()` не объединять.
3. Task13: два одинаковых pass usage helper; общий owner `llm_client.py`.
   Отличающийся подсчёт passes в multipass остаётся отдельным.

Объединение exception cleanup в orchestrator даёт лишь шесть строк сокращения;
дополнительная поставка сейчас не приоритетнее двух кандидатов API.
