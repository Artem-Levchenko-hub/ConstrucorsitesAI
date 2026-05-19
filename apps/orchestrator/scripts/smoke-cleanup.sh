#!/usr/bin/env bash
# Tear down everything smoke-vps.sh created. Safe to run multiple times.

set -uo pipefail

SANDBOX=/tmp/omnia-v2-test
PID_FILE="$SANDBOX/orchestrator.pid"

echo "=== V2 PoC cleanup ==="

# 1. Stop orchestrator
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "  stopping orchestrator pid=$PID"
        kill "$PID"
        sleep 1
        kill -9 "$PID" 2>/dev/null || true
    fi
fi
# Belt and braces: kill any uvicorn on :8003 that belongs to us
pkill -f "uvicorn omnia_orchestrator" 2>/dev/null || true

# 2. Destroy dev containers + the template image
for c in $(docker ps -aq --filter "label=omnia.kind=dev" 2>/dev/null); do
    echo "  removing container $c"
    docker rm -f "$c" >/dev/null 2>&1 || true
done
if docker image inspect omnia-template-nextjs-postgres-drizzle:dev >/dev/null 2>&1; then
    echo "  removing template image"
    docker rmi omnia-template-nextjs-postgres-drizzle:dev >/dev/null 2>&1 || true
fi

# 3. Remove sandbox state
if [ -d "$SANDBOX" ]; then
    echo "  removing $SANDBOX"
    rm -rf "$SANDBOX"
fi

echo "=== done ==="
echo "  V1 omnia-prod-* and other tenants were not touched."
