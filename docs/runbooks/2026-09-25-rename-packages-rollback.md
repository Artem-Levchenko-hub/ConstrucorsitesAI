# Откат волны 4.1–4.3 (переименование пакетов omnia_* → yleum_*), выкачено 25.09.2026 17:10–17:16 UTC

Ревизия волны: `1eba74c2c5919d2e9595e0cbe86740660ef78f5e` (NEW). Ревизия до неё: `113e8d82796518506afe406615191e75577b9696` (OLD).
Бэкапы юнитов: `/etc/systemd/system/omnia-orchestrator.service.pre-rename-4-1` на core и commerce. Старый образ шлюза: `omnia-gateway:pre-4-1` на core.
Старый образ api: `omnia-api:113e8d82…` на core, в реестре `registry.yleum.ru/platform/omnia-api:113e8d827965`.

Переключение юнитов вперёд (уже сделано): ExecStart `uvicorn omnia_orchestrator.main:app` → `uvicorn yleum_orchestrator.main:app` + `systemctl daemon-reload` на обоих хостах.


Почему нельзя «одной командой»: старый образ api ждёт пакет `omnia_api`, а новые юниты
оркестраторов запускают `yleum_orchestrator.main:app`. Откат = одновременно вернуть образ api,
исходники оркестраторов и юниты. Всё ниже — с Mac, порядок важен (api раньше оркестраторов,
как и при прямой выкатке; здесь наоборот не нужно — обе стороны старые).

Обозначения: OLD = ревизия до переименования (последняя зелёная, на проде сейчас), NEW = ревизия
переименования.

1. Замок: `ssh max-core cat /opt/omnia/.deploy.lock` — если остался от упавшей выкатки, удалить.
2. Юниты: восстановить юниты: `sudo cp /etc/systemd/system/omnia-orchestrator.service.pre-rename-4-1 /etc/systemd/system/omnia-orchestrator.service && sudo systemctl daemon-reload` на core и commerce (восстанавливает `/etc/systemd/system/omnia-orchestrator.service`
   из `*.pre-rename-4-1` на core и commerce, daemon-reload).
3. Источники оркестраторов на OLD:
   `ssh max-core 'cd /opt/omnia && git checkout -q --detach OLD && cd apps/orchestrator && ~/.local/bin/uv sync --frozen && sudo systemctl restart omnia-orchestrator && sleep 3 && curl -s 127.0.0.1:8003/health'`
   затем commerce: `ssh max-core 'rsync -a --delete --exclude .venv --exclude .env --exclude node_modules --exclude .next --exclude __pycache__ --exclude "*.tsbuildinfo" /opt/omnia/ commerce:/opt/omnia/ && ssh commerce "cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen && sudo systemctl restart omnia-orchestrator && sleep 3 && curl -s 127.0.0.1:8003/health"'`
   и OMNIA_RELEASE_SHA=OLD в обоих `apps/orchestrator/.env` (sed, как в deploy-prod.sh).
4. Api/воркеры на старый образ (он остаётся локально на core под тегом OLD):
   `ssh max-core 'cd /opt/omnia/apps/llm-gateway/deploy/full && sed -i -E "s#^(API_IMAGE=omnia-api:).*#\1OLD#; s#^(OMNIA_RELEASE_SHA=).*#\1OLD#" .env && docker compose up -d --no-build api worker generation-worker && sleep 5 && curl -s 127.0.0.1:8200/api/health | head -c 200'`
   (web не трогаем, если NEW не менял web; иначе так же WEB_IMAGE/WEB_RELEASE_SHA=OLD и `up -d --no-build --no-deps web`).
5. Воркер биллинга в K3s commerce: образ `registry.yleum.ru/platform/omnia-api:<OLD12>` уже в реестре —
   `infra/max-k3s/commerce/10-billing-workloads.sh apply` рендерит по digest текущего локального образа,
   поэтому сначала `docker tag omnia-api:OLD omnia-api:prod` на core, затем `image` + `apply` + `verify`.
6. Ожидаемые ревизии: `gh variable set PRODUCTION_EXPECTED_{API,WORKER,GENERATION_WORKER,ORCHESTRATOR}_RELEASE_SHA --body OLD`,
   общая тоже; `gh workflow run production-smoke.yml`.
7. Проверка: `curl https://yleum.ru/api/health` — status ok, api OLD, orchestrator OLD (не «mixed»),
   billing_worker ok; оба `127.0.0.1:8003/health` = OLD.

Перед прямой выкаткой 4.1 сохранить: `git rev-parse HEAD` на core (OLD), список образов
`docker images omnia-api --format '{{.Tag}}' | head`, юниты (делает `rename-4-1-units.sh prepare`).

8. Шлюз моделей: `ssh max-core 'docker tag omnia-gateway:pre-4-1 omnia-gateway:prod && cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose up -d --no-build --no-deps gateway && sleep 3 && curl -s 127.0.0.1:8101/health'`.
