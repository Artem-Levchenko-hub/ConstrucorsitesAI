#!/usr/bin/env bash
# V2 PoC smoke for VPS (170.168.72.200).
#
# What it does (idempotent, isolated, NO impact on V1 omnia-prod-*):
#   1. clones V2 branch into /tmp/omnia-v2-test/ (or fetches if already there)
#   2. builds the nextjs-postgres-drizzle:dev image from the template
#   3. starts the orchestrator on 127.0.0.1:8003 as a background process
#   4. POSTs /internal/projects/provision → one container on a free 32xx port
#   5. polls http://127.0.0.1:<port> until 200, prints success + URL
#
# Run on the VPS:
#   bash smoke-vps.sh
#
# Cleanup when done: bash smoke-cleanup.sh
#
# All state lives in /tmp/omnia-v2-test/. Containers prefixed `omnia-dev-smoke*`
# (do not collide with omnia-prod-*).

set -euo pipefail

SANDBOX=/tmp/omnia-v2-test
REPO=https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI.git
BRANCH=claude/v2-phase-a-fullstack
PROJECT_ID="00000000-0000-0000-0000-000000000001"
SLUG="smoke"

# Toggle to keep things alive after the script for manual poking.
KEEP_ALIVE="${KEEP_ALIVE:-1}"

echo "=== V2 PoC smoke — sandbox at $SANDBOX ==="
mkdir -p "$SANDBOX"
cd "$SANDBOX"

# --- 1. source ---
if [ ! -d source ]; then
    echo "[1/5] cloning V2 branch"
    git clone --branch "$BRANCH" --depth 1 "$REPO" source
else
    echo "[1/5] source exists, fetching"
    (cd source && git fetch origin "$BRANCH" && git reset --hard "origin/$BRANCH")
fi
cd source/apps/orchestrator

# --- 2. uv + deps ---
if ! command -v uv >/dev/null; then
    echo "[2/5] installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "[2/5] uv already installed"
fi
uv sync --quiet

# --- 3. build template image ---
TEMPLATE_IMAGE=omnia-template-nextjs-postgres-drizzle:dev
if ! docker image inspect "$TEMPLATE_IMAGE" >/dev/null 2>&1; then
    echo "[3/5] building template image (60-180s, first run)"
    docker build -t "$TEMPLATE_IMAGE" -f templates/nextjs-postgres-drizzle/Dockerfile.dev templates/nextjs-postgres-drizzle/
else
    echo "[3/5] template image cached"
fi

# --- 4. orchestrator background ---
ENV_FILE="$SANDBOX/orchestrator.env"
LOG_FILE="$SANDBOX/orchestrator.log"
PID_FILE="$SANDBOX/orchestrator.pid"

INTERNAL_TOKEN=$(openssl rand -hex 32)
cat > "$ENV_FILE" <<EOF
ENV=dev
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/placeholder
DOCKER_HOST=unix:///var/run/docker.sock
PROJECTS_ROOT=$SANDBOX/projects
NGINX_SITES_DIR=$SANDBOX/nginx-sites
SECRETS_ROOT=$SANDBOX/secrets
REGISTRY_URL=127.0.0.1:5000
BASE_DOMAIN=omniadevelop.ru
PORT_RANGE_MIN=3200
PORT_RANGE_MAX=3299
INTERNAL_TOKEN=$INTERNAL_TOKEN
EOF
mkdir -p "$SANDBOX/projects" "$SANDBOX/nginx-sites" "$SANDBOX/secrets"

# Kill previous orchestrator if any
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "[4/5] stopping previous orchestrator pid=$(cat "$PID_FILE")"
    kill "$(cat "$PID_FILE")"
    sleep 1
fi

echo "[4/5] starting orchestrator on 127.0.0.1:8003 (logs at $LOG_FILE)"
(
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    nohup uv run uvicorn omnia_orchestrator.main:app \
        --host 127.0.0.1 --port 8003 >>"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
)

# Wait for /health
for i in {1..20}; do
    if curl -sf http://127.0.0.1:8003/health >/dev/null 2>&1; then
        echo "  orchestrator ready (pid=$(cat "$PID_FILE"))"
        break
    fi
    sleep 0.5
    if [ "$i" = 20 ]; then
        echo "  orchestrator did not start in 10s — see $LOG_FILE"
        tail -20 "$LOG_FILE"
        exit 1
    fi
done

# --- 5. provision + verify ---
echo "[5/5] POST /internal/projects/provision"
RESP=$(curl -sf -X POST http://127.0.0.1:8003/internal/projects/provision \
    -H "X-Internal-Token: $INTERNAL_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"project_id\":\"$PROJECT_ID\",\"slug\":\"$SLUG\",\"template\":\"nextjs-postgres-drizzle\",\"tier\":\"free\"}")
echo "  response: $RESP"

PORT=$(echo "$RESP" | grep -oE '"port":[0-9]+' | grep -oE '[0-9]+')
if [ -z "$PORT" ]; then
    echo "  could not extract port from response"
    exit 1
fi

echo "  waiting for Next.js to bind on 127.0.0.1:$PORT (cold start 30-90s)"
for i in {1..60}; do
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/"; then
        echo
        echo "=== SUCCESS ==="
        echo "  dev URL (from VPS):    http://127.0.0.1:$PORT/"
        echo "  dev URL (from anywhere): http://170.168.72.200:$PORT/  (only if firewall opens it)"
        echo
        echo "  to inspect the container:    docker logs omnia-dev-$SLUG"
        echo "  to cleanup everything:       bash $SANDBOX/source/apps/orchestrator/scripts/smoke-cleanup.sh"
        if [ "$KEEP_ALIVE" = "1" ]; then
            echo "  orchestrator is still running (pid=$(cat "$PID_FILE")) — leave it for further pokes"
        fi
        exit 0
    fi
    sleep 2
done

echo
echo "=== TIMEOUT ==="
echo "  container started but never bound on $PORT within 120s"
echo "  logs:"
docker logs --tail 30 "omnia-dev-$SLUG" 2>&1 | sed 's/^/    /'
exit 1
