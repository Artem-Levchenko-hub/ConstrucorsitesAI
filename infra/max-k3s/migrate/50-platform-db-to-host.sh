#!/usr/bin/env bash
# Фаза 2 (разделение Core PostgreSQL): перенос базы платформы `omnia` из compose-контейнера
# omnia-prod-postgres на ХОСТОВЫЙ PostgreSQL 16 сервера core (поставлен remote/40-postgres.sh).
#
# Запускать НА CORE от root, из checkout /opt/omnia (каждый шаг идемпотентен и печатает, что сделал):
#   sudo bash infra/max-k3s/migrate/50-platform-db-to-host.sh <шаг>
#
#   precheck   — версии, место, доступность обоих Postgres, подсеть docker-сети; ничего не меняет
#   prepare    — роль+база на хосте, pg_hba/ufw для контейнеров, креды в /etc/max-studio; стек не трогает
#   freeze     — остановить писателей базы: api, worker, generation-worker, gateway (окно недоступности)
#   transfer   — pg_dump из контейнера → pg_restore в пустую хостовую базу (требует freeze)
#   verify     — сверка счётчиков всех таблиц и alembic_version между контейнером и хостом
#   switch     — .env: COMPOSE_FILE + PLATFORM_DATABASE_URL, поднять стек на хостовой базе, проверить
#                здоровье, остановить контейнер postgres (том остаётся — это путь отката)
#   status     — в каком режиме стек, кто подключён к какой базе, alembic_version с обеих сторон
#   rollback   — вернуть стек на контейнер (.env без host-строк, контейнер postgres запущен);
#                `rollback --with-data` сначала переносит хостовую базу обратно в контейнер
#   retire     — (через несколько дней после switch) удалить остановленный контейнер; том НЕ удаляет
#
# Порядок выката и что при этом с продом — в infra/max-k3s/migrate/README.md, раздел «Фаза 2б».
set -euo pipefail

STEP=${1:-}; shift || true
FULL=${FULL:-/opt/omnia/apps/llm-gateway/deploy/full}
ENV_FILE=${ENV_FILE:-$FULL/.env}
PLATFORM_CTR=${PLATFORM_CTR:-omnia-prod-postgres}
COMPOSE_NET=${COMPOSE_NET:-full_omnia-prod}
CREDS=${CREDS:-/etc/max-studio/platform-postgres.env}
HOST_PG_ENV=${HOST_PG_ENV:-/etc/max-studio/postgres.env}
WORK=${WORK:-/opt/omnia-runtime/migrate-in/platform-db}
PGVER=${PGVER:-16}
HBA=${HBA:-/etc/postgresql/$PGVER/main/pg_hba.conf}
WRITERS="api worker generation-worker gateway"
HOSTDB_COMPOSE="docker-compose.yml:docker-compose.hostdb.yml"

log()  { printf '\n\033[1;34m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[1;31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "нет команды $1"; }
envval() { sed -n "s/^$1=//p" "$2" | tail -1; }
upsert() { # upsert FILE KEY VALUE — ключ в .env, значение без экранирования (пароли без '#' и '$')
  local f=$1 k=$2 v=$3
  if grep -qE "^$k=" "$f"; then sed -i "s#^$k=.*#$k=$v#" "$f"; else printf '%s=%s\n' "$k" "$v" >> "$f"; fi
}
unset_key() { sed -i "/^$1=/d" "$2"; }

[ -n "$STEP" ] || { sed -n '2,22p' "$0"; exit 2; }
[ "$(id -u)" = 0 ] || die "запускать от root (sudo)"
[ -f "$ENV_FILE" ] || die "нет $ENV_FILE"

# --- параметры контейнерной базы (как их видит compose) ---
C_USER=$(envval POSTGRES_USER "$ENV_FILE"); C_USER=${C_USER:-omnia}
C_DB=$(envval POSTGRES_DB "$ENV_FILE");     C_DB=${C_DB:-omnia}
# --- параметры хостовой базы: те же имена, чтобы бэкапы и документация не переучивались ---
T_DB=${PLATFORM_PG_DB:-$C_DB}
T_ROLE=${PLATFORM_PG_USER:-$C_USER}
T_HOST=${PLATFORM_PG_HOST:-$( [ -f "$HOST_PG_ENV" ] && envval PG_HOST "$HOST_PG_ENV" || true)}
T_HOST=${T_HOST:-10.10.0.1}

host_mode() { grep -qE '^PLATFORM_DATABASE_URL=' "$ENV_FILE"; }
cpsql()  { docker exec -i "$PLATFORM_CTR" psql -X -v ON_ERROR_STOP=1 -U "$C_USER" -d "${1:-$C_DB}" -Atq; }
hpsql()  { (cd /tmp && sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d "${1:-$T_DB}" -Atq); }
compose() { (cd "$FULL" && docker compose "$@"); }
docker_subnet() { docker network inspect "$COMPOSE_NET" -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || true; }
container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$PLATFORM_CTR" 2>/dev/null || echo false)" = true ]; }

table_counts() { # table_counts <cpsql|hpsql> — «таблица<TAB>строк» для всех таблиц public, по алфавиту
  local runner=$1
  "$runner" <<'SQL' | while IFS= read -r t; do
SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1
SQL
    printf '%s\t%s\n' "$t" "$(printf 'SELECT count(*) FROM public.%s' "\"$t\"" | "$runner")"
  done
}

wait_http() { # wait_http URL SECONDS
  local url=$1 t=${2:-120} i=0
  until curl -fsS -m 3 "$url" >/dev/null 2>&1; do i=$((i+5)); [ "$i" -ge "$t" ] && return 1; sleep 5; done
}

case "$STEP" in
  precheck)
    log "1/6 инструменты"
    for c in docker pg_isready pg_dump pg_restore psql ufw; do need "$c"; done
    ver=$(docker compose version --short 2>/dev/null || echo 0); IFS=. read -r vmaj vmin _ <<<"${ver#v}"
    [ "${vmaj:-0}" -gt 2 ] || { [ "${vmaj:-0}" -eq 2 ] && [ "${vmin:-0}" -ge 24 ]; } || die "docker compose $ver < 2.24 — теги !override в docker-compose.hostdb.yml не сработают"
    echo "   docker compose $ver; pg_dump $(pg_dump --version | awk '{print $3}')"
    [ -f "$FULL/docker-compose.hostdb.yml" ] || die "нет $FULL/docker-compose.hostdb.yml — обновите checkout"

    log "2/6 хостовый PostgreSQL"
    pg_isready -q -h 127.0.0.1 || die "хостовый Postgres не отвечает на 127.0.0.1"
    hver=$(hpsql postgres <<<"SHOW server_version" | cut -d. -f1)
    echo "   host: $(hpsql postgres <<<'SELECT version()' | cut -c1-60)"
    echo "   слушает: $(hpsql postgres <<<'SHOW listen_addresses')"
    grep -q "$T_HOST" <<<"$(hpsql postgres <<<'SHOW listen_addresses')" || die "хостовый Postgres не слушает $T_HOST (remote/40-postgres.sh пишет listen_addresses)"

    log "3/6 контейнерный PostgreSQL ($PLATFORM_CTR)"
    container_running || die "контейнер $PLATFORM_CTR не запущен — нечего переносить"
    cver=$(cpsql <<<"SHOW server_version" | cut -d. -f1)
    echo "   container: $(cpsql <<<'SELECT version()' | cut -c1-60)"
    [ "$cver" = "$hver" ] || echo "   ВНИМАНИЕ: разные мажорные версии ($cver → $hver); pg_dump/pg_restore это переживут, но проверьте расширения"
    size=$(cpsql <<<"SELECT pg_size_pretty(pg_database_size('$C_DB'))")
    echo "   база $C_DB: $size, таблиц: $(cpsql <<<"SELECT count(*) FROM pg_tables WHERE schemaname='public'"), alembic: $(cpsql <<<'SELECT version_num FROM alembic_version')"
    echo "   расширения: $(cpsql <<<"SELECT string_agg(extname, ', ') FROM pg_extension WHERE extname<>'plpgsql'")"

    log "4/6 место на диске"
    df -h /var/lib/postgresql /opt/omnia-runtime | sed 's/^/   /'

    log "5/6 подсеть docker-сети $COMPOSE_NET (для pg_hba/ufw)"
    sub=$(docker_subnet); [ -n "$sub" ] || die "сеть $COMPOSE_NET не найдена — стек не поднят?"
    echo "   $sub → контейнеры пойдут на $T_HOST:5432"

    log "6/6 режим"
    if host_mode; then echo "   .env уже в host-режиме: $(envval COMPOSE_FILE "$ENV_FILE")"; else echo "   .env: контейнерный режим (PLATFORM_DATABASE_URL не задан)"; fi
    echo "PRECHECK_OK: цель $T_ROLE@$T_HOST/$T_DB"
    ;;

  prepare)
    log "роль и база на хосте: $T_ROLE / $T_DB"
    install -d -m 700 /etc/max-studio
    if [ -f "$CREDS" ] && grep -q '^PLATFORM_PG_PASSWORD=' "$CREDS"; then
      PW=$(envval PLATFORM_PG_PASSWORD "$CREDS")
    else
      PW=${PLATFORM_PG_PASSWORD:-$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)}
    fi
    cd /tmp
    if sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='$T_ROLE'" | grep -q 1; then
      sudo -u postgres psql -qc "alter role \"$T_ROLE\" login password '$PW'"
    else
      sudo -u postgres psql -qc "create role \"$T_ROLE\" login password '$PW'"
    fi
    if ! sudo -u postgres psql -tAc "select 1 from pg_database where datname='$T_DB'" | grep -q 1; then
      sudo -u postgres createdb -O "$T_ROLE" "$T_DB"
    fi
    umask 077
    cat > "$CREDS" <<EOF
# generated by infra/max-k3s/migrate/50-platform-db-to-host.sh — база платформы MAX Studio на хосте core
PLATFORM_PG_HOST=$T_HOST
PLATFORM_PG_PORT=5432
PLATFORM_PG_DB=$T_DB
PLATFORM_PG_USER=$T_ROLE
PLATFORM_PG_PASSWORD=$PW
PLATFORM_PG_DSN=postgresql://$T_ROLE:$PW@$T_HOST:5432/$T_DB
EOF
    chmod 600 "$CREDS"

    log "pg_hba: контейнеры compose-сети → только $T_DB/$T_ROLE (scram)"
    sub=$(docker_subnet); [ -n "$sub" ] || die "сеть $COMPOSE_NET не найдена"
    if ! grep -q "platform-db-to-host" "$HBA"; then
      printf '\n# platform-db-to-host: контейнеры платформы (compose-сеть %s) → база платформы\n' "$COMPOSE_NET" >> "$HBA"
    fi
    if ! grep -qE "^host\s+$T_DB\s+$T_ROLE\s+$sub\s" "$HBA"; then
      printf 'host    %s    %s    %s    scram-sha-256\n' "$T_DB" "$T_ROLE" "$sub" >> "$HBA"
    fi
    systemctl reload postgresql

    log "ufw: $sub → $T_HOST:5432"
    ufw allow from "$sub" to "$T_HOST" port 5432 proto tcp comment 'docker platform → host postgres' >/dev/null
    ufw reload >/dev/null

    log "проверка входа так, как пойдут контейнеры (с адреса docker-моста)"
    PGPASSWORD=$PW psql -h "$T_HOST" -U "$T_ROLE" -d "$T_DB" -tAc "select 'HOST_LOGIN_OK ' || current_user || '@' || inet_server_addr()"
    docker run --rm --network "$COMPOSE_NET" -e PGPASSWORD="$PW" postgres:16-alpine \
      psql -h "$T_HOST" -U "$T_ROLE" -d "$T_DB" -tAc "select 'CONTAINER_LOGIN_OK from ' || inet_client_addr()" \
      || die "контейнер из сети $COMPOSE_NET не достучался до $T_HOST:5432 — смотрите pg_hba/ufw"
    echo "PREPARE_OK: креды в $CREDS (0600); стек не тронут"
    ;;

  freeze)
    log "останавливаю писателей базы платформы: $WRITERS (web остаётся, nginx отдаст 502)"
    compose stop $WRITERS
    sleep 3
    conns=$(cpsql <<<"SELECT count(*) FROM pg_stat_activity WHERE datname='$C_DB' AND pid<>pg_backend_pid()")
    echo "   активных подключений к $C_DB в контейнере: $conns"
    [ "$conns" = 0 ] || { echo "   ещё подключены:"; cpsql <<<"SELECT application_name||' '||coalesce(client_addr::text,'-')||' '||state FROM pg_stat_activity WHERE datname='$C_DB' AND pid<>pg_backend_pid()" | sed 's/^/     /'; die "дождитесь/убейте подключения перед transfer"; }
    echo "FREEZE_OK"
    ;;

  transfer)
    [ -f "$CREDS" ] || die "сначала prepare"
    container_running || die "контейнер $PLATFORM_CTR не запущен"
    running=$(compose ps --status running --services 2>/dev/null | grep -cE "^(api|worker|generation-worker|gateway)$" || true)
    [ "$running" = 0 ] || die "писатели ещё запущены ($running) — сначала freeze"
    tables=$(hpsql <<<"SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    if [ "$tables" != 0 ]; then
      [ "${1:-}" = "--force" ] || die "хостовая база $T_DB не пуста ($tables таблиц). Повторный перенос: transfer --force (пересоздаст базу)"
      log "пересоздаю хостовую базу $T_DB (--force)"
      hpsql postgres <<<"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$T_DB' AND pid<>pg_backend_pid()" >/dev/null
      (cd /tmp && sudo -u postgres dropdb "$T_DB" && sudo -u postgres createdb -O "$T_ROLE" "$T_DB")
    fi
    install -d -m 700 "$WORK"
    dump="$WORK/$C_DB-$(date -u +%Y%m%d-%H%M%S).dump"
    log "pg_dump -Fc из контейнера → $dump"
    docker exec "$PLATFORM_CTR" pg_dump -U "$C_USER" -Fc "$C_DB" > "$dump"
    chmod 600 "$dump"; ls -la "$dump" | sed 's/^/   /'
    log "pg_restore в хостовую базу (владелец объектов — $T_ROLE)"
    # --role: объекты создаёт роль платформы, а не postgres; суперпользователь нужен только чтобы
    # ни одно расширение/функция не упёрлись в права. --exit-on-error: любая ошибка = стоп, не «warnings».
    # Дамп подаётся через stdin: файл лежит в каталоге root (0700), пользователю postgres он не виден.
    (cd /tmp && sudo -u postgres pg_restore --exit-on-error --no-owner --no-privileges --role="$T_ROLE" -d "$T_DB") < "$dump"
    echo "   alembic на хосте: $(hpsql <<<'SELECT version_num FROM alembic_version')"
    echo "TRANSFER_OK: дамп оставлен в $dump"
    ;;

  verify)
    log "сверка счётчиков всех таблиц public: контейнер ↔ хост"
    diff_out=$(diff <(table_counts cpsql) <(table_counts hpsql) || true)
    if [ -n "$diff_out" ]; then echo "$diff_out" | sed 's/^/   /'; die "счётчики расходятся (слева контейнер, справа хост)"; fi
    n=$(table_counts hpsql | wc -l | tr -d ' ')
    echo "   таблиц сверено: $n, строк всего: $(table_counts hpsql | awk -F'\t' '{s+=$2} END {print s}')"
    a=$(cpsql <<<'SELECT version_num FROM alembic_version'); b=$(hpsql <<<'SELECT version_num FROM alembic_version')
    [ "$a" = "$b" ] || die "alembic_version: контейнер $a, хост $b"
    echo "   alembic_version: $a"
    seqs=$(hpsql <<<"SELECT count(*) FROM pg_sequences WHERE schemaname='public'")
    echo "   последовательностей: $seqs (pg_dump переносит их значения)"
    echo "VERIFY_OK"
    ;;

  switch)
    [ -f "$CREDS" ] || die "сначала prepare/transfer/verify"
    PW=$(envval PLATFORM_PG_PASSWORD "$CREDS")
    url="postgresql+asyncpg://$T_ROLE:$PW@$T_HOST:5432/$T_DB"
    log ".env → host-режим"
    cp -p "$ENV_FILE" "$ENV_FILE.before-hostdb.$(date -u +%Y%m%d-%H%M%S)"
    upsert "$ENV_FILE" COMPOSE_FILE "$HOSTDB_COMPOSE"
    upsert "$ENV_FILE" PLATFORM_DATABASE_URL "$url"
    chmod 600 "$ENV_FILE"
    compose config -q || die "compose не собирает конфигурацию с docker-compose.hostdb.yml"
    # COMPOSE_FILE из .env должен реально подхватиться: у api не остаётся зависимости от postgres,
    # а DATABASE_URL смотрит на хост. Иначе стек молча поднимется на контейнере.
    compose config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
assert "postgres" not in (services["api"].get("depends_on") or {}), "docker-compose.hostdb.yml не применён (COMPOSE_FILE из .env не прочитан?)"
for name in ("api", "worker", "generation-worker", "gateway"):
    url = services[name]["environment"]["DATABASE_URL"]
    assert "@postgres:" not in url, f"{name}: DATABASE_URL всё ещё указывает на контейнер"
print("   override применён: зависимости от postgres нет, DATABASE_URL → хост")
' || die "проверка конфигурации compose не прошла"
    log "поднимаю стек на хостовой базе (api применит alembic upgrade head — должен быть no-op)"
    compose up -d $WRITERS
    wait_http http://127.0.0.1:8200/health 180 || { docker logs --tail 40 omnia-prod-api; die "api не поднялся на хостовой базе"; }
    echo "   api: $(curl -fsS -m 5 http://127.0.0.1:8200/health | head -c 160)"
    echo "   gateway: $(curl -fsS -m 5 http://127.0.0.1:8101/health | head -c 120 || echo DOWN)"
    sub=$(docker_subnet)
    conns=$(hpsql <<<"SELECT count(*) FROM pg_stat_activity WHERE datname='$T_DB' AND client_addr <<= '$sub'::inet")
    echo "   подключений контейнеров к хостовой базе: $conns"
    [ "$conns" -ge 1 ] || die "контейнеры не подключились к $T_HOST — откат: rollback"
    log "останавливаю контейнерный Postgres (том full_postgres-data остаётся для отката)"
    docker stop "$PLATFORM_CTR" >/dev/null && echo "   $PLATFORM_CTR остановлен"
    echo "SWITCH_OK: платформа на $T_ROLE@$T_HOST/$T_DB. Дальше: бэкап в host-режиме (infra/backup) и наблюдение; retire — через несколько дней"
    ;;

  status)
    if host_mode; then echo "режим: HOST ($(envval PLATFORM_DATABASE_URL "$ENV_FILE" | sed 's#//.*@#//***@#'))"; else echo "режим: CONTAINER ($PLATFORM_CTR)"; fi
    echo "compose: $(compose ps --format '{{.Service}}={{.State}}' 2>/dev/null | tr '\n' ' ')"
    if container_running; then
      echo "контейнер: alembic $(cpsql <<<'SELECT version_num FROM alembic_version' 2>/dev/null || echo '?'), подключений $(cpsql <<<"SELECT count(*) FROM pg_stat_activity WHERE datname='$C_DB' AND pid<>pg_backend_pid()" 2>/dev/null || echo '?')"
    else
      echo "контейнер: $(docker inspect -f '{{.State.Status}}' "$PLATFORM_CTR" 2>/dev/null || echo 'нет')"
    fi
    if hpsql postgres <<<"select 1 from pg_database where datname='$T_DB'" 2>/dev/null | grep -q 1; then
      echo "хост: alembic $(hpsql <<<'SELECT version_num FROM alembic_version' 2>/dev/null || echo '-'), подключений $(hpsql <<<"SELECT count(*) FROM pg_stat_activity WHERE datname='$T_DB'" 2>/dev/null || echo '?'), размер $(hpsql <<<"SELECT pg_size_pretty(pg_database_size('$T_DB'))")"
    else
      echo "хост: базы $T_DB нет (prepare не выполнялся)"
    fi
    ;;

  rollback)
    log "откат на контейнерный Postgres"
    if [ "${1:-}" = "--with-data" ]; then
      host_mode || die "--with-data имеет смысл только из host-режима"
      log "переношу хостовую базу обратно в контейнер (окно недоступности)"
      compose stop $WRITERS
      docker start "$PLATFORM_CTR" >/dev/null
      for i in $(seq 1 30); do docker exec "$PLATFORM_CTR" pg_isready -U "$C_USER" -q 2>/dev/null && break; sleep 2; done
      install -d -m 700 "$WORK"; dump="$WORK/$T_DB-host-$(date -u +%Y%m%d-%H%M%S).dump"
      (cd /tmp && sudo -u postgres pg_dump -Fc "$T_DB") > "$dump"; chmod 600 "$dump"
      cpsql postgres <<<"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$C_DB' AND pid<>pg_backend_pid()" >/dev/null
      docker exec "$PLATFORM_CTR" dropdb -U "$C_USER" "$C_DB"
      docker exec "$PLATFORM_CTR" createdb -U "$C_USER" "$C_DB"
      docker exec -i "$PLATFORM_CTR" pg_restore --exit-on-error --no-owner --no-privileges -U "$C_USER" -d "$C_DB" < "$dump"
      echo "   контейнер: alembic $(cpsql <<<'SELECT version_num FROM alembic_version')"
    fi
    cp -p "$ENV_FILE" "$ENV_FILE.before-rollback.$(date -u +%Y%m%d-%H%M%S)"
    unset_key COMPOSE_FILE "$ENV_FILE"; unset_key PLATFORM_DATABASE_URL "$ENV_FILE"
    compose up -d postgres
    for i in $(seq 1 30); do docker exec "$PLATFORM_CTR" pg_isready -U "$C_USER" -q 2>/dev/null && break; sleep 2; done
    compose up -d $WRITERS
    wait_http http://127.0.0.1:8200/health 180 || die "api не поднялся на контейнерной базе"
    echo "   api: $(curl -fsS -m 5 http://127.0.0.1:8200/health | head -c 160)"
    echo "ROLLBACK_OK: платформа снова на $PLATFORM_CTR"
    ;;

  retire)
    host_mode || die "стек не в host-режиме — retire только после switch"
    container_running && die "контейнер $PLATFORM_CTR ещё запущен — стек точно на хосте? (status)"
    docker rm "$PLATFORM_CTR" >/dev/null && echo "контейнер $PLATFORM_CTR удалён"
    echo "том full_postgres-data оставлен. Когда бэкапы хостовой базы проверены (restore-test), удалить вручную:"
    echo "   docker volume rm full_postgres-data"
    ;;

  *) die "неизвестный шаг: $STEP" ;;
esac
