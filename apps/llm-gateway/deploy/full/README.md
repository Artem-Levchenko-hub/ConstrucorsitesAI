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
   docker exec omnia-prod-postgres sh -lc \
     'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
     "select status,count(*) from generation_runs
      where finished_at is null
      group by status
      order by status;"'
   ```

   Any output blocks the update. This includes a generation queued for server
   capacity even though its model work has not started yet.

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

7. Run a disposable end-to-end generation canary and inspect its terminal
   `generation_runs` status before declaring the deployment complete.

Never use `docker compose down -v` during an update: it removes production
volumes.
