# Production release and Telegram reporting removal

This is the canonical production path for the release that removes both Omnia
generation-reporting integrations. It preserves the public build/edit canary
and GitHub incident lifecycle. It removes the observer runtime, its table,
Compose worker, host environment keys, and GitHub secrets.

Never print secret values. Inspect and delete Telegram configuration by key
name only. Never run an Alembic downgrade: revision 0048 deletes observer-only
state and its downgrade exists solely for migration-contract tests.

## 1. Confirm exact revisions

The owner must approve exact 40-character revisions:

~~~text
Deploy RELEASE_SHA=<40 lowercase hex>; rollback to ROLLBACK_SHA=<40 lowercase hex>.
~~~

~~~bash
export RELEASE_SHA='<approved release SHA>'
export ROLLBACK_SHA='<approved currently deployed SHA>'
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$ROLLBACK_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$RELEASE_SHA" != "$ROLLBACK_SHA"
~~~

The release must be the protected main revision and the rollback revision must
be the exact live revision captured below.

## 2. Run the local release gate

Use only disposable loopback databases. The gate rejects a dirty tree, a
revision mismatch, and non-loopback database hosts.

~~~bash
export EXPECTED_RELEASE_SHA="$(git rev-parse HEAD)"
export DATABASE_URL='postgresql+asyncpg://omnia:omnia@127.0.0.1:5432/omnia_release_gate'
export DATABASE_TEST_URL='postgresql+asyncpg://omnia:omnia@127.0.0.1:5432/omnia_release_gate_test'
export JWT_SECRET='<local value of at least 32 bytes>'
bash infra/release/local-release-gate.sh
~~~

Retain the generated .release-evidence directory through the release decision.

## 3. Capture live state and prove the observer is dark

Run on the production host from /opt/omnia. This release assumes the prior
observer is disabled and its worker is absent. Stop if either assertion fails.

~~~bash
set -euo pipefail
cd /opt/omnia
export RELEASE_RECORD="/opt/omnia-runtime/releases/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$RELEASE_RECORD"

git rev-parse HEAD | tee "$RELEASE_RECORD/live-git-sha.txt"
git status --short | tee "$RELEASE_RECORD/live-git-status.txt"
test "$(cat "$RELEASE_RECORD/live-git-sha.txt")" = "$ROLLBACK_SHA"
test ! -s "$RELEASE_RECORD/live-git-status.txt"

full_env=/opt/omnia/apps/llm-gateway/deploy/full/.env
orchestrator_env=/opt/omnia/apps/orchestrator/.env
test "$(grep -Ec '^DEV_GENERATION_TELEGRAM_REPORTS=false$' "$full_env")" -eq 1
if docker container inspect omnia-prod-generation-report-worker >/dev/null 2>&1; then
  echo 'removed report worker is unexpectedly running' >&2
  exit 1
fi

docker ps --format '{{.Names}} {{.Image}} {{.ID}} {{.Status}}' >"$RELEASE_RECORD/containers.txt"
for service_name in api worker web; do
  docker inspect --format '{{.Image}}' "omnia-prod-$service_name" >"$RELEASE_RECORD/$service_name-image-id.txt"
  grep -Eq '^sha256:[0-9a-f]{64}$' "$RELEASE_RECORD/$service_name-image-id.txt"
done
cmp -s "$RELEASE_RECORD/api-image-id.txt" "$RELEASE_RECORD/worker-image-id.txt"
sudo systemctl status omnia-orchestrator --no-pager >"$RELEASE_RECORD/orchestrator-status.txt"
sudo nginx -T >"$RELEASE_RECORD/nginx.txt" 2>&1

bash infra/backup/backup-omnia.sh >"$RELEASE_RECORD/backup.log" 2>&1
test -s "$RELEASE_RECORD/backup.log"
~~~

The encrypted backup is the long-term recovery artifact. Never copy a live
secret-bearing environment file into the release record.

## 4. Require zero active generations

Run this query before every API/worker recreation and before rollback. It must
produce no rows.

~~~bash
active_generations="$(docker exec omnia-prod-postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select status,count(*) from generation_runs where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"') group by status;"')"
test -z "$active_generations"
~~~

Wait for terminal state or cancel through the normal product path if it is not
empty.

## 5. Fetch, persist rollback tooling, and preflight schema compatibility

Check out only the approved release, then persist every helper outside the Git
checkout before changing the database, live environment, or image tags.

~~~bash
git fetch --prune origin
test "$(git rev-parse "$RELEASE_SHA^{commit}")" = "$RELEASE_SHA"
git checkout --detach "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$RELEASE_SHA"

release_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
export FULL_ENV_BACKUP="$full_env.pre-release-$release_timestamp"
export ORCHESTRATOR_ENV_BACKUP="$orchestrator_env.pre-release-$release_timestamp"
cp -p "$full_env" "$FULL_ENV_BACKUP"
cp -p "$orchestrator_env" "$ORCHESTRATOR_ENV_BACKUP"
test "$(stat -c '%a:%u:%g' "$FULL_ENV_BACKUP")" = "$(stat -c '%a:%u:%g' "$full_env")"
test "$(stat -c '%a:%u:%g' "$ORCHESTRATOR_ENV_BACKUP")" = "$(stat -c '%a:%u:%g' "$orchestrator_env")"

export ROLLBACK_POINTER=/opt/omnia-runtime/releases/pending-rollback.json
rollback_manifest_tool=/opt/omnia-runtime/releases/rollback-manifest.sh
rollback_env_updater=/opt/omnia-runtime/releases/update-env-value.sh
removal_env_tool=/opt/omnia-runtime/releases/remove-env-value.sh
compat_migration=/opt/omnia-runtime/releases/0048_remove_generation_telegram_reports.py
rollback_compose_override=/opt/omnia-runtime/releases/rollback-0048.override.yml

install -m 700 infra/release/rollback-manifest.sh "${rollback_manifest_tool}"
install -m 700 infra/release/update-env-value.sh "${rollback_env_updater}"
install -m 700 infra/release/remove-env-value.sh "${removal_env_tool}"
install -m 600 apps/api/migrations/versions/0048_remove_generation_telegram_reports.py "${compat_migration}"
bash "$rollback_manifest_tool" write "$ROLLBACK_POINTER" "$RELEASE_RECORD" "$FULL_ENV_BACKUP" "$ORCHESTRATOR_ENV_BACKUP" "$RELEASE_SHA" "$ROLLBACK_SHA"

cat >"$rollback_compose_override" <<'YAML'
services:
  api:
    volumes:
      - "${COMPAT_MIGRATION}:/app/migrations/versions/0048_remove_generation_telegram_reports.py:ro"
  worker:
    volumes:
      - "${COMPAT_MIGRATION}:/app/migrations/versions/0048_remove_generation_telegram_reports.py:ro"
YAML
chmod 600 "$rollback_compose_override"
~~~

Preflight the captured rollback API image against a disposable PostgreSQL
database with the external 0048 migration mounted. This proves a clean-shell
rollback can start after production advances to revision 0048.

~~~bash
rollback_api_image="$(tr -d '\n' <"$RELEASE_RECORD/api-image-id.txt")"
[[ "$rollback_api_image" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image inspect "$rollback_api_image" >/dev/null
preflight_network="omnia-rollback-preflight-$release_timestamp"
preflight_db="omnia-rollback-preflight-db-$release_timestamp"
preflight_redis="omnia-rollback-preflight-redis-$release_timestamp"
preflight_api="omnia-rollback-preflight-api-$release_timestamp"
cleanup_preflight() {
  docker rm -f "$preflight_api" "$preflight_redis" "$preflight_db" \
    >/dev/null 2>&1 || true
  docker network rm "$preflight_network" >/dev/null 2>&1 || true
}
trap cleanup_preflight EXIT
docker network create "$preflight_network" >/dev/null
docker run -d --rm --name "$preflight_db" --network "$preflight_network" -e POSTGRES_USER=omnia -e POSTGRES_PASSWORD=rollback-preflight -e POSTGRES_DB=omnia postgres:16 >/dev/null
for _attempt in $(seq 1 60); do
  docker exec "$preflight_db" pg_isready -U omnia -d omnia >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$preflight_db" pg_isready -U omnia -d omnia >/dev/null
docker run -d --rm --name "$preflight_redis" --network "$preflight_network" \
  redis:7-alpine >/dev/null
for _attempt in $(seq 1 30); do
  docker exec "$preflight_redis" redis-cli ping 2>/dev/null | grep -Fxq PONG && break
  sleep 1
done
test "$(docker exec "$preflight_redis" redis-cli ping)" = PONG
docker run --rm --network "$preflight_network" -v "${compat_migration}:/app/migrations/versions/0048_remove_generation_telegram_reports.py:ro" -e DATABASE_URL="postgresql+asyncpg://omnia:rollback-preflight@$preflight_db:5432/omnia" -e JWT_SECRET=rollback-preflight-jwt-secret-at-least-32-bytes -e SECRETS_ENCRYPTION_KEY=rollback-preflight-encryption-key "$rollback_api_image" /app/.venv/bin/alembic upgrade head
test "$(docker exec "$preflight_db" psql -U omnia -d omnia -Atc 'select version_num from alembic_version;')" = 0048_remove_telegram_reports

docker run -d --name "$preflight_api" --network "$preflight_network" \
  -v "${compat_migration}:/app/migrations/versions/0048_remove_generation_telegram_reports.py:ro" \
  -e DATABASE_URL="postgresql+asyncpg://omnia:rollback-preflight@$preflight_db:5432/omnia" \
  -e REDIS_URL="redis://$preflight_redis:6379/0" \
  -e JWT_SECRET=rollback-preflight-jwt-secret-at-least-32-bytes \
  -e SECRETS_ENCRYPTION_KEY=rollback-preflight-encryption-key \
  -e "OMNIA_RELEASE_SHA=$ROLLBACK_SHA" \
  "$rollback_api_image" \
  /app/.venv/bin/uvicorn omnia_api.main:app --host 0.0.0.0 --port 8000 \
  >/dev/null
preflight_api_ready=false
for _attempt in $(seq 1 60); do
  if docker exec "$preflight_api" /app/.venv/bin/python -c \
    'import json,sys,urllib.request; payload=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)); raise SystemExit(0 if payload == {"status": "ok", "release_sha": sys.argv[1]} else 1)' \
    "$ROLLBACK_SHA"; then
    preflight_api_ready=true
    break
  fi
  sleep 1
done
if [[ "$preflight_api_ready" != true ]]; then
  docker logs "$preflight_api"
  exit 1
fi
cleanup_preflight
trap - EXIT
~~~

## 6. Build and render the removal release

Create permission-preserving candidates. The first rollout changes only release
identity; host secret keys are removed after exact health is established.

~~~bash
compose_file=apps/llm-gateway/deploy/full/docker-compose.yml
candidate_full_env="$(mktemp "$full_env.candidate.XXXXXX")"
candidate_orchestrator_env="$(mktemp "$orchestrator_env.candidate.XXXXXX")"
rendered_compose="$(mktemp)"
cleanup_candidates() {
  test -z "$candidate_full_env" || rm -f "$candidate_full_env"
  test -z "$candidate_orchestrator_env" || rm -f "$candidate_orchestrator_env"
  rm -f "$rendered_compose"
}
trap cleanup_candidates EXIT
cp -p "$full_env" "$candidate_full_env"
cp -p "$orchestrator_env" "$candidate_orchestrator_env"
bash infra/release/update-env-value.sh "$candidate_full_env" OMNIA_RELEASE_SHA "$RELEASE_SHA"
bash infra/release/update-env-value.sh "$candidate_orchestrator_env" OMNIA_RELEASE_SHA "$RELEASE_SHA"

bash infra/release/test-compose-policy.sh
chmod 600 "$rendered_compose"
docker compose --env-file "$candidate_full_env" -f "$compose_file" config --format json >"$rendered_compose"
jq -e '.services | has("generation-report-worker") | not' "$rendered_compose" >/dev/null
for service_name in api worker web; do
  test "$(jq -r --arg service "$service_name" '.services[$service].environment.OMNIA_RELEASE_SHA' "$rendered_compose")" = "$RELEASE_SHA"
done
for removed_name in DEV_GENERATION_TELEGRAM_REPORTS TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  jq -e --arg name "$removed_name" 'all(.services[]; ((.environment // {}) | has($name) | not))' "$rendered_compose" >/dev/null
done

web_api_url="$(jq -er '.services.web.build.args.NEXT_PUBLIC_API_URL' "$rendered_compose")"
web_ws_url="$(jq -er '.services.web.build.args.NEXT_PUBLIC_WS_URL' "$rendered_compose")"
legal_version="$(jq -er '.services.web.build.args.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION' "$rendered_compose")"
docker build -t "omnia-api:$RELEASE_SHA" apps/api
docker build --build-arg "NEXT_PUBLIC_API_URL=$web_api_url" --build-arg "NEXT_PUBLIC_WS_URL=$web_ws_url" --build-arg NEXT_PUBLIC_USE_MOCKS=false --build-arg "NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION=$legal_version" -t "omnia-web:$RELEASE_SHA" apps/web
docker image inspect "omnia-api:$RELEASE_SHA" "omnia-web:$RELEASE_SHA" >/dev/null
~~~

## 7. Roll out and prove exact health

~~~bash
mv -f "$candidate_full_env" "$full_env"
candidate_full_env=""
docker tag "omnia-api:$RELEASE_SHA" omnia-api:prod
docker tag "omnia-web:$RELEASE_SHA" omnia-web:prod

docker compose --env-file "$full_env" -f "$compose_file" up -d --no-deps api
for _attempt in $(seq 1 60); do
  curl -fsS --max-time 5 http://127.0.0.1:8200/api/health >/dev/null && break
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:8200/api/health >/dev/null
docker compose --env-file "$full_env" -f "$compose_file" up -d --no-deps worker
docker compose --env-file "$full_env" -f "$compose_file" up -d --no-deps web
test ! "$(docker compose --env-file "$full_env" -f "$compose_file" config --services | grep -Fx generation-report-worker || true)"
docker rm -f omnia-prod-generation-report-worker >/dev/null 2>&1 || true

mv -f "$candidate_orchestrator_env" "$orchestrator_env"
candidate_orchestrator_env=""
cd /opt/omnia/apps/orchestrator
uv sync --frozen
sudo systemctl restart omnia-orchestrator
cd /opt/omnia
trap - EXIT

fetch_health() {
  local url="$1"
  local payload
  for _attempt in $(seq 1 60); do
    if payload="$(curl -fsS --max-time 5 "$url")"; then
      printf '%s' "$payload"
      return 0
    fi
    sleep 2
  done
  return 1
}
web="$(fetch_health http://127.0.0.1:3100/web-health)"
api="$(fetch_health http://127.0.0.1:8200/api/health)"
orchestrator="$(fetch_health http://127.0.0.1:8003/health)"
test "$(jq -r .release_sha <<<"$web")" = "$RELEASE_SHA"
test "$(jq -r .release_sha <<<"$api")" = "$RELEASE_SHA"
test "$(jq -r .dependencies.worker_release_sha <<<"$api")" = "$RELEASE_SHA"
test "$(jq -r .release_sha <<<"$orchestrator")" = "$RELEASE_SHA"

public_web="$(fetch_health https://constructor.lead-generator.ru/web-health)"
public_api="$(fetch_health https://constructor.lead-generator.ru/api/health)"
test "$(jq -r .release_sha <<<"$public_web")" = "$RELEASE_SHA"
test "$(jq -r .release_sha <<<"$public_api")" = "$RELEASE_SHA"
test "$(jq -r .dependencies.worker_release_sha <<<"$public_api")" = "$RELEASE_SHA"
test "$(jq -r .dependencies.orchestrator_release_sha <<<"$public_api")" = "$RELEASE_SHA"
~~~

## 8. Purge server configuration and observer state

Repeat the zero-active-generation query from section 4. Then remove every exact
key assignment from a protected candidate, atomically install it, and recreate
the two Python services.

~~~bash
active_generations="$(docker exec omnia-prod-postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select status,count(*) from generation_runs where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"') group by status;"')"
test -z "$active_generations"
candidate_full_env="$(mktemp "$full_env.remove-telegram.XXXXXX")"
removal_render="$(mktemp)"
trap 'rm -f "$candidate_full_env" "$removal_render"' EXIT
cp -p "$full_env" "$candidate_full_env"
bash "${removal_env_tool}" "${candidate_full_env}" DEV_GENERATION_TELEGRAM_REPORTS
bash "${removal_env_tool}" "${candidate_full_env}" TELEGRAM_BOT_TOKEN
bash "${removal_env_tool}" "${candidate_full_env}" TELEGRAM_CHAT_ID
for removed_name in DEV_GENERATION_TELEGRAM_REPORTS TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  test "$(grep -Ec "^$removed_name=" "$candidate_full_env")" -eq 0
done
chmod 600 "$removal_render"
docker compose --env-file "$candidate_full_env" -f "$compose_file" config --format json >"$removal_render"
for removed_name in DEV_GENERATION_TELEGRAM_REPORTS TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  jq -e --arg name "$removed_name" 'all(.services[]; ((.environment // {}) | has($name) | not))' "$removal_render" >/dev/null
done
mv -f "$candidate_full_env" "$full_env"
candidate_full_env=""
rm -f "$removal_render"
removal_render=""
docker compose --env-file "$full_env" -f "$compose_file" up -d --no-deps --force-recreate api
curl -fsS --retry 60 --retry-delay 2 --retry-connrefused --max-time 5 http://127.0.0.1:8200/api/health >/dev/null
docker compose --env-file "$full_env" -f "$compose_file" up -d --no-deps --force-recreate worker
trap - EXIT

for service_name in $(docker compose --env-file "$full_env" -f "$compose_file" config --services); do
  container_id="$(
    docker compose --env-file "$full_env" -f "$compose_file" ps -q "$service_name"
  )"
  test -z "$container_id" && continue
  for removed_name in DEV_GENERATION_TELEGRAM_REPORTS TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
    if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" | cut -d= -f1 | grep -Fxq "$removed_name"; then
      echo "removed environment key remains in a running container: $removed_name" >&2
      exit 1
    fi
  done
done
table_absent="$(
  printf '%s\\n' "SELECT to_regclass('public.generation_telegram_reports') IS NULL;" |
    docker exec -i omnia-prod-postgres sh -lc
      'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'
)"
test "$table_absent" = t
test ! "$(docker ps --format '{{.Names}}' | grep -Fx omnia-prod-generation-report-worker || true)"
~~~

Repeat every local/public identity assertion from section 7 after recreation.

## 9. Run smoke and paid canary, then delete GitHub secrets

Set the protected expected revision, run both workflows from main, and require
success before deleting secrets.

~~~bash
gh variable set PRODUCTION_EXPECTED_RELEASE_SHA --env production --body "$RELEASE_SHA"
gh workflow run production-smoke.yml --ref main
smoke_run="$(gh run list --workflow production-smoke.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$smoke_run" --exit-status
gh workflow run production-generation-canary.yml --ref main
canary_run="$(gh run list --workflow production-generation-canary.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$canary_run" --exit-status

if gh secret list --env production --json name --jq '.[].name' | grep -Fxq TELEGRAM_BOT_TOKEN; then
  gh secret delete TELEGRAM_BOT_TOKEN --env production
fi
if gh secret list --env production --json name --jq '.[].name' | grep -Fxq TELEGRAM_CHAT_ID; then
  gh secret delete TELEGRAM_CHAT_ID --env production
fi
if gh secret list --json name --jq '.[].name' | grep -Fxq TELEGRAM_BOT_TOKEN; then
  gh secret delete TELEGRAM_BOT_TOKEN
fi
if gh secret list --json name --jq '.[].name' | grep -Fxq TELEGRAM_CHAT_ID; then
  gh secret delete TELEGRAM_CHAT_ID
fi
~~~

GitHub deletion invalidates no already-issued bot credential. The owner must
revoke the bot token manually through BotFather after the release is accepted.
Never paste the replacement or revoked value into a task, log, or shell argv.

## 10. Rollback

Rollback on migration/startup, health, generation, preview, or cleanup failure.
Never run an Alembic downgrade. Revision 0048 remains canonical; the external
compatibility file lets the old API recognize it.

This section is resumable from a fresh shell even after checking out the old
revision.

~~~bash
set -euo pipefail
cd /opt/omnia
rollback_pointer=/opt/omnia-runtime/releases/pending-rollback.json
rollback_manifest_tool=/opt/omnia-runtime/releases/rollback-manifest.sh
rollback_env_updater=/opt/omnia-runtime/releases/update-env-value.sh
compat_migration=/opt/omnia-runtime/releases/0048_remove_generation_telegram_reports.py
rollback_compose_override=/opt/omnia-runtime/releases/rollback-0048.override.yml

RELEASE_RECORD="$(bash "$rollback_manifest_tool" read "$rollback_pointer" release_record)"
FULL_ENV_BACKUP="$(bash "$rollback_manifest_tool" read "$rollback_pointer" full_env_backup)"
ORCHESTRATOR_ENV_BACKUP="$(bash "$rollback_manifest_tool" read "$rollback_pointer" orchestrator_env_backup)"
RELEASE_SHA="$(bash "$rollback_manifest_tool" read "$rollback_pointer" release_sha)"
ROLLBACK_SHA="$(bash "$rollback_manifest_tool" read "$rollback_pointer" rollback_sha)"
test -d "$RELEASE_RECORD"
test -f "$FULL_ENV_BACKUP"
test -f "$ORCHESTRATOR_ENV_BACKUP"
test -f "$compat_migration"
test -f "$rollback_compose_override"
test "$(cat "$RELEASE_RECORD/live-git-sha.txt")" = "$ROLLBACK_SHA"

active_generations="$(docker exec omnia-prod-postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select status,count(*) from generation_runs where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"') group by status;"')"
test -z "$active_generations"

full_env=/opt/omnia/apps/llm-gateway/deploy/full/.env
orchestrator_env=/opt/omnia/apps/orchestrator/.env
rollback_full_candidate="$(mktemp "$full_env.rollback.XXXXXX")"
rollback_orchestrator_candidate="$(mktemp "$orchestrator_env.rollback.XXXXXX")"
trap 'rm -f "$rollback_full_candidate" "$rollback_orchestrator_candidate"' EXIT
cp -p "$FULL_ENV_BACKUP" "$rollback_full_candidate"
cp -p "$ORCHESTRATOR_ENV_BACKUP" "$rollback_orchestrator_candidate"
bash "$rollback_env_updater" "$rollback_full_candidate" USE_PROJECT_MEMORY false
bash "$rollback_env_updater" "$rollback_full_candidate" OMNIA_RELEASE_SHA "$ROLLBACK_SHA"
bash "$rollback_env_updater" "$rollback_orchestrator_candidate" OMNIA_RELEASE_SHA "$ROLLBACK_SHA"
mv -f "$rollback_full_candidate" "$full_env"
rollback_full_candidate=""

rollback_api_image="$(tr -d '\n' <"$RELEASE_RECORD/api-image-id.txt")"
rollback_web_image="$(tr -d '\n' <"$RELEASE_RECORD/web-image-id.txt")"
docker image inspect "$rollback_api_image" "$rollback_web_image" >/dev/null
git checkout --detach "$ROLLBACK_SHA"
test "$(git rev-parse HEAD)" = "$ROLLBACK_SHA"
docker tag "$rollback_api_image" omnia-api:prod
docker tag "$rollback_web_image" omnia-web:prod
export COMPAT_MIGRATION="$compat_migration"
compose_file=apps/llm-gateway/deploy/full/docker-compose.yml
docker compose --env-file "$full_env" -f "$compose_file" -f "$rollback_compose_override" up -d --no-deps api
curl -fsS --retry 60 --retry-delay 2 --retry-connrefused --max-time 5 http://127.0.0.1:8200/api/health >/dev/null
docker compose --env-file "$full_env" -f "$compose_file" -f "$rollback_compose_override" up -d --no-deps worker
docker compose --env-file "$full_env" -f "$compose_file" up -d --no-deps web
docker rm -f omnia-prod-generation-report-worker >/dev/null 2>&1 || true

mv -f "$rollback_orchestrator_candidate" "$orchestrator_env"
rollback_orchestrator_candidate=""
cd /opt/omnia/apps/orchestrator
uv sync --frozen
sudo systemctl restart omnia-orchestrator
cd /opt/omnia
trap - EXIT
~~~

Repeat the local/public health checks and require the rollback revision for
web, API, worker, and orchestrator. Keep the rollback pointer, external helper
bundle, 0048 compatibility migration, override, image IDs, and protected
environment backups until the rollback is accepted.

After either release or rollback is accepted, retain the encrypted backup under
the normal retention policy. Remove the plaintext protected environment
backups and rollback bundle only after the owner closes the rollback window.
Never run docker compose down -v; it deletes production volumes.
