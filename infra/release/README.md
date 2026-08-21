# Production release and rollback

This is the required procedure for changes to the production builder. The local
gate is read-only with respect to production: it accepts only loopback or local
Unix-socket database URLs and writes evidence only to ignored
`.release-evidence/`.

The repository must have a GitHub Environment named `production` whose
deployment branch policy allows only the protected `main` branch. Store
`PRODUCTION_CANARY_EMAIL`, `PRODUCTION_CANARY_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
and `TELEGRAM_CHAT_ID` as secrets in that environment, not as repository-level
secrets. `TELEGRAM_CHAT_ID` must identify the approved team group. A bot token
that has ever been pasted into chat, a ticket, a command argument, or a log must
be revoked in BotFather and replaced before this procedure. Do not add required
reviewers to the environment unless
scheduled canaries are intentionally expected to wait for approval. The
workflow also rejects every ref except `refs/heads/main` before checkout and
exposes credentials only to the steps that consume them.

## 1. Owner confirmation

Do not SSH to production, edit a production environment file, restart a
container/service, run a migration, change a GitHub production variable, or run
the paid canary until the owner explicitly confirms both exact 40-character
revisions:

```text
Deploy RELEASE_SHA=<40 lowercase hex>; rollback to ROLLBACK_SHA=<40 lowercase hex>.
```

Set and validate the reviewed inputs locally:

```bash
export RELEASE_SHA='<confirmed 40-character release SHA>'
export ROLLBACK_SHA='<confirmed 40-character rollback SHA>'
test "${#RELEASE_SHA}" -eq 40
test "${#ROLLBACK_SHA}" -eq 40
```

Record the confirmation in the change/task before continuing.

## 2. Local release gate

Use two distinct disposable local databases. The gate rejects remote database
hosts, a dirty tree, `unknown`, and any revision other than the current exact
`HEAD`.

```bash
export EXPECTED_RELEASE_SHA="$(git rev-parse HEAD)"
export DATABASE_URL='postgresql+asyncpg://localhost/omnia_release_gate'
export DATABASE_TEST_URL='postgresql+asyncpg://localhost/omnia_release_gate_test'
export JWT_SECRET='<local test secret of at least 32 bytes>'
bash infra/release/local-release-gate.sh
```

The gate runs locked installs, static checks, migrations/tests against local
databases, web tests/build, rendered Compose validation, and production API/web
image builds. Keep its `.release-evidence/<sha>-<UTC timestamp>/` directory until
the release decision is complete. Do not call the gate with a production DSN.

## 3. Capture live state and backups

Only after the confirmation in section 1, connect to the production host and
capture rollback inputs before checking out new code. Run as the production
operator from `/opt/omnia`:

```bash
set -euo pipefail
cd /opt/omnia
export RELEASE_RECORD="/opt/omnia-runtime/releases/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "${RELEASE_RECORD}"

git rev-parse HEAD | tee "${RELEASE_RECORD}/live-git-sha.txt"
git status --short | tee "${RELEASE_RECORD}/live-git-status.txt"
docker ps --format '{{.Names}} {{.Image}} {{.ID}} {{.Status}}' \
  | tee "${RELEASE_RECORD}/containers.txt"
docker inspect --format '{{.Name}} {{.Image}}' \
  omnia-prod-api omnia-prod-worker omnia-prod-web \
  | tee "${RELEASE_RECORD}/image-ids.txt"
docker inspect --format '{{.Image}}' omnia-prod-api \
  >"${RELEASE_RECORD}/api-image-id.txt"
docker inspect --format '{{.Image}}' omnia-prod-worker \
  >"${RELEASE_RECORD}/worker-image-id.txt"
docker inspect --format '{{.Image}}' omnia-prod-web \
  >"${RELEASE_RECORD}/web-image-id.txt"
if docker container inspect omnia-prod-generation-report-worker >/dev/null 2>&1; then
  printf 'present\n' >"${RELEASE_RECORD}/report-worker-status.txt"
  docker inspect --format '{{.Image}}' omnia-prod-generation-report-worker \
    >"${RELEASE_RECORD}/report-worker-image-id.txt"
  grep -Eq '^sha256:[0-9a-f]{64}$' \
    "${RELEASE_RECORD}/report-worker-image-id.txt"
  cmp -s "${RELEASE_RECORD}/api-image-id.txt" \
    "${RELEASE_RECORD}/report-worker-image-id.txt"
else
  printf 'absent\n' >"${RELEASE_RECORD}/report-worker-status.txt"
  printf 'absent\n' >"${RELEASE_RECORD}/report-worker-image-id.txt"
fi
grep -Eq '^sha256:[0-9a-f]{64}$' "${RELEASE_RECORD}/api-image-id.txt"
grep -Eq '^sha256:[0-9a-f]{64}$' "${RELEASE_RECORD}/worker-image-id.txt"
grep -Eq '^sha256:[0-9a-f]{64}$' "${RELEASE_RECORD}/web-image-id.txt"
cmp -s "${RELEASE_RECORD}/api-image-id.txt" "${RELEASE_RECORD}/worker-image-id.txt"
sudo systemctl status omnia-orchestrator --no-pager \
  >"${RELEASE_RECORD}/orchestrator-status.txt"
sudo nginx -T >"${RELEASE_RECORD}/nginx.txt" 2>&1

bash infra/backup/backup-omnia.sh \
  >"${RELEASE_RECORD}/backup.log" 2>&1
test -s "${RELEASE_RECORD}/backup.log"
test "$(cat "${RELEASE_RECORD}/live-git-sha.txt")" = "${ROLLBACK_SHA}"
test ! -s "${RELEASE_RECORD}/live-git-status.txt"
```

The backup script captures Postgres, project sources, MinIO data, and canonical
configuration in its encrypted backup. Do not copy secret-bearing `.env` files
into the release record as plaintext.

If either final assertion fails, stop and obtain a new owner confirmation or
cleanly resolve the production worktree before continuing.

## 4. Require zero active generations

The following existing query must return no rows:

```bash
active_generations="$(docker exec omnia-prod-postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select status,count(*) from generation_runs
   where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"')
   group by status;"')"
test -z "${active_generations}"
```

If it prints any count, do not restart API or worker. Wait for terminal state or
handle the run through the normal cancellation path, then repeat the query.

## 5. Prepare the dark release

Fetch only the confirmed revision and verify it before building:

```bash
git fetch --prune origin
test "$(git rev-parse "${RELEASE_SHA}^{commit}")" = "${RELEASE_SHA}"
git checkout --detach "${RELEASE_SHA}"
test "$(git rev-parse HEAD)" = "${RELEASE_SHA}"

full_env=/opt/omnia/apps/llm-gateway/deploy/full/.env
orchestrator_env=/opt/omnia/apps/orchestrator/.env
compose_file=apps/llm-gateway/deploy/full/docker-compose.yml
release_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
candidate_full_env="$(mktemp "${full_env}.candidate.XXXXXX")"
candidate_orchestrator_env="$(mktemp "${orchestrator_env}.candidate.XXXXXX")"
rendered_compose="$(mktemp)"
cleanup_candidates() {
  [[ -z "${candidate_full_env:-}" ]] || rm -f "${candidate_full_env}"
  [[ -z "${candidate_orchestrator_env:-}" ]] || rm -f "${candidate_orchestrator_env}"
  [[ -z "${rendered_compose:-}" ]] || rm -f "${rendered_compose}"
}
trap cleanup_candidates EXIT
cp -p "${full_env}" "${candidate_full_env}"
cp -p "${orchestrator_env}" "${candidate_orchestrator_env}"
test "$(stat -c '%a:%u:%g' "${candidate_full_env}")" = \
  "$(stat -c '%a:%u:%g' "${full_env}")"
test "$(stat -c '%a:%u:%g' "${candidate_orchestrator_env}")" = \
  "$(stat -c '%a:%u:%g' "${orchestrator_env}")"

bash infra/release/update-env-value.sh "${candidate_full_env}" USE_PROJECT_MEMORY false
bash infra/release/update-env-value.sh \
  "${candidate_full_env}" ACCEPTANCE_GAUNTLET_REFERENCE_GATE false
bash infra/release/update-env-value.sh \
  "${candidate_full_env}" REFERENCE_CEILING_ENFORCED false
bash infra/release/update-env-value.sh \
  "${candidate_full_env}" DEV_GENERATION_TELEGRAM_REPORTS true
bash infra/release/update-env-value.sh \
  "${candidate_full_env}" OMNIA_RELEASE_SHA "${RELEASE_SHA}"
bash infra/release/update-env-value.sh \
  "${candidate_orchestrator_env}" OMNIA_RELEASE_SHA "${RELEASE_SHA}"

# Read the newly rotated bot token and the approved fixed group id from the
# operator terminal. stdin mode keeps both values out of argv and shell history.
# Do not enable xtrace around this block.
set +x
IFS= read -r -s -p 'New Telegram bot token: ' telegram_bot_token </dev/tty
printf '\n' >/dev/tty
[[ "${telegram_bot_token}" =~ ^[0-9]{8,12}:[A-Za-z0-9_-]{30,}$ ]]
printf '%s\n' "${telegram_bot_token}" \
  | bash infra/release/update-env-value.sh \
    "${candidate_full_env}" TELEGRAM_BOT_TOKEN -
unset telegram_bot_token
IFS= read -r -s -p 'Approved negative Telegram group id: ' telegram_chat_id </dev/tty
printf '\n' >/dev/tty
[[ "${telegram_chat_id}" =~ ^-[1-9][0-9]*$ ]]
printf '%s\n' "${telegram_chat_id}" \
  | bash infra/release/update-env-value.sh \
    "${candidate_full_env}" TELEGRAM_CHAT_ID -
```

Render and assert the dark policy on the production host:

```bash
bash infra/release/test-compose-policy.sh
chmod 600 "${rendered_compose}"
docker compose --env-file "${candidate_full_env}" -f "${compose_file}" config --format json \
  >"${rendered_compose}"
for service_name in api worker; do
  test "$(jq -r --arg service "${service_name}" '.services[$service].environment.USE_PROJECT_MEMORY' "${rendered_compose}")" = false
  test "$(jq -r --arg service "${service_name}" '.services[$service].environment.ACCEPTANCE_GAUNTLET_REFERENCE_GATE' "${rendered_compose}")" = false
  test "$(jq -r --arg service "${service_name}" '.services[$service].environment.REFERENCE_CEILING_ENFORCED' "${rendered_compose}")" = false
  test "$(jq -r --arg service "${service_name}" '.services[$service].environment.DEV_GENERATION_TELEGRAM_REPORTS' "${rendered_compose}")" = true
  test "$(jq -r --arg service "${service_name}" '.services[$service].environment.OMNIA_RELEASE_SHA' "${rendered_compose}")" = "${RELEASE_SHA}"
done
report_service='generation-report-worker'
test "$(jq -r --arg service "${report_service}" '.services[$service].environment.DEV_GENERATION_TELEGRAM_REPORTS' "${rendered_compose}")" = true
test "$(jq -r --arg service "${report_service}" '.services[$service].environment.TELEGRAM_CHAT_ID' "${rendered_compose}")" = "${telegram_chat_id}"
test "$(jq -r --arg service "${report_service}" '.services[$service].environment.OMNIA_RELEASE_SHA' "${rendered_compose}")" = "${RELEASE_SHA}"
jq -e --arg service "${report_service}" \
  '.services[$service].environment.TELEGRAM_BOT_TOKEN
   | type == "string" and test("^[0-9]{8,12}:[A-Za-z0-9_-]{30,}$")' \
  "${rendered_compose}" >/dev/null
for required_name in DATABASE_URL MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY MINIO_BUCKET_PREVIEWS; do
  jq -e --arg service "${report_service}" --arg name "${required_name}" \
    '.services[$service].environment[$name] | type == "string" and length > 0' \
    "${rendered_compose}" >/dev/null
done
unset telegram_chat_id
test "$(jq -r '.services.web.environment.OMNIA_RELEASE_SHA' "${rendered_compose}")" = "${RELEASE_SHA}"
```

Build revision-tagged images without moving the live `:prod` tags:

```bash
web_api_url="$(jq -er '.services.web.build.args.NEXT_PUBLIC_API_URL' "${rendered_compose}")"
web_ws_url="$(jq -er '.services.web.build.args.NEXT_PUBLIC_WS_URL' "${rendered_compose}")"
legal_version="$(jq -er '.services.web.build.args.NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION' "${rendered_compose}")"
docker build -t "omnia-api:${RELEASE_SHA}" apps/api
docker build \
  --build-arg "NEXT_PUBLIC_API_URL=${web_api_url}" \
  --build-arg "NEXT_PUBLIC_WS_URL=${web_ws_url}" \
  --build-arg NEXT_PUBLIC_USE_MOCKS=false \
  --build-arg "NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION=${legal_version}" \
  -t "omnia-web:${RELEASE_SHA}" apps/web
docker image inspect "omnia-api:${RELEASE_SHA}" "omnia-web:${RELEASE_SHA}" \
  --format '{{.Id}} {{index .RepoTags 0}}'
rm -f "${rendered_compose}"
rendered_compose=""
```

At this point the live environment files and live image tags are still
untouched. Any failure removes only the candidate files through the active
trap. Continue to section 6 in the same shell so the candidate paths remain
available.

## 6. Roll out in dependency order

Move the reviewed images to the Compose tags only after both builds succeed.
API starts first and applies the additive migration, then worker, web, and the
host orchestrator:

```bash
compose_file=apps/llm-gateway/deploy/full/docker-compose.yml
full_env=/opt/omnia/apps/llm-gateway/deploy/full/.env
orchestrator_env=/opt/omnia/apps/orchestrator/.env
export FULL_ENV_BACKUP="${full_env}.pre-release-${release_timestamp}"
export ORCHESTRATOR_ENV_BACKUP="${orchestrator_env}.pre-release-${release_timestamp}"
cp -p "${full_env}" "${FULL_ENV_BACKUP}"
cp -p "${orchestrator_env}" "${ORCHESTRATOR_ENV_BACKUP}"
test "$(stat -c '%a:%u:%g' "${FULL_ENV_BACKUP}")" = \
  "$(stat -c '%a:%u:%g' "${full_env}")"
test "$(stat -c '%a:%u:%g' "${ORCHESTRATOR_ENV_BACKUP}")" = \
  "$(stat -c '%a:%u:%g' "${orchestrator_env}")"

# Persist every rollback input before changing live configuration or image tags.
# Keep the reader outside the Git checkout so rollback can be resumed even after
# checking out a revision that predates this release tooling.
export ROLLBACK_POINTER=/opt/omnia-runtime/releases/pending-rollback.json
rollback_manifest_tool=/opt/omnia-runtime/releases/rollback-manifest.sh
rollback_env_updater=/opt/omnia-runtime/releases/update-env-value.sh
install -m 700 infra/release/rollback-manifest.sh "${rollback_manifest_tool}"
install -m 700 infra/release/update-env-value.sh "${rollback_env_updater}"
bash "${rollback_manifest_tool}" write \
  "${ROLLBACK_POINTER}" \
  "${RELEASE_RECORD}" \
  "${FULL_ENV_BACKUP}" \
  "${ORCHESTRATOR_ENV_BACKUP}" \
  "${RELEASE_SHA}" \
  "${ROLLBACK_SHA}"

mv -f "${candidate_full_env}" "${full_env}"
candidate_full_env=""
docker tag "omnia-api:${RELEASE_SHA}" omnia-api:prod
docker tag "omnia-web:${RELEASE_SHA}" omnia-web:prod

docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps api
api_ready=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1:8200/api/health >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
if [[ "${api_ready}" != true ]]; then
  docker compose --env-file "${full_env}" -f "${compose_file}" logs --tail=200 api
  exit 1
fi
docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps worker
docker compose --env-file "${full_env}" -f "${compose_file}" \
  up -d --no-deps generation-report-worker
test "$(docker inspect --format '{{.State.Running}}' \
  omnia-prod-generation-report-worker)" = true
test "$(docker inspect --format '{{.Image}}' \
  omnia-prod-generation-report-worker)" = \
  "$(docker image inspect --format '{{.Id}}' "omnia-api:${RELEASE_SHA}")"
docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps web

mv -f "${candidate_orchestrator_env}" "${orchestrator_env}"
candidate_orchestrator_env=""
cd /opt/omnia/apps/orchestrator
uv sync --frozen
sudo systemctl restart omnia-orchestrator
cd /opt/omnia
trap - EXIT
```

The two `.pre-release-*` files contain secrets and must remain readable only by
their original owner. The permission-protected rollback pointer contains paths
and revisions, but no secret values. Keep all three files through the
release/rollback decision. After the release is fully accepted, read the exact
backup paths from the pointer, remove those two backups, and finally remove
`/opt/omnia-runtime/releases/pending-rollback.json` and its adjacent
`rollback-manifest.sh` and `update-env-value.sh`; the encrypted backup from
section 3 is the long-term recovery artifact.

## 7. Require exact health and canary proof

All four runtime identities must equal `RELEASE_SHA` locally and off-host:

```bash
fetch_health() {
  local url="$1"
  local payload
  for _attempt in $(seq 1 60); do
    if payload="$(curl -fsS --max-time 5 "${url}")"; then
      printf '%s' "${payload}"
      return 0
    fi
    sleep 2
  done
  return 1
}

web="$(fetch_health http://127.0.0.1:3100/web-health)" || {
  docker logs --tail=200 omnia-prod-web
  exit 1
}
api="$(fetch_health http://127.0.0.1:8200/api/health)" || {
  docker logs --tail=200 omnia-prod-api
  exit 1
}
orchestrator="$(fetch_health http://127.0.0.1:8003/health)" || {
  sudo journalctl -u omnia-orchestrator -n 200 --no-pager
  exit 1
}
test "$(jq -r .release_sha <<<"${web}")" = "${RELEASE_SHA}"
test "$(jq -r .release_sha <<<"${api}")" = "${RELEASE_SHA}"
test "$(jq -r .dependencies.worker_release_sha <<<"${api}")" = "${RELEASE_SHA}"
test "$(jq -r .release_sha <<<"${orchestrator}")" = "${RELEASE_SHA}"

public_web="$(fetch_health https://constructor.lead-generator.ru/web-health)"
public_api="$(fetch_health https://constructor.lead-generator.ru/api/health)"
test "$(jq -r .release_sha <<<"${public_web}")" = "${RELEASE_SHA}"
test "$(jq -r .release_sha <<<"${public_api}")" = "${RELEASE_SHA}"
test "$(jq -r .dependencies.worker_release_sha <<<"${public_api}")" = "${RELEASE_SHA}"
test "$(jq -r .dependencies.orchestrator_release_sha <<<"${public_api}")" = "${RELEASE_SHA}"
```

Set the protected GitHub variable `PRODUCTION_EXPECTED_RELEASE_SHA` to the exact
release only with the owner's production approval, dispatch the cheap smoke,
then dispatch the paid disposable canary and watch both runs:

```bash
test "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "${RELEASE_SHA}"
gh workflow run production-smoke.yml --ref main
gh run watch "$(gh run list --workflow production-smoke.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
gh workflow run production-generation-canary.yml --ref main
gh run watch "$(gh run list --workflow production-generation-canary.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

With the protected canary credentials already loaded into the operator
environment, run the observer acceptance. The script never reads Telegram
credentials or chat history; its output contains only run lifecycle metadata.

```bash
umask 077
acceptance_output="${RELEASE_RECORD}/dev-generation-telegram-acceptance.json"
cd /opt/omnia/apps/api
DEV_TELEGRAM_ACCEPTANCE_CANCEL=false \
  uv run python scripts/dev_generation_telegram_acceptance.py \
  >"${acceptance_output}"
cd /opt/omnia
jq -e '
  keys == ["cleanup", "runs"] and
  .cleanup == true and
  (.runs | length == 2) and
  all(.runs[];
    (keys == ["finished_at", "mode", "preview", "run_id", "snapshot", "started_at", "terminal_status"]) and
    .terminal_status == "completed" and .snapshot == true and .preview == true)
' "${acceptance_output}" >/dev/null
```

In the approved team group, a human must confirm exactly those two run ids each
have one start message containing the exact user-authored canary message and no
system/effective prompt, followed by a threaded successful finish and PNG
preview. Do not use `getUpdates`, bot update history, or Telegram chat scraping
as a delivery queue or automated proof.
Optionally repeat with `DEV_TELEGRAM_ACCEPTANCE_CANCEL=true`; the third record
must be `cancelled` with `snapshot=false` and `preview=false`, and its threaded
Telegram finish must say that it was cancelled.

Keep `USE_PROJECT_MEMORY=false`. Enabling memory globally is a separate approved
configuration release after its own canary evidence.

## 8. Rollback

Rollback immediately on health, migration/startup, generation, preview, or
cleanup failure. Keep migration `0047` applied: it is additive. Never run an
Alembic downgrade.

```bash
set -euo pipefail
cd /opt/omnia

# Load the complete rollback state from disk. No variables from the failed
# rollout shell are required.
rollback_pointer=/opt/omnia-runtime/releases/pending-rollback.json
rollback_manifest_tool=/opt/omnia-runtime/releases/rollback-manifest.sh
rollback_env_updater=/opt/omnia-runtime/releases/update-env-value.sh
RELEASE_RECORD="$(bash "${rollback_manifest_tool}" read "${rollback_pointer}" release_record)"
FULL_ENV_BACKUP="$(bash "${rollback_manifest_tool}" read "${rollback_pointer}" full_env_backup)"
ORCHESTRATOR_ENV_BACKUP="$(bash "${rollback_manifest_tool}" read "${rollback_pointer}" orchestrator_env_backup)"
manifest_release_sha="$(bash "${rollback_manifest_tool}" read "${rollback_pointer}" release_sha)"
manifest_rollback_sha="$(bash "${rollback_manifest_tool}" read "${rollback_pointer}" rollback_sha)"
if [[ -n "${RELEASE_SHA:-}" ]]; then
  test "${RELEASE_SHA}" = "${manifest_release_sha}"
fi
if [[ -n "${ROLLBACK_SHA:-}" ]]; then
  test "${ROLLBACK_SHA}" = "${manifest_rollback_sha}"
fi
RELEASE_SHA="${manifest_release_sha}"
ROLLBACK_SHA="${manifest_rollback_sha}"
test -d "${RELEASE_RECORD}"
test -f "${FULL_ENV_BACKUP}"
test -f "${ORCHESTRATOR_ENV_BACKUP}"
test "$(cat "${RELEASE_RECORD}/live-git-sha.txt")" = "${ROLLBACK_SHA}"

# Quiesce before changing any runtime. This query must return no rows.
active_generations="$(docker exec omnia-prod-postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select status,count(*) from generation_runs
   where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"')
   group by status;"')"
test -z "${active_generations}"

full_env=/opt/omnia/apps/llm-gateway/deploy/full/.env
orchestrator_env=/opt/omnia/apps/orchestrator/.env
rollback_full_candidate="$(mktemp "${full_env}.rollback.XXXXXX")"
rollback_orchestrator_candidate="$(mktemp "${orchestrator_env}.rollback.XXXXXX")"
cleanup_rollback_candidates() {
  [[ -z "${rollback_full_candidate:-}" ]] || rm -f "${rollback_full_candidate}"
  [[ -z "${rollback_orchestrator_candidate:-}" ]] || \
    rm -f "${rollback_orchestrator_candidate}"
}
trap cleanup_rollback_candidates EXIT
cp -p "${FULL_ENV_BACKUP}" "${rollback_full_candidate}"
cp -p "${ORCHESTRATOR_ENV_BACKUP}" "${rollback_orchestrator_candidate}"
bash "${rollback_env_updater}" \
  "${rollback_full_candidate}" USE_PROJECT_MEMORY false
bash "${rollback_env_updater}" \
  "${rollback_full_candidate}" OMNIA_RELEASE_SHA "${ROLLBACK_SHA}"
bash "${rollback_env_updater}" \
  "${rollback_orchestrator_candidate}" OMNIA_RELEASE_SHA "${ROLLBACK_SHA}"

rollback_api_image="$(tr -d '\n' <"${RELEASE_RECORD}/api-image-id.txt")"
rollback_web_image="$(tr -d '\n' <"${RELEASE_RECORD}/web-image-id.txt")"
[[ "${rollback_api_image}" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "${rollback_web_image}" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image inspect "${rollback_api_image}" "${rollback_web_image}" >/dev/null

# This container may not exist on the rollback revision. Remove it by stable
# container name while the current release is still checked out, then restore
# the protected full environment before switching to older Compose code.
docker rm -f omnia-prod-generation-report-worker >/dev/null 2>&1 || true
mv -f "${rollback_full_candidate}" "${full_env}"
rollback_full_candidate=""
git checkout --detach "${ROLLBACK_SHA}"
test "$(git rev-parse HEAD)" = "${ROLLBACK_SHA}"
docker tag "${rollback_api_image}" omnia-api:prod
docker tag "${rollback_web_image}" omnia-web:prod
compose_file=apps/llm-gateway/deploy/full/docker-compose.yml
docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps api

api_ready=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1:8200/api/health >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
if [[ "${api_ready}" != true ]]; then
  docker compose --env-file "${full_env}" -f "${compose_file}" logs --tail=200 api
  exit 1
fi

docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps worker
docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps web
mv -f "${rollback_orchestrator_candidate}" "${orchestrator_env}"
rollback_orchestrator_candidate=""
cd /opt/omnia/apps/orchestrator
uv sync --frozen
sudo systemctl restart omnia-orchestrator
cd /opt/omnia
trap - EXIT

fetch_rollback_health() {
  local url="$1"
  local payload
  for _attempt in $(seq 1 60); do
    if payload="$(curl -fsS --max-time 5 "${url}")"; then
      printf '%s' "${payload}"
      return 0
    fi
    sleep 2
  done
  return 1
}

rollback_web="$(fetch_rollback_health http://127.0.0.1:3100/web-health)" || {
  docker logs --tail=200 omnia-prod-web
  exit 1
}
rollback_api="$(fetch_rollback_health http://127.0.0.1:8200/api/health)" || {
  docker logs --tail=200 omnia-prod-api
  exit 1
}
rollback_orchestrator="$(fetch_rollback_health http://127.0.0.1:8003/health)" || {
  sudo journalctl -u omnia-orchestrator -n 200 --no-pager
  exit 1
}
for actual_sha in \
  "$(jq -r '.release_sha // "unknown"' <<<"${rollback_web}")" \
  "$(jq -r '.release_sha // "unknown"' <<<"${rollback_api}")" \
  "$(jq -r '.dependencies.worker_release_sha // "unknown"' <<<"${rollback_api}")" \
  "$(jq -r '.release_sha // "unknown"' <<<"${rollback_orchestrator}")"; do
  [[ "${actual_sha}" == "${ROLLBACK_SHA}" || "${actual_sha}" == unknown ]]
done

# Confirm rollback did not race a newly-started generation. This must remain empty.
active_generations="$(docker exec omnia-prod-postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select status,count(*) from generation_runs
   where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"')
   group by status;"')"
test -z "${active_generations}"
```

Repeat the public health checks. Update the expected GitHub revision to the
rollback revision only if all four identities equal `ROLLBACK_SHA`. A legacy
rollback image that predates release identity may report `unknown`; availability
recovery takes priority, and the monitor must remain visibly red until an
identity-aware release is restored. Preserve `RELEASE_RECORD`, the rollback
pointer and its external helper/updater bundle, and the two permission-protected
environment backups until the rollback is accepted.

## 9. Mandatory pre-public observer disable proof

This observer is development-only. Before public launch, perform a separately
approved configuration release at an exact confirmed SHA. First repeat the
zero-active-generation query from section 4. Then disable the flag in a
permission-preserving candidate, validate all three consumers, and recreate the
API, preview worker, and report worker:

```bash
set -euo pipefail
cd /opt/omnia
active_generations="$(docker exec omnia-prod-postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select status,count(*) from generation_runs
   where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"')
   group by status;"')"
test -z "${active_generations}"

full_env=/opt/omnia/apps/llm-gateway/deploy/full/.env
compose_file=apps/llm-gateway/deploy/full/docker-compose.yml
disable_candidate="$(mktemp "${full_env}.disable-observer.XXXXXX")"
disable_backup="${full_env}.pre-public-observer-disable-$(date -u +%Y%m%dT%H%M%SZ)"
disable_render="$(mktemp)"
trap 'rm -f "${disable_candidate:-}" "${disable_render:-}"' EXIT
cp -p "${full_env}" "${disable_candidate}"
cp -p "${full_env}" "${disable_backup}"
test "$(stat -c '%a:%u:%g' "${disable_candidate}")" = \
  "$(stat -c '%a:%u:%g' "${full_env}")"
test "$(stat -c '%a:%u:%g' "${disable_backup}")" = \
  "$(stat -c '%a:%u:%g' "${full_env}")"
bash infra/release/update-env-value.sh \
  "${disable_candidate}" DEV_GENERATION_TELEGRAM_REPORTS false
chmod 600 "${disable_render}"
docker compose --env-file "${disable_candidate}" -f "${compose_file}" \
  config --format json >"${disable_render}"
for service_name in api worker generation-report-worker; do
  test "$(jq -r --arg service "${service_name}" \
    '.services[$service].environment.DEV_GENERATION_TELEGRAM_REPORTS' \
    "${disable_render}")" = false
done
rm -f "${disable_render}"
disable_render=""
mv -f "${disable_candidate}" "${full_env}"
disable_candidate=""
docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps api
api_ready=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1:8200/api/health >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
test "${api_ready}" = true
docker compose --env-file "${full_env}" -f "${compose_file}" up -d --no-deps worker
docker compose --env-file "${full_env}" -f "${compose_file}" \
  up -d --no-deps generation-report-worker
test "$(docker inspect --format '{{.State.Running}}' \
  omnia-prod-generation-report-worker)" = true
trap - EXIT
```

Run the disposable build/edit acceptance again and prove its validated run ids
have no observer rows. The output file is redacted by construction:

```bash
umask 077
pre_public_output="${RELEASE_RECORD}/pre-public-observer-disable.json"
cd /opt/omnia/apps/api
DEV_TELEGRAM_ACCEPTANCE_CANCEL=false \
  uv run python scripts/dev_generation_telegram_acceptance.py \
  >"${pre_public_output}"
cd /opt/omnia
jq -e '.cleanup == true and (.runs | length == 2)' \
  "${pre_public_output}" >/dev/null
run_id_sql=""
while IFS= read -r run_id; do
  [[ "${run_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
  run_id_sql+="${run_id_sql:+,}'${run_id}'::uuid"
done < <(jq -r '.runs[].run_id' "${pre_public_output}")
test -n "${run_id_sql}"
report_count="$(
  printf 'select count(*) from generation_telegram_reports where run_id in (%s);\n' \
    "${run_id_sql}" \
  | docker exec -i omnia-prod-postgres sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'
)"
test "${report_count}" = 0
```

A human must also confirm that neither run produced a Telegram message. Keep the
source default `false`, preserve the redacted output and zero-row result in the
release record, and retain the permission-protected env backup until the
configuration release is accepted. Re-enabling after public launch requires a
new privacy and operations review.

Never run `docker compose down -v` during release or rollback. It deletes
production volumes.
