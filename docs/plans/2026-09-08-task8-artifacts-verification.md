# Task 8 — общая подготовка версии внутри прежней транзакции

## Граница

Исходный checkout: `a6237ff5446797c7b821df661e092369187d47bb`.
Предыдущий API runtime: `4960f6b99909e44efc421b6e2c99140d55cbd43b`;
его API-код совпадает с исходным checkout. Web остаётся `10c4ef12`,
orchestrator `1011a0fd`.

Из двух путей `messages._process_prompt` выносится только одинаковая операция:
создать Snapshot, добавить/flush, найти Project и обновить current_snapshot_id,
найти GenerationRun и записать идентификатор версии, Git SHA и список файлов.
Helper не делает commit и не ловит ошибки. Возвращаются Snapshot и найденный
Project: последний используется caller после транзакции, поэтому терять эту
локальную ссылку или выполнять второй запрос нельзя.

У callers остаются Git commit/upload, явный `repo_svc.commit_files` test seam,
различающиеся exact_tree kwargs, выбор модели, запись Message, free quota,
commit/refresh, preview/events, attestation и repair. Новая идемпотентность,
атомарность Git+SQL и откат данных не добавляются.

## Исходный контракт

Новый `tests/test_generation_artifacts.py` исполняет реальные AST-участки
двух callers от Git commit до session.refresh, включая исходную функцию
расходования бесплатного лимита. Внешние модельные/runtime шаги не запускаются.
Это ограниченная проверка участка, **не полный успешный `_process_prompt`**.
Исходный код не копируется в тест: AST читается из текущего router, а ожидаемые
строки, файлы и последовательности заданы независимо.

До изменения production-кода: **31 passed**, 10 disposable DB cases исключены
локально; Python 3.12, 15.76 s, Ruff clean. Использованы реальный pygit2 и
изолированное in-memory MinIO; async session double имеет отдельную durable
копию, изменяемую только успешным commit.

Зафиксированы parent/prompt/model, Project/Message/Run связи и changed-files
provenance; Git→flush→связи→quota→commit→refresh; различия сообщения/токенов;
модели fallback/forced/orchestrated; optional rows; business quota и User
fallback; повторное выполнение; старое Git-дерево; exact_tree и пустые файлы.

Ошибки до commit не дают durable публикации. Ошибка refresh после commit
оставляет уже сохранённые данные. Повтор участка создаёт новую версию и ещё
раз расходует лимит, как прежде: идемпотентностью владеет внешний dispatch.
Ошибка SQL может оставить непривязанный Git artifact; автоматический откат Git
не заявляется. Комментарий про topmix в старом caller не совпадает с фактической
строкой модели «Оркестратор Sonnet+DeepSeek»; фактический контракт сохранён.

## Проверки перед поставкой

10 PostgreSQL-проверок используют существующий disposable `test_engine`:
оба пути с успешной публикацией/replay и ошибками Git/upload/flush/commit.
Состояние читается независимо в новых сессиях. Этот fixture очищает схему,
поэтому его запрещено направлять на production DB.

В CI добавлен ранний запуск этого файла на disposable PostgreSQL перед
существующим полным gate; прежние проверки не удаляются. Локально допускается
только `-k 'not disposable_db'` с заведомо недоступным loopback DB/Redis.

Исходное baseline review: No findings; замеченные пробелы exact_tree,
отсутствующие usage/business и post-commit refresh покрыты до extraction.
После extraction: те же **31 passed / 10 deselected**, включая точную identity
возвращённого Project и None при его отсутствии. Отдельно **31 passed** в
repo_limits/snapshot_restore/snapshot_preview_images/release_proof. Full Ruff
clean; mypy: **275 source files**, ошибок нет; diff check чистый.
Независимое итоговое review source/tests/CI/helper/docs: No findings.

Коммит `e03793b4178a6b3b301bbf4db7cce3515ecadbb4` отправлен в origin/main.
CI `34265225657`: все семь jobs success. Ранний PostgreSQL шаг: **41 passed**
(12.85 s), включая все 10 DB cases. Полный API: **3170 passed, 12 skipped,
8 xfailed** (666.61 s). Лог сохранён в `.artifacts/refactor-task8-20260908/ci-api.log`.

## Поставка

Production API, worker и generation-worker доставлены на `e03793b4178a6b3b301bbf4db7cce3515ecadbb4`.
Фактический image: `sha256:bc2526f3f95ee05a11daa4d16bd5e07d06bbb7e98def17166f6c1725dd9f5cfe`.
Именно этот собранный image прошёл 31 offline consumer test с отключённой сетью
и без production credentials перед переключением. Staging image имел другой
image ID и не использовался как подмена доказательства доставленного образа.

Перед обновлением выполнены read-only проверки отсутствия активных runs,
operations и leases; создан PostgreSQL backup. На время переключения включён
короткий write gate, после проверки он снят. Миграции, пользовательские
генерации и lifecycle Project Cell не запускались.

API health/release и точный image/release трёх consumers подтверждены.
Web `10c4ef128006cfedc5e1a781b9dfaa8e1d94b77e` и orchestrator
`1011a0fdf7cc4f636e7550937c0e89447fc21bcd` сохранили прежние процессы;
прочие compose consumers — прежние ID/image/start/env hash.
Публичные `/login`, `/max/register`, `/max/product`, `/max/guide`: 200;
`/max`: прежний 307 на регистрацию. Пять серверных dirty документов сохранены:
SHA256 diff `0faee2b7c90dfd954d0def658d80cd89f21312061c500b2d9efd6ae06f1d250d`.
Backup/result: `/opt/omnia-runtime/releases/task8-api-e03793b4`.

Это доказательства refactor-контракта и доступности компонентов. Новая полная
генерация и пользовательский business-flow не запускались. Предсуществующий
global smoke с единым устаревшим SHA не исправлялся; component SHAs проверены
отдельно. Для отчёта публикуется только H141, без неопубликованных H134–H136.

Task 6/7 пока отложены: согласованный модельный smoke несовместим с текущим
указанием не запускать пользовательские генерации самостоятельно. Промпты не
меняются. Task 3 также ожидает отдельного решения. Весь план не завершён.
