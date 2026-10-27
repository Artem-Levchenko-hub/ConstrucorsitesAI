#!/usr/bin/env bash
# Выкатка ревизии main на прод (core = yleum.ru) одной командой, в порядке, который требуют
# двусторонние изменения api ↔ оркестратор:
#   .env identity → проверка отрендеренного compose → сборка api+web (nohup на core, опрос)
#   → api/worker/generation-worker → оркестратор core → rsync → оркестратор commerce → web
#   → PRODUCTION_EXPECTED_* → воркер биллинга в K3s commerce на тот же образ api → smoke.
#
# Использование (после ЗЕЛЁНОГО CI этой ревизии — красное не выкатываем):
#   infra/release/deploy-prod.sh <полный sha> [--no-web | --web-only] [--legal-version 2026-09-25]
#     --no-web            web не менялся: образ web не собирается и контейнер не трогается
#     --web-only          менялся только web: собирается и перезапускается один контейнер web;
#                         api/воркеры/оркестраторы/воркер биллинга не пересоздаются (безопасно во
#                         время живого прогона адаптации), OMNIA_RELEASE_SHA и expected api-переменные не меняются
#     --legal-version V   версия юридических документов: явно пишется в env воркера биллинга
#     --gateway           менялся apps/llm-gateway: пересобрать образ omnia-gateway:prod и поднять один контейнер
#                         шлюза (yleum-prod-gw) с --no-deps, проверить его health на :8101
#     --bootstrap-admission первый выпуск: старый API ещё не имеет durable drain;
#                         ingress + read-only gate → target migration/fence → обычный rollout
#     --repair-window N   окно починки адаптации (RESTORATION_ADAPTATION_REPAIR_SECONDS), по умолчанию 3600:
#                         пишется в .env платформы и сверяется в отрендеренном compose (защита от отката к умолчанию)
#                         (compose и api берут её из docker-compose.yml / config.py)
#     --finalize-owned-release NONCE --control-revision SHA --no-web
#                         завершить уже заменённый точный target под прежним владельцем;
#                         только исправленный publication gate берётся из control SHA
#     --resume-owned-release NONCE --control-revision SHA
#                         продолжить полный rollout прежнего target с существующими barriers/captures;
#                         только publication gate берётся из проверенного control SHA
#
# Замок: /opt/omnia/.deploy.lock на core — вторая выкатка одновременно не начнётся. Если
# сценарий умер и замок остался — посмотреть его содержимое (кто, когда, что) и удалить руками.
# Требования на Mac: ssh-алиас max-core (и commerce с него), gh (переменные репозитория),
# kubectl с ~/.kube/max-studio.yaml (воркер биллинга), git-чекаут репозитория.
set -euo pipefail

SHA="${1:?полный sha ревизии main}"; shift || true
WEB=1
API=1
LEGAL=""
REPAIR=3600
GW=0
BOOTSTRAP=0
FINALIZE_NONCE=""
RESUME_NONCE=""
CONTROL_SHA=""
REPAIR_OVERRIDE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-web) WEB=0 ;;
    --web-only) API=0 ;;
    --legal-version) LEGAL="${2:?}"; shift ;;
    --repair-window) REPAIR="${2:?}"; REPAIR_OVERRIDE=1; shift ;;
    --gateway) GW=1 ;;
    --bootstrap-admission) BOOTSTRAP=1 ;;
    --finalize-owned-release) FINALIZE_NONCE="${2:?}"; shift ;;
    --resume-owned-release) RESUME_NONCE="${2:?}"; shift ;;
    --control-revision) CONTROL_SHA="${2:?}"; shift ;;
    *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$FINALIZE_NONCE" ] || [ -n "$RESUME_NONCE" ] || [ -n "$CONTROL_SHA" ]; then
  if [ -z "$CONTROL_SHA" ] || [ "$API" != 1 ] || [ "$GW" != 0 ] || [ "$BOOTSTRAP" != 0 ] \
    || [ -n "$LEGAL" ] || [ "$REPAIR_OVERRIDE" != 0 ]; then
    echo "owned continuation requires owner/control flags without rollout overrides" >&2
    exit 2
  fi
  if { [ -n "$FINALIZE_NONCE" ] && [ -z "$RESUME_NONCE" ] && [ "$WEB" = 0 ]; } \
    || { [ -n "$RESUME_NONCE" ] && [ -z "$FINALIZE_NONCE" ] && [ "$WEB" = 1 ]; }; then
    :
  else
    echo "choose finalize with --no-web, or resume with full API/web rollout" >&2; exit 2
  fi
  [[ "$CONTROL_SHA" =~ ^[0-9a-f]{40}$ ]] \
    && [[ "${FINALIZE_NONCE:-$RESUME_NONCE}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
    echo "owned continuation requires a full control SHA and canonical existing owner UUID" >&2; exit 2;
  }
fi
if [ "$API" = 0 ] && { [ "$GW" = 1 ] || [ "$BOOTSTRAP" = 1 ]; }; then
  echo "--web-only нельзя сочетать с --gateway или --bootstrap-admission: нужен полный drain" >&2
  exit 2
fi
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "нужен полный 40-символьный sha, получено: $SHA" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RELEASE_DRIVER="${BASH_SOURCE[0]}"
LOG="/tmp/omnia-build-${SHA:0:12}.log"
SHORT="${SHA:0:8}"
say() { echo "== $* ($(date -u +%H:%M:%SZ))"; }
api_maintenance() {
  local launcher arguments
  launcher=$(git -C "$REPO" show "$SHA:infra/release/api-maintenance.py" | base64 -w0) || return
  arguments=$(python3 -c 'import base64,json,sys; print(base64.b64encode(json.dumps(sys.argv[1:]).encode()).decode())' "${API_RUNTIME_SHA:-}" "$@") || return
  # The API's real mount/network/env are retained, but no process is inserted
  # into its reader cgroup. Target bootstrap/resume bytes still arrive on stdin.
  ssh max-core "sudo -n python3 -c 'import base64; exec(compile(base64.b64decode(\"$launcher\"), \"release-api-maintenance\", \"exec\"))' '$arguments'"
}
ingress_drain() {
  git -C "$REPO" show "$SHA:infra/release/generation-ingress-drain.py" \
    | ssh max-core "sudo python3 - $1 $SHA"
}
publication_drain() {
  local gate_revision="${CONTROL_SHA:-$SHA}"
  if [ "$1" = core ]; then
    git -C "$REPO" show "$gate_revision:infra/release/generation-publication-drain.py" \
      | ssh max-core "sudo /opt/omnia/apps/orchestrator/.venv/bin/python -"
  else
    git -C "$REPO" show "$gate_revision:infra/release/generation-publication-drain.py" \
      | ssh max-core "ssh -o BatchMode=yes commerce 'sudo /opt/omnia/apps/orchestrator/.venv/bin/python -'"
  fi
}
quiescence_gate() {
  database_quiescence \
    && publication_drain core && publication_drain commerce
}
database_quiescence() {
  if [ "${READER_RETRY:-0}" = 1 ]; then
    reader_resume_check
  elif [ "$BOOTSTRAP" = 1 ]; then
    # Read-only target helper runs against legacy models without importing the
    # new fence model/table. Only the verified ingress barrier supplies exclusion.
    ingress_drain check || return
    git -C "$REPO" show "$SHA:apps/api/src/yleum_api/services/generation_deployment_drain.py" \
      | api_maintenance - bootstrap-check "$SHA"
  else
    api_maintenance -m yleum_api.services.generation_deployment_drain check "$SHA"
  fi
}

reader_control() {
  local host="$1" action="$2"; shift 2
  local command="sudo -n /opt/omnia/apps/orchestrator/.venv/bin/python - $action --host $host $*"
  if [ "$host" = core ]; then
    git -C "$REPO" show "$SHA:infra/release/generation-reader-inventory.py" | ssh max-core "$command"
  else
    git -C "$REPO" show "$SHA:infra/release/generation-reader-inventory.py" \
      | ssh max-core "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes commerce '$command'"
  fi
}

reader_prepare_retry() {
  local core_owner commerce_owner
  core_owner=$(reader_control core prepare-owner --release "$SHA") || return
  commerce_owner=$(reader_control commerce prepare-owner --release "$SHA") || return
  READER_NONCE=$(python3 - "$SHA" "${RESUME_NONCE:-}" "$core_owner" "$commerce_owner" <<'READER_OWNER'
import json, re, sys
from uuid import UUID
target = sys.argv[1]
owners = []
for raw in sys.argv[3:]:
    value = json.loads(raw)
    assert set(value) == {"owner"}
    owner = value["owner"]
    if owner is not None:
        assert set(owner) == {"version", "release_sha", "owner_nonce"}
        assert type(owner["version"]) is int and owner["version"] == 1
        assert owner["release_sha"] == target and re.fullmatch("[0-9a-f]{40}", target)
        assert str(UUID(owner["owner_nonce"])) == owner["owner_nonce"]
        owners.append(owner)
assert not owners or all(value == owners[0] for value in owners)
assert not sys.argv[2] or len(owners) == 2, "resume requires both existing owners"
print(owners[0]["owner_nonce"] if owners else "")
READER_OWNER
  ) || return
  if [ -n "${RESUME_NONCE:-}" ] && [ "$READER_NONCE" != "$RESUME_NONCE" ]; then
    echo "resume owner changed; captures retained" >&2; return 75
  fi
  READER_RETRY=0
  if [ -n "$READER_NONCE" ]; then
    # An interrupted first capture may lack its peer; never overwrite an
    # existing capture with partially replaced processes.
    reader_control core capture --release "$SHA" --owner-nonce "$READER_NONCE" || return
    reader_control commerce capture --release "$SHA" --owner-nonce "$READER_NONCE" || return
    READER_RETRY=1
  fi
}

reader_resume_check() {
  local core_context commerce_context context loader
  core_context=$(reader_control core capture-context --release "$SHA" --owner-nonce "$READER_NONCE") || return
  commerce_context=$(reader_control commerce capture-context --release "$SHA" --owner-nonce "$READER_NONCE") || return
  context=$(python3 - "$SHA" "$READER_NONCE" "$core_context" "$commerce_context" <<'READER_CONTEXT'
import base64, json, re, sys
owner = {"version": 1, "release_sha": sys.argv[1], "owner_nonce": sys.argv[2]}
result = dict(owner)
for host, raw in zip(("core", "commerce"), sys.argv[3:], strict=True):
    value = json.loads(raw)
    assert set(value) == {"owner", "capture_sha256"} and value["owner"] == owner
    assert re.fullmatch("[0-9a-f]{64}", value["capture_sha256"])
    result[host + "_capture_sha256"] = value["capture_sha256"]
print(base64.b64encode(json.dumps(result).encode()).decode())
READER_CONTEXT
  ) || return
  loader=$(git -C "$REPO" show "$SHA:infra/release/generation-reader-resume.py" | base64 -w0) || return
  # New process + exact committed package before any app imports. Installed
  # image contributes dependencies only; import/schema incompatibility denies.
  git -C "$REPO" archive --format=tar "$SHA" apps/api/src \
    | api_maintenance -c "import base64; exec(compile(base64.b64decode(\"$loader\"), \"target-reader-resume\", \"exec\"))" "$SHA" "$context"
}

reader_before() {
  [ "${READER_RETRY:-0}" != 1 ] || return 0
  local configured
  configured=$(ssh max-core "python3 -c 'import os; print(int(os.path.lexists(\"/var/lib/yleum-generation-readers/config.json\")))'")
  if [ "$configured" = 1 ]; then
    reader_control core verify-current --release "$API_RUNTIME_SHA"
  elif [ "$configured" = 0 ]; then
    # First reader-only rollout: no saved shape may rely on a reader which
    # cannot stand by. Neither a TTL nor a waiting status is quiescence.
    git -C "$REPO" show "$SHA:infra/release/check-active-generations.sh" \
      | ssh max-core 'sudo -n bash -s -- --require-no-recovery-state'
  else
    return 1
  fi
  READER_NONCE=$(python3 -c 'from uuid import uuid4; print(uuid4())')
  [[ "$READER_NONCE" =~ ^[0-9a-f-]{36}$ ]] || return 1
  reader_control core capture --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_control commerce capture --release "$SHA" --owner-nonce "$READER_NONCE"
}

reader_target() {
  local core_result commerce_result
  core_result=$(reader_control core verify-target --release "$SHA" --owner-nonce "$READER_NONCE") || return
  commerce_result=$(reader_control commerce verify-target --release "$SHA" --owner-nonce "$READER_NONCE") || return
  python3 - "$core_result" "$commerce_result" <<'READER_MATCH'
import json, sys
left, right = (json.loads(value) for value in sys.argv[1:])
assert left == right and left.get("verified") is True, "reader host snapshots differ"
print("both reader inventories and retirement verified")
READER_MATCH
}

gateway_capture_identity() {
  # Only opaque Docker IDs leave the host; never inspect environment values.
  GW_PREVIOUS_IDENTITY=$(ssh max-core "docker inspect yleum-prod-gw --format '{{.Id}} {{.Image}}'")
  read -r GW_PREVIOUS_CONTAINER_ID GW_PREVIOUS_IMAGE_ID <<< "$GW_PREVIOUS_IDENTITY"
  [[ "$GW_PREVIOUS_CONTAINER_ID" =~ ^[0-9a-f]{64}$ ]] && [[ "$GW_PREVIOUS_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "gateway identity capture failed; admission remains fenced" >&2; return 1;
  }
}
gateway_cutover_gate() {
  ssh max-core "python3 - '$GW_PREVIOUS_CONTAINER_ID' '$GW_PREVIOUS_IMAGE_ID'" <<'GATEWAY_CUTOVER_PY'
import json
import subprocess
import sys

old_container, old_image = sys.argv[1:]
format_metadata = ('{"id":{{json .Id}},"image":{{json .Image}},'
                   '"running":{{json .State.Running}},'
                   '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                   '"configured_image":{{json .Config.Image}},"name":{{json .Name}}}')

def command(args, include_stderr=False, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError("gateway cutover inspection failed; admission remains fenced")
    return (result.stdout + (result.stderr if include_stderr else "")).strip()

current = json.loads(command(['docker', 'inspect', 'yleum-prod-gw', '--format', format_metadata]))
expected_image = command(['docker', 'image', 'inspect', 'omnia-gateway:prod', '--format', '{{.Id}}'])
if not (current['running'] is True and current['id'] != old_container
        and current['image'] == expected_image
        and current['configured_image'] == 'omnia-gateway:prod'
        and current['service'] == 'gateway'):
    raise RuntimeError("gateway replacement identity is not confirmed; admission remains fenced")
running = command(['docker', 'ps', '--no-trunc', '--format', '{{.ID}}']).splitlines()
if old_container in running:
    raise RuntimeError("previous gateway container is still running; admission remains fenced")
for container in running:
    if container == current['id']:
        continue
    item = json.loads(command(['docker', 'inspect', container, '--format', format_metadata]))
    if (item['service'] == 'gateway' or item['image'] == old_image
            or item['configured_image'].startswith('omnia-gateway:')
            or item['name'] in ('/yleum-prod-gw', '/omnia-prod-gw')):
        raise RuntimeError("another gateway writer is running; admission remains fenced")
for image in {old_image, expected_image}:
    ancestors = command(['docker', 'ps', '--no-trunc', '--filter', 'ancestor=' + image,
                         '--format', '{{.ID}}']).splitlines()
    if any(container != current['id'] for container in ancestors):
        raise RuntimeError("another gateway image writer is running; admission remains fenced")
logs = command(['docker', 'logs', current['id']], include_stderr=True)
if 'startup.postgres_unavailable' in logs:
    raise RuntimeError("new gateway started without its database; admission remains fenced")
# Query the new container's configured gateway pool, not the API's DB connection.
# Its live /health also verifies that the serving process has initialized a pool.
program = '''import asyncio, json, urllib.request
from yleum_gateway.core.db import init_pool, get_pool, close_pool
async def table_constraints(connection, table):
    return await connection.fetch("SELECT c.contype::text AS kind,c.convalidated AS validated,pg_get_constraintdef(c.oid) AS definition,ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum,n) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.n) AS columns FROM pg_constraint c WHERE c.conrelid=$1::regclass", table)
async def verify():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=10) as response:
            if response.status != 200 or json.load(response).get("status") != "ok":
                raise RuntimeError("gateway health failed")
        await init_pool()
        async with get_pool().acquire() as connection, connection.transaction(readonly=True):
            if not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM alembic_version)"):
                raise RuntimeError("migration marker missing")
            if not await connection.fetchval("SELECT to_regclass('public.usage_settlements') IS NOT NULL"):
                raise RuntimeError("settlement table missing")
            # Feature gate accepts later migration heads without assuming head is forever 0074.
            await connection.fetch("SELECT id,user_id,billing_account_id,provider_scope,provider_request_id,receipt_hash,usage_id,wallet_charge_id,status FROM usage_settlements LIMIT 0")
            # New cost writers require the additive schema before admission resumes.
            provenance_column = await connection.fetchrow("SELECT data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='usage' AND column_name='cost_provenance'")
            if provenance_column is None or dict(provenance_column) != {'data_type': 'jsonb', 'is_nullable': 'YES', 'column_default': None}:
                raise RuntimeError("cost provenance schema missing")
            await connection.fetch("SELECT cost_provenance FROM usage LIMIT 0")
            # Alembic's check naming convention expands explicitly named checks.
            # Require the actual validated definitions, independent of their names.
            constraints = await table_constraints(connection, 'public.usage_settlements')
            receipt_unique = any(row['kind'] == 'u' and row['validated']
                and sorted(row['columns']) == ['provider_request_id','provider_scope','user_id']
                for row in constraints)
            checks = {row['definition'] for row in constraints
                      if row['kind'] == 'c' and row['validated']}
            expected_checks = {
                "CHECK ((status = ANY (ARRAY['settled'::text, 'free'::text, 'unpaid'::text, 'deferred'::text])))",
                "CHECK (((status = 'settled'::text) = (wallet_charge_id IS NOT NULL)))",
            }
            if not receipt_unique or not expected_checks.issubset(checks):
                raise RuntimeError("settlement constraints missing")
            await connection.fetch("SELECT run_id,is_free,version,created_at FROM generation_billing_policies LIMIT 0")
            await connection.fetch("SELECT run_id,user_id,billing_account_id,outcome,amount_rub,usage_ids,accepted_snapshot_id,receipt,created_at FROM generation_billing_intents LIMIT 0")
            await connection.fetch("SELECT run_id,next_attempt_at,settled_at FROM generation_billing_outbox LIMIT 0")
            for table, parent in [('generation_billing_policies','generation_runs'),
                                  ('generation_billing_intents','generation_runs'),
                                  ('generation_billing_outbox','generation_billing_intents')]:
                rows = await table_constraints(connection, table)
                if not any(row['kind']=='p' and row['validated'] and row['columns']==['run_id'] for row in rows):
                    raise RuntimeError("generation settlement identity missing")
                expected_fk = 'FOREIGN KEY (run_id) REFERENCES ' + parent + ('(run_id)' if parent=='generation_billing_intents' else '(id)') + ' ON DELETE CASCADE'
                if not any(row['kind']=='f' and row['validated'] and row['definition']==expected_fk for row in rows):
                    raise RuntimeError("generation settlement binding missing")
                if table=='generation_billing_intents':
                    definitions = {row['definition'] for row in rows if row['kind']=='c' and row['validated']}
                    if not {"CHECK ((outcome = ANY (ARRAY['billable'::text, 'waived'::text])))", "CHECK (((amount_rub >= (0)::numeric) AND ((outcome = 'billable'::text) OR (amount_rub = (0)::numeric))))", "CHECK (((outcome <> 'billable'::text) OR ((billing_account_id IS NOT NULL) AND (accepted_snapshot_id IS NOT NULL))))"}.issubset(definitions):
                        raise RuntimeError("generation settlement financial constraint missing")
                    for definition in ['FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE',
                                       'FOREIGN KEY (billing_account_id) REFERENCES billing_accounts(id) ON DELETE RESTRICT']:
                        if not any(row['kind']=='f' and row['validated'] and row['definition']==definition for row in rows):
                            raise RuntimeError("generation settlement account binding missing")
            if await connection.fetchval("SELECT attnotnull FROM pg_attribute WHERE attrelid='generation_billing_intents'::regclass AND attname='billing_account_id'"):
                raise RuntimeError("waived generation without wallet cannot be represented")
            # Unit-funded calls require the actual gateway reader to see their
            # immutable grants, reservations and prospective payment snapshots.
            await connection.fetch("SELECT id,billing_account_id,bucket,scope,units,external_ref,payment_id,subscription_id,period_start,period_end FROM generation_credit_grants LIMIT 0")
            await connection.fetch("SELECT run_id,billing_account_id,user_id,project_id,kind,state,units,allocations,accepted_snapshot_id,settled_at FROM generation_credit_reservations LIMIT 0")
            await connection.fetch("SELECT edit_units,edits_only,package_version FROM payments LIMIT 0")
            await connection.fetch("SELECT generation_initial_accepted FROM projects LIMIT 0")
            column_specs = {
                ('generation_credit_grants','units'): ('integer','NO'),
                ('generation_credit_reservations','units'): ('integer','NO'),
                ('generation_credit_reservations','allocations'): ('jsonb','NO'),
                ('payments','edit_units'): ('integer','NO'),
                ('payments','edits_only'): ('boolean','NO'),
                ('payments','package_version'): ('integer','YES'),
                ('projects','generation_initial_accepted'): ('boolean','NO'),
            }
            columns = await connection.fetch("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY($1::text[])", sorted({table for table, _ in column_specs}))
            actual_columns = {(row['table_name'],row['column_name']): (row['data_type'],row['is_nullable']) for row in columns}
            if any(actual_columns.get(key) != value for key, value in column_specs.items()):
                raise RuntimeError("unit billing column contract missing")
            unit_tables = [
                ('generation_credit_grants','id', {
                    "CHECK ((units >= 0))",
                    "CHECK ((bucket = ANY (ARRAY['initial'::text, 'edit'::text, 'edit_only'::text, 'flexible'::text])))",
                    "CHECK ((scope = ANY (ARRAY['free'::text, 'period'::text, 'package'::text])))",
                }, {
                    'FOREIGN KEY (billing_account_id) REFERENCES billing_accounts(id) ON DELETE RESTRICT',
                    'FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE RESTRICT',
                    'FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE RESTRICT',
                }),
                ('generation_credit_reservations','run_id', {
                    "CHECK ((units > 0))",
                    "CHECK ((kind = ANY (ARRAY['initial'::text, 'edit'::text])))",
                    "CHECK ((state = ANY (ARRAY['held'::text, 'consumed'::text, 'released'::text])))",
                    "CHECK (((jsonb_typeof(allocations) = 'array'::text) AND (jsonb_array_length(allocations) > 0)))",
                }, {
                    'FOREIGN KEY (billing_account_id) REFERENCES billing_accounts(id) ON DELETE RESTRICT',
                    'FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT',
                }),
            ]
            for table, primary, expected, expected_fks in unit_tables:
                rows = await table_constraints(connection, table)
                if not any(row['kind']=='p' and row['validated'] and row['columns']==[primary] for row in rows):
                    raise RuntimeError("unit billing identity missing")
                checks = {row['definition'] for row in rows if row['kind']=='c' and row['validated']}
                foreign_keys = {row['definition'] for row in rows if row['kind']=='f' and row['validated']}
                if not expected.issubset(checks) or not expected_fks.issubset(foreign_keys):
                    raise RuntimeError("unit billing financial constraints missing")
                if table == 'generation_credit_grants':
                    if not any(row['kind']=='u' and row['validated'] and row['columns']==['external_ref'] for row in rows):
                        raise RuntimeError("unit grant idempotency missing")
                    if not any(row['kind']=='c' and row['validated']
                               and set(row['columns'])=={'scope','payment_id','subscription_id','period_start','period_end'}
                               and 'period_end IS NOT NULL' in row['definition'] for row in rows):
                        raise RuntimeError("unit grant source binding missing")
                elif not any(row['kind']=='c' and row['validated']
                             and set(row['columns'])=={'state','accepted_snapshot_id','settled_at'} for row in rows):
                    raise RuntimeError("unit reservation terminal binding missing")
    except Exception:
        raise SystemExit("gateway settlement schema unavailable; admission remains fenced") from None
    finally:
        await close_pool()
asyncio.run(verify())
'''
command(['docker', 'exec', '-i', current['id'], 'python', '-'], input=program)
print("gateway writer retirement, runtime health and settlement schema confirmed")
GATEWAY_CUTOVER_PY
}

control_preflight() {
  local committed actual ci checkout_status
  [ "$(git -C "$REPO" rev-parse --verify HEAD)" = "$CONTROL_SHA" ] || {
    echo "control checkout HEAD differs from reviewed revision" >&2; return 75;
  }
  checkout_status=$(git -C "$REPO" status --porcelain=v1 --untracked-files=all --ignored) || return
  [ -z "$checkout_status" ] || {
    echo "control checkout is not clean; use an isolated exact checkout" >&2; return 75;
  }
  git -C "$REPO" merge-base --is-ancestor "$SHA" "$CONTROL_SHA"
  git -C "$REPO" merge-base --is-ancestor "$CONTROL_SHA" origin/main
  committed=$(git -C "$REPO" show "$CONTROL_SHA:infra/release/deploy-prod.sh" \
    | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
  actual=$(python3 -c 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$RELEASE_DRIVER")
  [ "$committed" = "$actual" ] || { echo "control driver differs from committed source" >&2; return 75; }
  git -C "$REPO" diff --name-only --no-renames -z "$SHA" "$CONTROL_SHA" \
    | python3 -c 'import sys
allowed = {b"infra/release/deploy-prod.sh", b"infra/release/generation-publication-drain.py",
           b"infra/release/test_generation_reader_deploy.py", b"infra/release/test_machine_quiescence_contract.py",
           b"infra/release/test_owned_release_finalize_contract.py",
           b"infra/release/test_generation_physical_drain_contract.py",
           b"apps/orchestrator/tests/test_inspector_upstream_contract.py",
           b"apps/api/tests/test_gateway_billing_cutover_release.py",
           b"infra/release/generation-drain.md"}
target, control = sys.argv[1:]
changed = sys.stdin.buffer.read().split(b"\0")
paths = changed[:-1]
assert changed[-1] == b"" and (paths or target == control) and all(path in allowed for path in paths), "control changes exceed reviewed operator scope"
' "$SHA" "$CONTROL_SHA"
  ci=$(cd "$REPO" && gh api --method GET 'repos/{owner}/{repo}/actions/workflows/ci.yml/runs' \
    -f "head_sha=$CONTROL_SHA" -f branch=main -f event=push -f per_page=100)
  python3 - "$CONTROL_SHA" "$ci" <<'CONTROL_CI'
import json, sys
revision, raw = sys.argv[1:]
runs = [run for run in json.loads(raw)["workflow_runs"]
        if run["head_sha"] == revision and run["head_branch"] == "main"
        and run["event"] == "push" and run["path"] == ".github/workflows/ci.yml"]
assert runs, "control main CI is unconfirmed"
latest = max(runs, key=lambda run: (run["created_at"], run["id"], run["run_attempt"]))
assert latest["status"] == "completed" and latest["conclusion"] == "success", "control main CI is not green"
print("control CI confirmed", latest["id"], "attempt", latest["run_attempt"])
CONTROL_CI
}

verify_expected_revisions() {
  local repository environment web
  repository=$(cd "$REPO" && gh variable list --json name,value)
  environment=$(cd "$REPO" && gh variable list --env production --json name,value)
  web=$(ssh max-core 'curl -fsS --max-time 10 https://yleum.ru/web-health')
  python3 - "$SHA" "$repository" "$environment" "$web" <<'EXPECTED_REVISIONS'
import json, re, sys
expected, raw_repository, raw_environment, raw_web = sys.argv[1:]
def variables(raw):
    rows = json.loads(raw)
    result = {row["name"]: row["value"] for row in rows}
    assert len(result) == len(rows), "duplicate expected revision"
    return result
repository, environment = variables(raw_repository), variables(raw_environment)
for role in ("API", "WORKER", "GENERATION_WORKER", "ORCHESTRATOR"):
    assert repository["PRODUCTION_EXPECTED_" + role + "_RELEASE_SHA"] == expected, "expected role revision differs"
assert repository["PRODUCTION_EXPECTED_RELEASE_SHA"] == expected
assert environment["PRODUCTION_EXPECTED_RELEASE_SHA"] == expected
web = json.loads(raw_web)
web_expected = repository["PRODUCTION_EXPECTED_WEB_RELEASE_SHA"]
assert re.fullmatch(r"[0-9a-f]{7,40}", web_expected)
assert web["status"] == "ok" and web["service"] == "web" and web["release_sha"] == web_expected, "unchanged web identity differs"
print("expected target revisions and unchanged web confirmed")
EXPECTED_REVISIONS
}

verify_billing_runtime() {
  local binding target_image api_image validated image producer_release
  binding=$(ssh max-core 'sudo -n cat /etc/max-studio/billing-image.ref')
  target_image=$(ssh max-core "docker image inspect omnia-api:$SHA --format '{\"id\":{{json .Id}},\"repo_digests\":{{json .RepoDigests}}}'")
  api_image=$(ssh max-core "docker inspect yleum-prod-api --format '{{.Image}}'")
  validated=$(python3 - "$SHA" "$binding" "$target_image" "$api_image" <<'BILLING_IMAGE'
import json, re, sys
expected, raw, raw_image, api_image = sys.argv[1:]
rows = [line.split("=", 1) for line in raw.splitlines()]
binding = dict(rows)
assert len(rows) == len(binding) == 3 and set(binding) == {"IMAGE_REF", "IMAGE_TAG", "RELEASE_SHA"}
producer = binding["RELEASE_SHA"]
assert producer == expected[:12] or re.fullmatch(r"[0-9]{12}", producer), "unsupported billing producer label"
assert binding["IMAGE_TAG"] == "registry.yleum.ru/platform/omnia-api:" + producer
assert re.fullmatch(r"registry\.yleum\.ru/platform/omnia-api@sha256:[0-9a-f]{64}", binding["IMAGE_REF"])
image = json.loads(raw_image)
assert re.fullmatch(r"sha256:[0-9a-f]{64}", api_image) and image["id"] == api_image, "billing target image differs from live API"
assert binding["IMAGE_REF"] in image["repo_digests"], "billing digest does not map to target image content"
if producer != expected[:12]:
    print("billing producer timestamp retained; target is verified by image content and full runtime revision", file=sys.stderr)
print(binding["IMAGE_REF"], producer)
BILLING_IMAGE
  )
  read -r image producer_release <<< "$validated"
  ssh max-core "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes commerce 'sudo -n python3 - $SHA $image $producer_release'" <<'BILLING_RUNTIME'
import hashlib, json, re, subprocess, sys
expected, image, producer = sys.argv[1:]
def kube(*arguments):
    result = subprocess.run(["k3s", "kubectl", "-n", "billing", *arguments, "-o", "json"],
                            check=True, capture_output=True, text=True, timeout=20)
    return json.loads(result.stdout)
deployment = kube("get", "deployment", "billing-worker")
assert deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
assert deployment["spec"]["replicas"] == 1
assert all(deployment["status"].get(field) == 1
           for field in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"))
template = deployment["spec"]["template"]
assert template["metadata"]["annotations"]["omnia.release_sha"] == producer
assert len(template["spec"]["containers"]) == 1
assert template["spec"]["containers"][0]["name"] == "worker"
assert template["spec"]["containers"][0]["image"] == image
pods = kube("get", "pods", "-l", "app=billing-worker")["items"]
assert len(pods) == 1 and not pods[0]["metadata"].get("deletionTimestamp")
pod = pods[0]
assert pod["metadata"]["annotations"]["omnia.release_sha"] == producer
assert len(pod["spec"]["containers"]) == 1 and pod["spec"]["containers"][0]["image"] == image
assert pod["status"]["phase"] == "Running"
assert any(condition["type"] == "Ready" and condition["status"] == "True"
           for condition in pod["status"]["conditions"])
statuses = pod["status"]["containerStatuses"]
assert len(statuses) == 1 and statuses[0]["name"] == "worker" and statuses[0]["ready"] is True
assert "running" in statuses[0]["state"]
status = statuses[0]
repository = "registry.yleum.ru/platform/omnia-api"
digest_pattern = r"sha256:[0-9a-f]{64}"
reference_pattern = re.escape(repository) + "@" + digest_pattern
assert re.fullmatch(reference_pattern, image), "invalid billing pinned reference"
actual = status["imageID"]
if actual.startswith("docker-pullable://"):
    actual = actual.removeprefix("docker-pullable://")
elif actual.startswith("containerd://"):
    actual = repository + "@" + actual.removeprefix("containerd://")
assert re.fullmatch(reference_pattern, actual), "invalid billing runtime reference"

def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, "duplicate cached image JSON key"
        result[key] = value
    return result

def content(digest, size=None):
    assert re.fullmatch(digest_pattern, digest), "invalid cached image digest"
    result = subprocess.run(["k3s", "ctr", "-n", "k8s.io", "content", "get", digest],
                            check=True, capture_output=True, timeout=20)
    raw = result.stdout
    assert 0 < len(raw) <= 2 * 1024**2 and (size is None or len(raw) == size), "cached image size differs"
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == digest, "cached image hash differs"
    document = json.loads(raw, object_pairs_hook=unique_keys)
    assert isinstance(document, dict), "cached image object required"
    return document, len(raw)

manifests = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}
indexes = {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}
configs = {"application/vnd.oci.image.config.v1+json", "application/vnd.docker.container.image.v1+json"}
layers = {"application/vnd.oci.image.layer.v1.tar", "application/vnd.oci.image.layer.v1.tar+gzip",
          "application/vnd.docker.image.rootfs.diff.tar", "application/vnd.docker.image.rootfs.diff.tar.gzip"}

def descriptor(value, allowed):
    assert isinstance(value, dict) and set(value) <= {"mediaType", "digest", "size", "platform", "annotations"}
    assert value["mediaType"] in allowed and re.fullmatch(digest_pattern, value["digest"])
    assert type(value["size"]) is int and value["size"] > 0
    return value["mediaType"], value["digest"], value["size"]

def selected_leaf(reference):
    digest = reference.split("@", 1)[1]
    root, size = content(digest)
    assert root["schemaVersion"] == 2 and not ({"artifactType", "subject"} & set(root))
    if root["mediaType"] in indexes:
        candidates = [entry for entry in root["manifests"]
                      if entry.get("platform") == {"os": "linux", "architecture": "amd64"}
                      and entry.get("annotations", {}).get("vnd.docker.reference.type") != "attestation-manifest"]
        assert len(candidates) == 1, "billing image platform selection is ambiguous or absent"
        return descriptor(candidates[0], manifests)
    return descriptor({"mediaType": root["mediaType"], "digest": digest, "size": size}, manifests)

# Different index attestations may describe the exact same executable platform leaf.
pinned_leaf, actual_leaf = selected_leaf(image), selected_leaf(actual)
assert pinned_leaf == actual_leaf, "billing runtime selects different image content"
leaf, _ = content(pinned_leaf[1], pinned_leaf[2])
assert leaf["schemaVersion"] == 2 and leaf["mediaType"] == pinned_leaf[0]
assert not ({"artifactType", "subject"} & set(leaf)), "billing executable manifest is an artifact"
_, config_id, config_size = descriptor(leaf["config"], configs)
assert isinstance(leaf["layers"], list) and leaf["layers"], "billing image layers absent"
for layer in leaf["layers"]:
    descriptor(layer, layers)
config, _ = content(config_id, config_size)
assert config["os"] == "linux" and config["architecture"] == "amd64", "billing config platform differs"
rootfs = config["rootfs"]
assert rootfs["type"] == "layers" and len(rootfs["diff_ids"]) == len(leaf["layers"])
assert all(re.fullmatch(digest_pattern, digest) for digest in rootfs["diff_ids"])

def cri(action, identity):
    result = subprocess.run(["k3s", "crictl", action, identity], check=True,
                            capture_output=True, text=True, timeout=20)
    return json.loads(result.stdout)

for reference in (image, actual):
    inspected = cri("inspecti", reference)
    assert inspected["status"]["id"] == config_id, "billing CRI image config differs"
    repo_digests = inspected["status"]["repoDigests"]
    assert isinstance(repo_digests, list) and image in repo_digests and actual in repo_digests
    image_spec = inspected["info"]["imageSpec"]
    assert image_spec["os"] == "linux" and image_spec["architecture"] == "amd64" and image_spec["rootfs"] == rootfs
assert status["image"] == config_id, "billing kube image config differs"
assert re.fullmatch(r"containerd://[0-9a-f]{64}", status["containerID"]), "invalid billing container identity"
container_id = status["containerID"].removeprefix("containerd://")
live = cri("inspect", container_id)["status"]
assert live["id"] == container_id and live["state"] == "CONTAINER_RUNNING" and live["metadata"]["name"] == "worker"
assert pod["metadata"]["namespace"] == "billing" and pod["metadata"]["uid"] and pod["metadata"]["name"]
for key, value in {"container.name": "worker", "pod.namespace": "billing",
                   "pod.uid": pod["metadata"]["uid"], "pod.name": pod["metadata"]["name"]}.items():
    assert live["labels"]["io.kubernetes." + key] == value, "billing CRI pod binding differs"
assert live["imageRef"] == actual and live["image"]["image"] == config_id
assert live["image"]["userSpecifiedImage"] == image, "billing CRI requested image differs"
print("billing target digest and single ready runtime confirmed")
BILLING_RUNTIME
}

resume_owned_preflight() {
  git -C "$REPO" merge-base --is-ancestor "$API_RUNTIME_SHA" "$SHA" || {
    echo "resume API runtime is not an ancestor of the owning target" >&2; return 75;
  }
  READER_NONCE="$RESUME_NONCE"
  READER_RETRY=1
  # Observe complete existing authority before any begin, prepare-owner or capture.
  ingress_drain check
  reader_control core verify-owner --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_control commerce verify-owner --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_resume_check
}

finalize_preflight() {
  [ "$API_RUNTIME_SHA" = "$SHA" ] || { echo "finalize API runtime differs from target" >&2; return 75; }
  READER_NONCE="$FINALIZE_NONCE"
  READER_RETRY=1
  # Read-only starting-state checks. Missing barriers/captures are never repaired.
  ingress_drain check
  reader_control core verify-owner --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_control commerce verify-owner --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_resume_check
  reader_target
  verify_expected_revisions
  verify_billing_runtime
}

finish_owned_release() {
  local readers_ready=0
  # Shared normal/finalize closing path. Every call observes the target again.
  verify_billing_runtime
  api_maintenance - "$SHA" <<'PY'
import json, os, sys, urllib.request
expected = sys.argv[1]
assert os.environ.get("OMNIA_RELEASE_SHA") == expected, "API process revision mismatch"
hosts = json.loads(os.environ.get("ORCHESTRATOR_HOSTS") or "[]")
assert len(hosts) == 2 and {host["name"] for host in hosts} == {"core", "commerce"}, "both controller roles must be verified"
urls = ["https://yleum.ru/api/health", *[host["url"].rstrip("/") + "/health" for host in hosts]]
for index, url in enumerate(urls):
    with urllib.request.urlopen(url, timeout=10) as response:
        body = json.load(response)
    assert body.get("release_sha") == expected, "runtime revision mismatch"
    assert body.get("status") in ("ok", "healthy"), "runtime health is not green"
    if index == 0:
        assert body["dependencies"]["billing_worker"] == "ok", "billing health is not green"
        for role in ("worker", "generation_worker", "orchestrator"):
            assert body["dependencies"][role + "_release_sha"] == expected, "dependency revision differs"
        assert body["dependencies"]["billing_worker_release_sha"] in {expected, expected[:12]}, "billing revision differs"
print("API, workers, billing and both controller runtime identities confirmed")
PY
  ssh max-core 'set -euo pipefail; curl -fsS --max-time 10 http://127.0.0.1:8101/health >/dev/null; logs=$(docker logs yleum-prod-gw 2>&1); if grep -q startup.postgres_unavailable <<< "$logs"; then echo "gateway database unavailable; admission remains fenced" >&2; exit 75; fi'
  if [ "$GW" = 1 ]; then
    gateway_cutover_gate
  fi
  for i in $(seq 1 18); do
    if reader_target; then readers_ready=1; break; fi
    sleep 5
  done
  [ "$readers_ready" = 1 ] || {
    echo "Reader inventory/retirement unconfirmed; drain and rollout inhibit retained." >&2
    return 75
  }
  ingress_drain check
  quiescence_gate
  reader_control core verify-owner --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_control commerce verify-owner --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_target
  ingress_drain end
  api_maintenance -m yleum_api.services.generation_deployment_drain end "$SHA"
  reader_control core clear --release "$SHA" --owner-nonce "$READER_NONCE"
  reader_control commerce clear --release "$SHA" --owner-nonce "$READER_NONCE"
}

say "выкатка $SHA api=$API web=$WEB gateway=$GW legal=${LEGAL:-по умолчанию образа} repair=$REPAIR"
git -C "$REPO" fetch -q origin
git -C "$REPO" merge-base --is-ancestor "$SHA" origin/main || { echo "ревизия $SHORT не лежит в origin/main — выкатываем только то, что в main" >&2; exit 2; }
if [ -n "$CONTROL_SHA" ]; then
  control_preflight
fi

say "core: замок выкатки"
ssh max-core "set -o noclobber; echo \"$(whoami)@$(hostname -s) $(date -u +%FT%TZ) $SHA\" > /opt/omnia/.deploy.lock" \
  || { echo "на core уже идёт выкатка (или остался замок): $(ssh max-core cat /opt/omnia/.deploy.lock)" >&2; exit 75; }
trap 'ssh max-core rm -f /opt/omnia/.deploy.lock >/dev/null 2>&1 || true' EXIT

API_RUNTIME_SHA=$(api_maintenance --describe-runtime)
[[ "$API_RUNTIME_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "API runtime revision is unconfirmed" >&2; exit 75; }
if [ -n "$FINALIZE_NONCE" ]; then
  finalize_preflight
  finish_owned_release
  say "готово: завершён прежний релиз $SHORT, control ${CONTROL_SHA:0:8}"
  exit 0
fi
if [ -n "${RESUME_NONCE:-}" ]; then
  resume_owned_preflight
fi

if [ "$API" = 1 ]; then
  say "scoped ingress barrier: новые изменения проектов временно закрыты"
  ingress_drain begin
  # Recover only this persisted release owner, before asking old/mixed readers
  # for quiescence. This never fabricates a CURRENT compatibility inventory.
  reader_prepare_retry
  say "закрываю новый admission; существующие запуски продолжают работу"
  # First installation needs the ingress bootstrap documented alongside this
  # script. Never silently bypass this when the old API lacks the drain module.
  if [ "$BOOTSTRAP" = 1 ]; then
    api_maintenance -c 'import importlib.util; assert importlib.util.find_spec("yleum_api.services.generation_deployment_drain") is None, "use normal fenced deployment"'
  else
    api_maintenance -m yleum_api.services.generation_deployment_drain begin "$SHA" || {
    echo "Не удалось установить durable drain. Первый выпуск: см. generation-drain.md; выкатка остановлена." >&2
    exit 75
  }
  fi
  drained=0
  for i in $(seq 1 180); do
    if quiescence_gate; then
      drained=1; break
    fi
    sleep 10
  done
  [ "$drained" = 1 ] || {
    echo "Есть незавершённые операции. Drain оставлен закрытым; не перезапускайте сервисы." >&2
    exit 75
  }
  reader_before
fi

say "core: ff-merge + идентичность релиза в compose .env"
ssh max-core "set -e; cd /opt/omnia; git fetch -q origin; git merge --ff-only $SHA >/dev/null; [ \"\$(git rev-parse HEAD)\" = \"$SHA\" ]; git rev-parse --short=12 HEAD; cd apps/llm-gateway/deploy/full; [ $API = 1 ] && sed -i -E 's#^(API_IMAGE=omnia-api:).*#\1$SHA#; s#^(OMNIA_RELEASE_SHA=).*#\1$SHA#' .env; [ $WEB = 1 ] && { sed -i -E 's#^(WEB_IMAGE=omnia-web:).*#\1$SHA#' .env; grep -q '^WEB_RELEASE_SHA=' .env && sed -i -E 's#^WEB_RELEASE_SHA=.*#WEB_RELEASE_SHA=$SHA#' .env || echo "WEB_RELEASE_SHA=$SHA" >> .env; }; grep -E '^(API_IMAGE|WEB_IMAGE|WEB_RELEASE_SHA|OMNIA_RELEASE_SHA|RESTORATION_ADAPTATION_REPAIR_SECONDS|MAX_GENERATION_DEADLINE_SECONDS|LEGAL_DOCUMENT_VERSION)=' .env | cut -c1-80"

say "core: окно починки адаптации в .env платформы"
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && printf '%s' '$REPAIR' | /opt/omnia/infra/release/update-env-value.sh .env RESTORATION_ADAPTATION_REPAIR_SECONDS - >/dev/null && grep -n '^RESTORATION_ADAPTATION_REPAIR_SECONDS=' .env"

if [ -n "$LEGAL" ]; then
  # Единственный источник — .env платформы: compose отдаёт его api, worker'ам и сборке web,
  # а шаг core-access воркера биллинга копирует его в /etc/max-studio/billing-worker.env
  # (тот файл он пересоздаёт целиком, поэтому править его напрямую бесполезно).
  say "core: версия документов в .env платформы"
  ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && printf '%s' '$LEGAL' | /opt/omnia/infra/release/update-env-value.sh .env LEGAL_DOCUMENT_VERSION - >/dev/null && grep -n '^LEGAL_DOCUMENT_VERSION=' .env"
fi

say "core: проверка отрендеренного compose (секреты входа только у api, окно починки, теги образов)"
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose -f docker-compose.yml -f docker-compose.hostdb.yml config --format json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)[\"services\"]
api = d[\"api\"][\"environment\"]; gw = d[\"generation-worker\"][\"environment\"]; wk = d[\"worker\"].get(\"environment\", {})
assert api.get(\"OAUTH_LOGIN_REDIRECT_BASE_URL\") == \"https://yleum.ru\", \"redirect base\"
assert not gw.get(\"YANDEX_ID_CLIENT_SECRET\") and not wk.get(\"YANDEX_ID_CLIENT_SECRET\"), \"secret leaked to a worker\"
assert not gw.get(\"VK_ID_CLIENT_SECRET\") and not wk.get(\"VK_ID_CLIENT_SECRET\"), \"secret leaked to a worker\"
assert api.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\") == \"$REPAIR\", api.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\")
assert gw.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\") == \"$REPAIR\", gw.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\")
if $API: assert d[\"api\"][\"image\"].endswith(\"$SHA\"), d[\"api\"][\"image\"]
if $WEB: assert d[\"web\"][\"image\"].endswith(\"$SHA\"), d[\"web\"][\"image\"]
if $WEB: assert d[\"web\"][\"environment\"].get(\"OMNIA_RELEASE_SHA\") == \"$SHA\", d[\"web\"][\"environment\"].get(\"OMNIA_RELEASE_SHA\")
legal = \"$LEGAL\"
if legal:
    assert api.get(\"LEGAL_DOCUMENT_VERSION\") == legal, api.get(\"LEGAL_DOCUMENT_VERSION\")
    assert (d[\"web\"][\"build\"].get(\"args\") or {}).get(\"NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION\") == legal
print(\"compose ok: yandex creds at api:\", bool(api.get(\"YANDEX_ID_CLIENT_SECRET\")), \"| repair window\", api.get(\"RESTORATION_ADAPTATION_REPAIR_SECONDS\"), \"| legal\", api.get(\"LEGAL_DOCUMENT_VERSION\"))
'"

TARGETS=""; [ $API = 1 ] && TARGETS="api"; [ $WEB = 1 ] && TARGETS="$TARGETS web"; TARGETS="${TARGETS# }"
say "core: сборка $TARGETS под nohup, опрос до готовности"
# A fresh directory binds completion to this launch, including retries of the same SHA.
BUILD_RUN=$(ssh max-core "mktemp -d /tmp/omnia-build-${SHA:0:12}.XXXXXX")
LOG="$BUILD_RUN/build.log"
BUILD_STATUS="$BUILD_RUN/exit-code"
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && (nohup bash -c 'set +e; docker compose -f docker-compose.yml -f docker-compose.hostdb.yml build $TARGETS; build_exit=\$?; printf \"%s\\n\" \"\$build_exit\" >$BUILD_STATUS.tmp && mv $BUILD_STATUS.tmp $BUILD_STATUS; exit \"\$build_exit\"' >$LOG 2>&1 </dev/null &) && echo сборка запущена"
IMAGES=""; [ $API = 1 ] && IMAGES="omnia-api:$SHA"; [ $WEB = 1 ] && IMAGES="$IMAGES omnia-web:$SHA"; IMAGES="${IMAGES# }"
state=""
for i in $(seq 1 60); do
  sleep 30
  state=$(ssh max-core "if [ -f $BUILD_STATUS ]; then build_exit=\$(cat $BUILD_STATUS); if [ \"\$build_exit\" != 0 ]; then echo failed; elif docker image inspect $IMAGES >/dev/null 2>&1; then echo built; else echo missing; fi; else echo building; fi")
  case "$state" in
    built) echo "образы собраны за ~$((i*30)) с"; break ;;
    failed|missing) echo "сборка завершилась ошибкой или без ожидаемых образов:"; ssh max-core "tail -40 $LOG"; exit 1 ;;
  esac
  [ $((i % 6)) -eq 0 ] && echo "  идёт сборка ($((i*30)) с): $(ssh max-core "tail -1 $LOG | cut -c1-100")"
done
[ "$state" = built ] || { echo "сборка не уложилась в 30 минут"; exit 1; }
ssh max-core "grep -E 'ERROR|error:' $LOG | tail -3 || true"

if [ $API = 1 ]; then
ingress_drain check
quiescence_gate
if [ "$BOOTSTRAP" = 1 ]; then
  say "первый выпуск: target image мигрирует platform DB и устанавливает durable fence"
  ssh max-core "set -e; cd /opt/omnia/apps/llm-gateway/deploy/full; docker compose -f docker-compose.yml -f docker-compose.hostdb.yml run --rm --no-deps --entrypoint /bin/sh api -ec '/app/.venv/bin/alembic upgrade head && /app/.venv/bin/python -m yleum_api.services.generation_deployment_drain begin $SHA && /app/.venv/bin/python -m yleum_api.services.generation_deployment_drain check $SHA'"
  quiescence_gate
fi
say "core: образ api читаем для uid 10001 (воркер биллинга в K3s)?"
ssh max-core "docker run --rm --user 10001:10001 --entrypoint sh omnia-api:$SHA -c 'n=\$(find /app/src /app/migrations /orchestrator/templates -type f ! -perm -o=r 2>/dev/null | wc -l); echo \"нечитаемых файлов: \$n\"; [ \"\$n\" = 0 ]'"

say "core: api worker generation-worker (api раньше оркестраторов)"
reader_control core install --release "$SHA" --ssh-user root
ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose -f docker-compose.yml -f docker-compose.hostdb.yml up -d --no-build --force-recreate api worker generation-worker 2>&1 | grep -E 'Started|Recreated|Error|error' | tail -5; for i in \$(seq 1 40); do s=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8200/api/health 2>/dev/null || true); [ \"\$s\" = 200 ] && { echo \"api health 200 с попытки \$i\"; break; }; sleep 3; done; docker compose -f docker-compose.yml -f docker-compose.hostdb.yml logs --since 3m api 2>&1 | grep -i -E 'running upgrade|RuntimeError|Traceback' | tail -3; docker tag omnia-api:$SHA omnia-api:prod"

say "оркестраторы: core, затем commerce (одна ревизия)"
BOOTSTRAP=0  # API now runs the target image and must enforce the persisted fence.
API_RUNTIME_SHA="$SHA"
# У commerce есть собственные служебные файлы и Git objects: --delete удалял их при выпуске.
ssh max-core "set -e; f=/opt/omnia/apps/orchestrator/.env; sed -i -E 's/^OMNIA_RELEASE_SHA=.*/OMNIA_RELEASE_SHA=$SHA/' \$f; cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen 2>&1 | tail -1 && { sudo systemctl restart yleum-orchestrator 2>/dev/null || sudo systemctl restart omnia-orchestrator; }; for i in \$(seq 1 30); do curl -sf 127.0.0.1:8003/health >/dev/null 2>&1 && break; sleep 2; done; echo core: \$(curl -s 127.0.0.1:8003/health); rsync -a --exclude '/.deploy.lock*' --exclude .venv --exclude .env --exclude '.env.before-*' --exclude node_modules --exclude .next --exclude __pycache__ --exclude '*.tsbuildinfo' /opt/omnia/ commerce:/opt/omnia/; ssh -o BatchMode=yes commerce \"set -e; sudo -n /opt/omnia/apps/orchestrator/.venv/bin/python /opt/omnia/infra/release/generation-reader-inventory.py install --host commerce --release $SHA; f=/opt/omnia/apps/orchestrator/.env; sed -i -E 's/^OMNIA_RELEASE_SHA=.*/OMNIA_RELEASE_SHA=$SHA/' \\\$f; cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen 2>&1 | tail -1 && { sudo systemctl restart yleum-orchestrator 2>/dev/null || sudo systemctl restart omnia-orchestrator; }; for i in \\\$(seq 1 30); do curl -sf 127.0.0.1:8003/health >/dev/null 2>&1 && break; sleep 2; done; echo commerce: \\\$(curl -s 127.0.0.1:8003/health)\""

fi

if [ $API = 1 ]; then
  say "commerce: canonical inspector relay (owned loopback only)"
  INSPECTOR_ASSET_SHA=$(ssh max-core "git -C /opt/omnia show $SHA:apps/api/src/yleum_api/static/omnia-inspector.js | sha256sum | cut -d' ' -f1")
  [[ "$INSPECTOR_ASSET_SHA" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid canonical inspector digest"; exit 1; }
  ssh max-core "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes commerce 'cd /opt/omnia/apps/orchestrator && sudo -n /opt/omnia/apps/orchestrator/.venv/bin/python /opt/omnia/infra/release/verify-inspector-relay.py --install --expected-sha $SHA --asset-sha256 $INSPECTOR_ASSET_SHA'"
fi

if [ $WEB = 1 ]; then
  say "core: web"
  ssh max-core "cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose -f docker-compose.yml -f docker-compose.hostdb.yml up -d --no-build --no-deps web 2>&1 | grep -E 'Started|Recreated|Error|error' | tail -2; docker tag omnia-web:$SHA omnia-web:prod; for i in \$(seq 1 30); do s=\$(curl -s -o /dev/null -w '%{http_code}' https://yleum.ru/web-health 2>/dev/null || true); [ \"\$s\" = 200 ] && { echo \"web-health 200 с попытки \$i\"; break; }; sleep 3; done"
fi

if [ $GW = 1 ]; then
  gateway_capture_identity
  say "core: шлюз моделей (gateway) — сборка и перезапуск одного контейнера"
  ssh max-core "set -euo pipefail; cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose -f docker-compose.yml -f docker-compose.hostdb.yml build gateway 2>&1 | { grep -E 'Built|ERROR|error' || true; } | tail -2; docker compose -f docker-compose.yml -f docker-compose.hostdb.yml up -d --no-build --no-deps --force-recreate gateway 2>&1 | { grep -E 'Started|Recreated|Error|error' || true; } | tail -2; for i in \$(seq 1 30); do s=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8101/health 2>/dev/null || true); [ \"\$s\" = 200 ] && { echo \"gateway health 200 с попытки \$i\"; break; }; sleep 2; done; docker inspect \$(docker inspect yleum-prod-gw >/dev/null 2>&1 && echo yleum-prod-gw || echo omnia-prod-gw) --format 'gateway image {{.Image}} started {{.State.StartedAt}}' | cut -c1-90"
fi

say "публичный health"
ssh max-core "curl -s https://yleum.ru/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"status\"], \"api\", d[\"release_sha\"][:8], \"orch\", d[\"dependencies\"].get(\"orchestrator_release_sha\",\"\")[:8], \"billing\", d[\"dependencies\"].get(\"billing_worker\"), {k:v for k,v in d[\"checks\"].items() if v!=\"ok\"})'; echo \"legal/config: \$(curl -s https://yleum.ru/api/legal/config | python3 -c 'import json,sys; print(json.load(sys.stdin).get(\"document_version\"))')\""

# Шлюз моделей мог подняться «в деградации»: 25.09 он потерял базу при старте
# (смена подсети в окне переименования), записал предупреждение, продолжил отвечать
# health 200 — и десять часов каждый вызов модели оплачивался у провайдера и не
# записывался в журнал расхода. Теперь это ловится на каждой волне, даже если сам
# шлюз в ней не пересобирался.
# Адреса оркестраторов в настройках платформы должны быть достижимы ИЗНУТРИ api,
# а не только с хоста. 26.09 они указывали на шлюз старой compose-сети
# (172.19.0.1): сеть стояла пустой, выглядела мусором, и её уборка увела адрес в
# никуда — платформа ушла в «деградацию» с непонятным «mixed» вместо ревизии.
# Проверка отсюда ловит любое такое расхождение на той же волне.
say "core: оркестраторы видны из контейнера api?"
api_maintenance - <<'PY'
import json, os, sys, urllib.request
raw = os.environ.get("ORCHESTRATOR_HOSTS") or "[]"
hosts = json.loads(raw) if raw.strip().startswith("[") else []
if not hosts:
    hosts = [{"name": "core", "url": os.environ.get("ORCHESTRATOR_URL", "")}]
bad = []
for h in hosts:
    url = (h.get("url") or "").rstrip("/")
    try:
        with urllib.request.urlopen(url + "/health", timeout=8) as r:
            body = json.loads(r.read().decode())
        print("  %-10s %s %s" % (h.get("name"), url, body.get("release_sha", "")[:12]))
    except Exception as exc:
        bad.append("%s (%s): %s" % (h.get("name"), url, exc))
if bad:
    print("ОРКЕСТРАТОР НЕ ВИДЕН ИЗ API: " + "; ".join(bad))
    print("Починка: сверить адреса в ORCHESTRATOR_HOSTS с текущей compose-сетью")
    print("(docker network inspect full_yleum-prod -f \"{{range .IPAM.Config}}{{.Gateway}}{{end}}\")")
    sys.exit(1)
PY

say "core: шлюз моделей не в деградации?"
ssh max-core 'set -e; code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8101/health || true); echo "gateway health: $code"; [ "$code" = 200 ] || { echo "ШЛЮЗ НЕ ОТВЕЧАЕТ"; exit 1; }; n=$(docker logs yleum-prod-gw 2>&1 | grep -c "startup.postgres_unavailable" || true); echo "startup.postgres_unavailable в текущем контейнере: $n"; [ "$n" = 0 ] || { echo "ШЛЮЗ РАБОТАЕТ БЕЗ БАЗЫ: списания теряются, вызовы моделей оплачиваются впустую. Починка: разрешить подсеть контейнера в ufw и pg_hba хостового PostgreSQL, затем docker compose -f docker-compose.yml -f docker-compose.hostdb.yml up -d --no-build --no-deps --force-recreate gateway"; exit 1; }'

say "github: ожидаемые ревизии (пять + общая)"
cd "$REPO"
VARS=""; [ $API = 1 ] && VARS="API WORKER GENERATION_WORKER ORCHESTRATOR"; [ $WEB = 1 ] && VARS="$VARS WEB"
for v in $VARS; do gh variable set "PRODUCTION_EXPECTED_${v}_RELEASE_SHA" --body "$SHA"; done
if [ $API = 1 ]; then
  gh variable set PRODUCTION_EXPECTED_RELEASE_SHA --body "$SHA"
  gh variable set PRODUCTION_EXPECTED_RELEASE_SHA --env production --body "$SHA"
fi
echo "переменные выставлены"

if [ $API = 1 ]; then
say "воркер биллинга (K3s commerce) → тот же образ api"
cd "$REPO/infra/max-k3s"
./commerce/10-billing-workloads.sh image 2>&1 | grep -E 'IMAGE_PUSHED|error' | tail -1
./commerce/10-billing-workloads.sh core-access 2>&1 | grep -E 'BILLING_ACCESS_DONE'
./commerce/10-billing-workloads.sh secret 2>&1 | grep -E 'configured|created|перезапущен'
./commerce/10-billing-workloads.sh apply 2>&1 | grep -E 'configured|unchanged' | head -2
sleep 30
./commerce/10-billing-workloads.sh verify 2>&1 | grep -v '^\s*$' | tail -3
if [ -n "$LEGAL" ]; then
  ./kube-tunnel.sh up >/dev/null 2>&1 || true
  export KUBECONFIG="$HOME/.kube/max-studio.yaml"
  echo "secret LEGAL_DOCUMENT_VERSION = $(kubectl --context max-commerce -n billing get secret billing-worker-env -o jsonpath='{.data.LEGAL_DOCUMENT_VERSION}' | base64 -d)"
  kubectl --context max-commerce -n billing exec deploy/billing-worker -- sh -c 'P=/app/.venv/bin/python; [ -x "$P" ] || P=python; "$P" -c "from yleum_api.core.config import get_settings; print(\"воркер: legal_document_version =\", get_settings().legal_document_version)"'
fi

fi

say "smoke"
cd "$REPO"
gh workflow run production-smoke.yml >/dev/null && echo "smoke запущен (gh run list --workflow production-smoke.yml --limit 1)"
if [ "$API" = 1 ]; then
  finish_owned_release
fi
say "готово: прод на $SHORT"
