#!/bin/sh
# Generic dev supervisor for the no-stack bare box. There is no fixed framework —
# the AGENT writes its dev-server start command to /app/.omnia-dev.sh (any stack)
# and this loop runs it on :3000, restarting it if it exits (crash → self-heal).
# Until the agent writes that file the preview is simply not up yet (expected
# during scaffolding). To switch stacks the agent rewrites .omnia-dev.sh and frees
# the port (`fuser -k 3000/tcp` / kill) so the loop re-runs the new command.
set -u
START=/app/.omnia-dev.sh
echo "[bare] generic dev box ready (node $(node -v), $(python3 --version 2>&1)). Waiting for the agent to write $START and start a server on :3000."
while true; do
  if [ -f "$START" ]; then
    sh "$START" || true
    echo "[bare] dev server exited — restarting in 2s"
  fi
  sleep 2
done
