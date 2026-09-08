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
Итоговые CI/review и runtime-доказательства дописываются после проверки.
На момент этой записи PostgreSQL cases ещё не запускались, пакет не доставлен.

Task 6/7 пока отложены: согласованный модельный smoke несовместим с текущим
указанием не запускать пользовательские генерации самостоятельно. Промпты не
меняются. Task 3 также ожидает отдельного решения. Весь план не завершён.
