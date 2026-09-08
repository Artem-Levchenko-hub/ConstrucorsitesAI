# START HERE — передача безопасного рефакторинга Omnia

## Актуально: Task 4, Task 5, Task 8 и исправляющий пакет доставлены

Task 4 web runtime: `10c4ef128006cfedc5e1a781b9dfaa8e1d94b77e`, pushed и
доставлен web-only, image `sha256:0b65efaeeada358406021164c68710d397e10063d4066d5abffa8b2922dabe70`.
`/web-health` подтверждает revision и ok; публичные страницы проходят smoke,
gate снят. На момент поставки Task 4 API/workers были `4960f6b9`, orchestrator `1011a0fd`; их процессы
и пять dirty серверных документов не менялись.

Новый HEAD в Edge: 2 HTTP/1 отмена → 1/0 на 1440 и 390 px. Исторический выбор,
terminal, пагинация, A→B→A и F5 сохранены; console errors/overflow нет.
Полный web на Node 20: 472 passed; CI `34262431711` web и production image-build
success. API job этого run ещё выполнялся при доставке (API-код не изменён).
Независимое source/helper review: No findings. Полные доказательства:
[Task 4 verification](2026-09-08-task4-history-verification.md).

**Task 8 доставлен:** общий SQL-блок подготовки Snapshot/Project/Run вынесен
в service с сохранением прежней транзакции. API/worker/generation-worker:
`e03793b4178a6b3b301bbf4db7cce3515ecadbb4`, image
`sha256:bc2526f3f95ee05a11daa4d16bd5e07d06bbb7e98def17166f6c1725dd9f5cfe`.
CI `34265225657` полностью success: 41 artifact check и 3170 API passed /
12 skipped / 8 xfailed. Ruff/mypy275 clean, independent review No findings.
Production health/release ok, write gate снят; web/orchestrator и прочие
consumers не перезапускались. Подробности:
[Task 8 verification](2026-09-08-task8-artifacts-verification.md).

**Task 9 подготовлен, ещё не доставлен:** locked cancellation helper перенесён
из router в существующий generation_runs service; worker больше не импортирует
эту private router функцию. 10 baseline cases прошли до/после, 8 DB cases
и endpoint/worker consumers ожидают CI. Продолжить с
[Task 9 verification](2026-09-08-task9-cancellation-verification.md), завершить
review/gates/API-only поставку до следующего пакета.

Task 6/7 отложены: план требует согласованного model smoke, а владелец запускает
генерации сам. Промпты не менялись. Task 3 числовой контракт не согласован.
Не повторять Task 4/5. H140 уже опубликован, public version 72, 136 прежних
записей сохранены; H141 опубликован: public version 73, 137 прежних записей сохранены; следующая H142. H134–H136 не публиковать целиком
вместе с репозиторным JSON.

### Предыдущая поставка Task 5

8 сентября выполнение возобновлено. Task 5 `777f3f61` и отдельное исправление
baseline `4960f6b99909e44efc421b6e2c99140d55cbd43b` отправлены и доставлены.
Полный CI `34258366570` success: API 3129 passed / 12 skipped / 8 xfailed;
orchestrator 1244 passed / 30 skipped / 15 xfailed плюс 9 реальных Docker-проверок.
Первый job orchestrator завис без вывода и был остановлен после успеха API;
отдельный повтор того же SHA прошёл. Причина зависания не установлена.

Production API/worker/generation-worker работают на `4960f6b9`, образ
`sha256:8a44eb141a52c21e9d9615a7991c8b21316a6b7c2e1609c717d04ab58658b670`.
API health/release и публичные SHA256 трёх kit-ресурсов проверены. Maintenance
gate снят. Orchestrator остаётся `1011a0fd`, web `179a3b3f`: их runtime-код
этим пакетом не менялся. Пять dirty secondbrain-документов сохранены побайтно.
Резервная копия и результат: `/opt/omnia-runtime/releases/startup-latency-4960f6b9`.

**Baseline Task 4:** доказано дублирование обновления истории. Временный
стенд `.artifacts/refactor-task4-20260908/README.md` содержит 7 baseline-тестов:
новый HEAD 2 запроса/1 отмена; две страницы и rollback 3/1; тот же HEAD 1/0;
отдельный terminal 1/0. Это вызовы API-функции и AbortSignal в тесте реальных
компонентов, не измерение серверных HTTP-запросов. Сохранять выбор версии,
все страницы, terminal reconciliation и защиту от поздних ответов A→B→A.
Новый regression suite: 13/13; итоговая поставка приведена выше. Исходные
измерения сохранены отдельно от результата оптимизации.

Task 3 числовая сериализация отложена: отдельного решения нет. Tasks 6/7 и 9–13 не
выполнены. Не объявлять весь план завершённым и не повторять Task 5. Полная
генерация пользователя не запускалась; live business-flow не заявляется.

Канонический checkout `C:/Users/Артём/ConstrucorsitesAI`, ветка
`codex/project-cell-cloud-20260902`, upstream `origin/main`. Свежесть проверять
перед продолжением. Runtime-доказательства: [Task 5](2026-09-08-task5-template-verification.md)
и [исправляющий пакет](2026-09-08-baseline-corrections.md). Правило публичного отчёта:
синхронизировать только H138/H139; не публиковать весь repo JSON с ранее
неопубликованными H134–H136.

<details>
<summary>Исторические точки остановки до этой доставки</summary>

## Продолжение 08.09: Task 5 подготовлен, доставка заблокирована

Код Task 5 отправлен в origin/main: `777f3f61ba377da9fb2eae0008f47f4f7faa0704`.
Локально прошли 147 проверок, Ruff/mypy, независимое ревью и установленный wheel.
В CI `34249743033` прошли обязательные API-регрессии, новые consumers, wheel и
обычная сборка Docker-образа с автономной проверкой всех шаблонов. Полная API-suite
выявила 14 ошибок; 13 воспроизведены также на исходном `810f0fbb`.

Среди прежних ошибок есть настоящий баг передачи вопросов онбординга клиенту и
недостающая проверка кабинета в offline harness. У владельца запрошено разрешение
на отдельный исправляющий пакет: утверждённый план не разрешает молча менять
поведение продукта или ослаблять проверки. Ответ пока не получен. Отдельно
ожидается решение о точности JSON-сумм для Task 3.

**Production не обновлён:** API/worker остаются на `179a3b3f`, orchestrator на
`1011a0fd`. Для Task 5 собран и проверен отдельный образ, контейнеры не
перезапускались. Следующий шаг — разрешить и исправить baseline failures,
получить зелёный полный gate, затем закончить доставку Task 5. Не повторять его
реализацию и не объявлять весь план выполненным.

Подробности: [Task 5: проверки и блокеры](2026-09-08-task5-template-verification.md).
Рабочий checkout: `C:/Users/Артём/ConstrucorsitesAI`, ветка
`codex/project-cell-cloud-20260902`, upstream `origin/main`. Более ранний снимок
передачи ниже сохранён для истории.

## Точка остановки

**8 сентября 2026: остановлено по просьбе владельца для передачи другому агенту. Только GPT-6 Astra.**

P01 (Task 2: проверка наличия PostgreSQL без выгрузки тома) реализован, проверен, отправлен в origin/main и доставлен в production. **Следующий пакет — Task 5: убрать дубли static-template ресурсов. Его реализация НЕ начата:** нет новой карты consumers, frozen golden fixtures, materializer или изменений шаблонов. После объявления следующего пакета выполнена только свежая проверка Git; затем владелец попросил остановиться и сохранить передачу.

Текущая поставка передачи содержит только документы и запись отчёта; runtime-код не меняется. Не повторять P01 и не считать весь рефакторинг завершённым. Новая задача/агент и фоновое продолжение не создавались.

## Что читать

1. Этот файл: точка остановки, актуальные ограничения, доставка.
2. [Полный утверждённый план: безопасный тотальный рефакторинг](2026-09-08-safe-total-refactoring-plan.md) — Tasks 0–13, границы R/O против B/N, 20 сценариев сохранения функциональности.
3. [P01: проверка и production-доставка](2026-09-08-p01-postgres-probe-verification.md) — факты, команды, CI, замеры и ограничения.

Все необходимые инструкции для продолжения сохранены в Git, а не только во внешней папке Codex.

## Дополнение владельца: целевое качество 10/10

Владелец отдельно попросил довести качество кода до 10/10, чтобы следующим агентам становилось проще работать. Продолжать по десяти критериям раздела 3 полного плана: ясная ответственность, именование, один владелец общей логики, явные зависимости/side effects, отсутствие лишних абстракций, осмысленные границы, обоснованная оптимизация, сохранённые тесты и безопасность, неизменность контрактов, краткая актуальная карта подсистемы.

Критерий результата — следующему агенту проще найти вход, понять контракт, локально изменить и доказать корректность. Не выдавать механическое сокращение строк за качество, не удалять возможности ради оценки. 10/10 — цель, текущая оценка не выставлена; недоступное независимое review и непроверенные сценарии записывать как gaps. Это дополнение не отменяет остановку для передачи: Task 5 остаётся следующим, ещё не начатым пакетом.

## Ревизии и окружение на момент подготовки передачи

| Объект | Проверенное состояние |
|---|---|
| Локальный checkout | C:/Users/79133/ConstrucorsitesAI-fresh |
| Локальная ветка / upstream | codex/data-versioning-20260908 / origin/main |
| BASE этой docs-поставки | 303274b29c377ce40a1d302eb4f686b5c102454a |
| После fetch перед документированием | local HEAD = upstream = server checkout = BASE; ahead/behind 0/0; локальное дерево чистое |
| Код и полный CI P01 | 1011a0fdf7cc4f636e7550937c0e89447fc21bcd; CI 34235756123 success |
| Отчёт P01 | 303274b2; diff runtime-кода относительно 1011a0fd отсутствует |
| Production checkout / ветка | /opt/omnia; codex/production-p01-20260908, upstream origin/main |
| Orchestrator runtime | host omnia-orchestrator.service; release SHA 1011a0fd |
| Остальные web/API/workers | релиз 179a3b3f; P01 их не пересобирал/не перезапускал |

Это снимок, а не разрешение считать следующий checkout свежим. Текущий docs-коммит определяется через git log: его SHA нельзя вписать в собственное содержимое. Перед новой работой повторить весь mandatory preflight из AGENTS.md, включая сервер.

На сервере сохранены пять unrelated tracked изменений:

- secondbrain/knowledge/concepts/daily-ingestion-process.md
- secondbrain/knowledge/concepts/proxyapi-anthropic-route.md
- secondbrain/knowledge/concepts/secondbrain-runtime.md
- secondbrain/knowledge/index.md
- secondbrain/knowledge/log.md

Также git status показывает untracked резервные env-файлы в apps/llm-gateway/deploy/full и apps/orchestrator, backups/, материалы secondbrain и файл с пробелами в имени. Не читать их содержимое ради рефакторинга, не добавлять в commit и не удалять. Это не часть нашей поставки. Проверять отсутствие пересечения целевых файлов перед каждым ff-only merge; при новом/непонятном пересечении остановиться.

## Что сделано и что осталось

| Пакет | Статус |
|---|---|
| Task 0 / подготовка | Выполнена для P01; повторять перед новой задачей |
| Task 1 / измерения | Узкая IO/metadata проверка P01; полноценная трасса всей генерации не получена |
| Task 2 / P01 | Доставлен, с явно записанным отсутствием отдельного независимого reviewer |
| Task 5 / static-template dedup | Следующий; код и golden ещё не начаты |
| Tasks 3–4, 6–13 | Не завершены этой сессией; не вычёркивать из программы |
| Новый адаптивный rollback / P12 старого аудита | Не часть текущего рефакторинга, требуется отдельное согласование |
| Общий production-smoke exact-SHA monitor | Предсуществующее расхождение, отдельная незавершённая задача |

Порядок Task 5 раньше Tasks 3–4 разрешён планом: это независимый R-пакет, направленный на уменьшение дублирующегося кода, а не изменение поведения.

## Первый конкретный шаг следующего агента: Task 5

После свежего preflight прочитать API guidance и составить read-only карту всех потребителей статических шаблонов. Начальные точки:

- apps/api/src/omnia_api/templates/{blank,landing,portfolio,blog}/assets/omnia-kit.css и omnia-kit.js;
- apps/api/src/omnia_api/services/repo.py;
- init/create/select mode, export, restore, managed asset injection;
- apps/api/Dockerfile, фактическое package-data/wheel inclusion, CI;
- apps/api/tests/test_select_mode.py и обнаруженные kit/template consumer tests.

Далее, строго последовательно:

1. На новом BASE установить, какие ресурсы действительно совпадают побайтово; не использовать старую оценку как delete list.
2. ДО рефакторинга сохранить независимый golden: каждый путь и SHA256 полного materialized дерева, включая dotfiles; отдельно file modes, если они значимы.
3. Написать characterization/regression tests реального init/export и collisions с пользовательскими файлами.
4. Оставить один поддерживаемый источник только доказанных дублей внутри API package; materializer создаёт самостоятельные файлы на прежних путях.
5. Не обновлять shared-копией уже созданные проекты; не менять старые snapshots, версии и данные.
6. Проверить empty/existing destinations, all consumers, экспорт и standalone результат. Никаких CDN/runtime symlinks.
7. Собрать реальный wheel/API image с обычным build context. Изолированные DB/Docker проверки обязательны там, где нужны; одних mocks недостаточно.
8. Полная подходящая suite, lint/typecheck, diff sanity, Astra review, отчёт, commit/push, документированная поставка и health. Только затем следующий пакет.

Предлагаемые файлы (ещё НЕ созданы): services/template_materialization.py, templates/shared-assets/, tests/test_template_materialization.py в API.

## Нерушимые границы

- Только GPT-6 Astra для реализации, проверки, review и доставки. Старое Sol/Terra/Luna распределение этим явно заменено; не запускать их автоматически.
- Сохранить весь текущий функционал, UX/дизайн, пользовательские тексты/языки, версионирование, историю, права и бизнес-данные.
- Сохранить даже действующие запреты: например, MAX restore 409 нельзя молча превратить в новый работающий rollback.
- Не удалять/перезаписывать live БД, snapshots, volumes; не выполнять destructive migrations, reset/stash/rebase/force-push или общий Docker prune.
- Не менять зависимости, схемы, billing, retry/terminal semantics и публикацию результата под видом сокращения кода.
- Не запускать пользовательскую генерацию самостоятельно: владелец решил запускать её сам.
- Не заявлять ускорение всей генерации или полное отсутствие ошибок по результату одного локального пакета.

## Проверки и ограничения среды

- P01: локально 1248 passed / 26 skipped / 15 xfailed; Linux CI 1244 passed / 30 skipped / 15 xfailed; отдельно 9/9 real-Docker cases. Это результаты P01, не тесты будущего Task 5.
- Локальный orchestrator использует Python 3.14.3 / Windows cp1251. Полный suite прошёл с -X utf8; шесть первоначальных nginx-fixture failures были связаны с кодировкой, их source не менялся.
- Mypy запускать из apps/orchestrator: запуск из root не читает нужный pyproject и даёт другие diagnostics.
- Локальный Docker daemon не работает: Desktop завершается с ошибкой отсутствующего registry key. Не ремонтировать Windows/registry попутно; P01 real-Docker проверен в изолированном Ubuntu GitHub Actions.
- API tests/conftest.py очищает тестовую схему. Никогда не передавать production DATABASE_URL / DATABASE_TEST_URL в тесты; PostgreSQL/Redis только disposable.
- Context7 и Sequential Thinking как инструменты в этой среде не найдены. Можно проверить доступность заново в новой среде; не имитировать вызовы и не откладывать работу бесконечно.
- Отдельный Astra reviewer не запустился из-за agent thread limit. Primary Astra проверил/доработал код, но это не независимое второе review. Лимит не обходился созданием пользовательской задачи.
- API/worker/web и orchestrator имеют разные deployment SHAs; до P01 global production-smoke ожидал устаревший bd3f499c268ff840634b0778143cc0ebcacd84e7. Не подменять проверку, не ослаблять smoke и не рестартовать всё ради метки.

## Доставка следующего runtime-пакета

Документированный SSH: i48ptgvnis@170.168.72.200. Серверный repo: /opt/omnia.

Production compose: apps/llm-gateway/deploy/full/docker-compose.yml, project full, контейнеры omnia-prod-*. НЕ infra/docker-compose.yml. Orchestrator отдельно: omnia-orchestrator.service, cwd /opt/omnia/apps/orchestrator, фактический env /opt/omnia/apps/orchestrator/.env.

В текущем checkout push выполнялся как git push origin HEAD:main — обычный fast-forward, без force и без переименования upstream. Сначала проверить configured upstream и свежесть заново. На сервере документированный git fetch + git merge --ff-only origin/main допустим только без пересечения сохраняемых изменений.

Перед runtime restart проверить aggregate active generation_runs, project_cell_operations и project_cell_activity_leases; не прерывать активную работу. API image могут использовать api/worker/generation-worker — установить фактических consumers, не ограничиваться прежним сокращённым списком.

После поставки проверить component release identity и relevant health; для docs-only передачи source checkout обновляется без runtime restart. Использовать health как доказательство доступности, не как доказательство успешной новой генерации.

Важный отчётный нюанс: публичный /otchet содержит только утверждённые записи. В репозитории H134–H136 были ранее не опубликованы; P01 публично добавил только H137. Не заменять весь /var/www/otchet/data.json репозиторной копией. Публиковать лишь новый согласованный фрагмент, сохраняя прочую историю и проверяя HTTP readback.

## Готовое поручение для новой задачи

> Продолжи безопасный тотальный рефакторинг Omnia исключительно на GPT-6 Astra. Прочитай docs/plans/2026-09-08-refactoring-handoff.md и полный docs/plans/2026-09-08-safe-total-refactoring-plan.md. Подтверди свежесть local/upstream/production. P01 уже доставлен — не переделывай его. Начни с Task 5: карта consumers и golden materialization, затем безопасная дедупликация static-template ресурсов. Сохрани 100% текущего функционала, версионирование, UX и бизнес-данные. Выполняй только разрешённые R/O-пакеты, по одному, с проверкой и полным циклом доставки. Пользовательскую генерацию не запускай. Фиксируй факты, оставшиеся риски и точную следующую точку в этих документах.

</details>
