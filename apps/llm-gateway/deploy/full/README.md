# Full-stack production deployment

> All production updates and rollbacks must follow
> [`infra/release/README.md`](../../../../infra/release/README.md), including the
> exact release/rollback confirmation, local gate, dark-memory policy, health
> identity checks, and disposable canary.

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
   docker exec omnia-prod-postgres sh -lc \
     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
     "select status,count(*) from generation_runs
      where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"')
      group by status;"'
   ```

2. Back up Postgres and the current Compose/nginx configuration.
3. Validate configuration before changing containers:

   ```bash
   docker compose config --quiet
   ```

4. Build and test images under temporary tags first.
5. Deploy the API before the worker. The API command runs
   `alembic upgrade head`, and the worker waits for the API health check.
6. Verify:

   ```bash
   curl -fsS http://127.0.0.1:8200/health
   curl -fsS http://127.0.0.1:8101/health
   curl -fsS http://127.0.0.1:3100/ >/dev/null
   ```

7. Dispatch and watch the repository-owned disposable canary before declaring
   the deployment complete:

   ```bash
   test "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$RELEASE_SHA"
   gh workflow run production-generation-canary.yml --ref main
   gh run watch "$(gh run list --workflow production-generation-canary.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
   ```

   For local diagnosis with credentials already present in the environment:

   ```bash
   cd apps/api
   uv run python scripts/production_generation_canary.py
   ```

Never use `docker compose down -v` during an update: it removes production
volumes.
