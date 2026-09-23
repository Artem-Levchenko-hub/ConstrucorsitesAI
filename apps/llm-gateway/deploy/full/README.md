# Full-stack production deployment

This Compose stack is intended to sit behind nginx on the same host.

## Security invariants

- Web, API, gateway, and MinIO host ports bind to `127.0.0.1`.
- The API reaches the LLM gateway through the private Compose network.
- Do not expose `/llm/` through public nginx. The gateway has no end-user
  authentication and forwards requests with the platform provider credentials.
- Only nginx ports 80/443 and explicitly required infrastructure ports should be
  reachable from the internet.

If a legacy nginx config still contains `location /llm/`, remove it or restrict
it:

```nginx
location /llm/ {
    allow 127.0.0.1;
    allow ::1;
    deny all;
    proxy_pass http://127.0.0.1:8101/;
}
```

## Safe update

1. Confirm there is no active generation:

   ```bash
   bash /opt/omnia/infra/release/check-active-generations.sh
   ```

   The script reads the platform DB where it lives: inside
   `omnia-prod-postgres`, or on the host PostgreSQL of core once `.env`
   carries `PLATFORM_DATABASE_URL` (see `docker-compose.hostdb.yml`). Any
   output blocks the update. This includes a generation queued for server
   capacity even though its model work has not started yet.

2. Back up Postgres and the current Compose/nginx configuration.
3. Validate configuration before changing containers:

   ```bash
   docker compose config --quiet
   ```

4. Build and test images under temporary tags first. Public Project Cells also
   require a precompiled trusted MAX core, separate from the agent's dev kit:

   ```bash
   cd /opt/omnia
   revision=$(git rev-parse HEAD)
   core_base=$(docker image inspect omnia-template-max-miniapp-nextjs:dev --format '{{.Id}}')
   bash apps/orchestrator/scripts/build-public-max-core.sh "$core_base" "omnia-max-public-core:$revision"
   docker image inspect "omnia-max-public-core:$revision" --format '{{.Id}}'
   ```

   Set `CELL_PUBLIC_CORE_IMAGE` in `/opt/omnia/apps/orchestrator/.env` to that
   immutable image ID before restarting `omnia-orchestrator.service`. Preserve
   the old environment/image for rollback. The build reuses the pinned kit's
   dependencies with no package install; its network is disabled. Its runtime
   serves the current trusted routes from `server.js`, with metadata updated as
   atomic JSON data. Do not replace or retag the agent's dev template image.
5. Deploy the API before the worker. The API command runs
   `alembic upgrade head`, and the worker waits for the API health check.
6. Verify:

   ```bash
   curl -fsS http://127.0.0.1:8200/health
   curl -fsS http://127.0.0.1:8101/health
   curl -fsS http://127.0.0.1:3100/ >/dev/null
   ```

7. Run a disposable end-to-end generation canary and inspect its terminal
   `generation_runs` status before declaring the deployment complete.

Never use `docker compose down -v` during an update: it removes production
volumes.

## Production smoke

`.github/workflows/production-smoke.yml` запускает внешнюю проверку каждые пять
минут. Она проверяет web/API, шесть зависимостей API, версии компонентов,
страницу `/mvp`, health постоянного MAX-проекта и отказ webhook без авторизации.
Проверка генерации через модель и пользовательских данных выполняется отдельным
generation canary из шага 7.

Настройки находятся в GitHub Actions repository variables:

| Переменная | Ожидаемое значение |
| --- | --- |
| `PRODUCTION_EXPECTED_WEB_RELEASE_SHA` | Полный SHA работающего web image |
| `PRODUCTION_EXPECTED_API_RELEASE_SHA` | Полный SHA работающего API image |
| `PRODUCTION_EXPECTED_WORKER_RELEASE_SHA` | Полный SHA общего image worker и generation-worker |
| `PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA` | Полный SHA работающего host-orchestrator |
| `PRODUCTION_MAX_CANARY_URL` | Базовый адрес постоянного опубликованного служебного MAX-проекта |

Все SHA содержат ровно 40 шестнадцатеричных символов. API-only поставка сохраняет
прежние ожидаемые версии web и orchestrator. Ожидаемые значения обновляются после
подтверждения фактических образов и readiness. Прежняя общая переменная
`PRODUCTION_EXPECTED_RELEASE_SHA` этим workflow больше не используется.

Контрольный MAX-проект сохраняется между запусками. Перед заменой его адреса
подтвердите `/api/health` → 200 с `status=ok`, `platform=max-miniapp`, а также
неавторизованный `POST /api/max/webhook` с `{}` → 401. Одноразовый проект,
удаляемый cleanup генерационного теста, для этого адреса не подходит. Новый MAX
в Project Cell требует первой принятой сборки перед штатным start/deploy.

Ошибки имеют имена, например `api.release_mismatch`,
`api.readiness.generation_worker`, `max_health.http_502`. Тела ответов и адреса
в диагностический вывод не попадают. Отсутствующий canary, неизвестная версия
или успешный неавторизованный webhook оставляют проверку красной.

Запуски по расписанию сохраняют прежнее управление incident issue. Push и
ручной запуск проверяют те же условия без создания issues и комментариев.
Для проверки с уже заданными переменными и без сторонних Python-зависимостей:

```bash
PYTHONPATH=apps/api/src python3 -S -m omnia_api.ops.production_smoke
```
