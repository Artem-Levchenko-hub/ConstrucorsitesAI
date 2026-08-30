#!/bin/sh
set -e

echo "[entrypoint] applying deterministic MAX Mini App migrations"
if ! timeout 45 node scripts/apply-migrations.mjs; then
  echo "[entrypoint] migration apply failed; refusing to start against a stale schema" >&2
  exit 1
fi
exec pnpm dev
