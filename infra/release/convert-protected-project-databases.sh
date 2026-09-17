#!/usr/bin/env bash
# Convert protected MAX project databases back to ordinary project databases.
#
# Owner decision (17.09.2026): end-user data protection is out of scope. The
# protected mode added an omnia_runtime role, RLS policies and an omnia_guard
# schema to each project database, plus controller files next to the machine
# state. This script removes exactly those objects. Business rows are not
# touched: no table is dropped, truncated or rewritten.
#
# Run on the production host, as a user with sudo and docker, BEFORE the
# orchestrator that no longer reads data-policy.json is started. Each workspace
# must have no running project database or product container.
#
#   bash convert-protected-project-databases.sh <workspace-uuid>...
#
# Every step writes to a root-only record directory. Nothing is deleted:
# controller files are moved into the record, and a file-level archive of the
# stopped cluster is taken first.
set -euo pipefail

state_root="${STATE_ROOT:-/opt/omnia-runtime/state/project-machines}"
record_root="${RECORD_ROOT:-/opt/omnia-runtime/releases}"
# Dry runs point these at copies; production uses the defaults.
volume_prefix="${VOLUME_PREFIX:-omnia-machine-}"
postgres_image='postgres@sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50'
pgdata=/var/lib/postgresql/data

(($# > 0)) || { echo "usage: $0 <workspace-uuid>..." >&2; exit 2; }
record="$record_root/unprotect-projects-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 700 "$record"
echo "record: $record"

sql() {
  local container="$1"
  docker exec -i "$container" psql -X -qAt -v ON_ERROR_STOP=1 -h /tmp -U postgres -d postgres
}

for workspace in "$@"; do
  [[ "$workspace" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
    || { echo "invalid workspace id: $workspace" >&2; exit 2; }
  hex="${workspace//-/}"
  state="$state_root/$workspace"
  volume="$volume_prefix$hex-app-postgres-data"
  temp="omnia-unprotect-$hex"
  out="$record/$workspace"
  sudo install -d -m 700 "$out"
  echo "=== $workspace"

  sudo test -f "$state/data-policy.json" || { echo "not protected, skipping"; continue; }
  docker volume inspect "$volume" >/dev/null
  for running in "omnia-machine-$hex-project-postgres" "omnia-machine-$hex-dev" \
                 "omnia-machine-$hex-max-core"; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$running" 2>/dev/null || true)" == true ]]; then
      echo "refusing: $running is running; stop writers first" >&2
      exit 1
    fi
  done

  # 1. Archive the stopped cluster and the controller files.
  docker run --rm --network none --entrypoint tar \
    -v "$volume:/source:ro" "$postgres_image" -C /source -cf - . \
    | sudo tee "$out/app-postgres-data.tar" >/dev/null
  sudo sh -c "sha256sum '$out/app-postgres-data.tar' > '$out/app-postgres-data.tar.sha256'"
  sudo test -s "$out/app-postgres-data.tar"

  # 2. Start the cluster privately: no network, socket only.
  docker rm -f "$temp" >/dev/null 2>&1 || true
  docker run -d --name "$temp" --network none --user 70:70 --entrypoint postgres \
    -v "$volume:$pgdata" -e "PGDATA=$pgdata" "$postgres_image" \
    -D "$pgdata" -c listen_addresses= -c unix_socket_directories=/tmp >/dev/null
  trap 'docker rm -f "$temp" >/dev/null 2>&1 || true' EXIT
  for _ in $(seq 1 60); do
    docker exec "$temp" pg_isready -h /tmp -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$temp" pg_isready -h /tmp -U postgres >/dev/null

  # Row counts before, per table, to prove business data is unchanged.
  counts_sql="select format('%s.%s=%s', schemaname, relname,
    (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I',
      schemaname, relname), false, true, '')))[1]::text)
    from pg_stat_user_tables where schemaname not in ('omnia_guard') order by 1;"
  echo "$counts_sql" | sql "$temp" | sudo tee "$out/row-counts.before" >/dev/null

  # 3. Remove the protection objects in one transaction.
  sql "$temp" <<'SQL'
BEGIN;
SET LOCAL lock_timeout = '10s';
DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT DISTINCT n.nspname, c.relname
    FROM pg_policy p
    JOIN pg_class c ON c.oid = p.polrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE p.polname IN ('omnia_restore_actor', 'omnia_restore_limit')
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS omnia_restore_actor ON %I.%I', t.nspname, t.relname);
    EXECUTE format('DROP POLICY IF EXISTS omnia_restore_limit ON %I.%I', t.nspname, t.relname);
    -- Only tables the protection enrolled; any other app policy keeps RLS on.
    IF NOT EXISTS (SELECT FROM pg_policy q JOIN pg_class k ON k.oid = q.polrelid
                   JOIN pg_namespace m ON m.oid = k.relnamespace
                   WHERE m.nspname = t.nspname AND k.relname = t.relname) THEN
      EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY', t.nspname, t.relname);
      EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY', t.nspname, t.relname);
    END IF;
  END LOOP;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'omnia_runtime') THEN
    REASSIGN OWNED BY omnia_runtime TO postgres;
    DROP OWNED BY omnia_runtime;
    DROP ROLE omnia_runtime;
  END IF;
END $$;
DROP SCHEMA IF EXISTS omnia_guard CASCADE;
GRANT CONNECT ON DATABASE postgres TO PUBLIC;
GRANT USAGE ON SCHEMA public TO PUBLIC;
COMMIT;
SQL

  # 4. Verify: no protection objects left, same row counts, postgres keeps a password.
  echo "$counts_sql" | sql "$temp" | sudo tee "$out/row-counts.after" >/dev/null
  sudo cmp "$out/row-counts.before" "$out/row-counts.after"
  leftovers="$(sql "$temp" <<'SQL'
select 'role' where exists (select from pg_roles where rolname='omnia_runtime')
union all select 'schema' where exists (select from pg_namespace where nspname='omnia_guard')
union all select 'policy' where exists (select from pg_policy where polname like 'omnia_restore_%')
union all select 'no-postgres-password' where not exists
  (select from pg_authid where rolname='postgres' and rolpassword is not null);
SQL
)"
  if [[ -n "$leftovers" ]]; then
    echo "verification failed: $leftovers" >&2
    exit 1
  fi

  docker stop -t 30 "$temp" >/dev/null
  docker rm "$temp" >/dev/null
  trap - EXIT

  # 5. Retire controller files into the record (never delete).
  for name in data-policy.json data-runtime.cjs postgres-hba.conf \
              postgres-controller.conf initial-database.json data-key-binding.json; do
    if sudo test -e "$state/$name"; then
      sudo mv "$state/$name" "$out/$name"
    fi
  done
  echo "converted $workspace"
done
echo "done; record kept at $record"
