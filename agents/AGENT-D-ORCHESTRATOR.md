# Агент D — Orchestrator + DevOps

> **Статус брифа.** Зона ответственности и особенности компонента. Общие правила — Git-процесс через PR,
> проверки, безопасность данных, выпуск и критерии готовности — в [`AGENTS.md`](../AGENTS.md).
> Стек и структура ниже — исходный замысел V2 Phase A (май 2026): перед опорой на них сверяй с кодом.

При работе в зоне читай актуальную границу Project Cell (`docs/superpowers/specs/2026-08-31-enterprise-project-cell-agent-runtime-design.md`),
`docs/08-vps-setup.md` (сервер) и `infra/release/README.md` (выпуск). `docs/07-v2-architecture.md` — legacy-контекст V2.

## Кто ты в этой команде

Ты владелец **runtime-плоскости V2**: per-project Docker контейнеры, hibernate-таймер, build-pipeline для deploy, nginx auto-config, изоляция через per-project Postgres schemas.

Твой сервис называется **`omnia-orchestrator`**, лежит в `apps/orchestrator/`. Слушает `:8003` на хосте VPS (НЕ внутри docker-compose). Принимает запросы только от `apps/api` через header `X-Internal-Token` (shared secret).

## Зона ответственности

- Ответственность — `apps/orchestrator/` и `infra/` (docker-compose dev-стека, релиз, бэкап, Project Cell, nginx-шаблоны).
- Правка других зон — по правилам раздела 1 `AGENTS.md` (нужна задаче, указана в PR, согласована). Публичные endpoints для runtime добавляет backend (агент B).
- Контракт internal API — `apps/orchestrator/src/omnia_orchestrator/schemas/runtime.py`; публичная часть — раздел V2 `docs/01-api-contract.md`. Меняются в том же PR, что реализация и тесты, с согласия агента B.
- Операции на production-сервере — только в рамках разрешённого выпуска или явно порученной операции (разделы 3 и 7 `AGENTS.md`).

## Стек

- **Python 3.12**, `uv`.
- **FastAPI 0.115+** (async).
- **docker-py 7.1+** — Docker Engine API SDK.
- **asyncpg** — admin connection к `omnia-postgres-users` для `CREATE SCHEMA`.
- **pydantic v2** + pydantic-settings.
- **structlog** — единый лог-формат.
- **sentry-sdk[fastapi]** — копия паттерна из `apps/api/core/sentry.py`.
- **httpx** — health-poll контейнеров после wake.
- `pytest + pytest-asyncio + pytest-mock` (мокаем docker).

## Проверки

Обязательный набор — как в CI (раздел 6 `AGENTS.md`): `uv sync --frozen`, ruff и `uv run mypy src` как в job `orchestrator-release-gate`, `uv run pytest -q`; для `infra/release` — `bash infra/release/test-release-tools.sh`.

## История: исходный план фаз

План sprint A1 (scaffold, роадмап по дням, открытые вопросы, smoke «sprint A1 done») времён V2 Phase A — исторический; отметки и «Definition of Done» оттуда не описывают текущее
состояние и не обязательны к чтению при старте. Полный текст — в Git: `git show d5088222:agents/AGENT-D-ORCHESTRATOR.md`.
