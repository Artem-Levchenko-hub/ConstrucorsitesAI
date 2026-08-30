#!/bin/sh
set -e

echo "[entrypoint] syncing MAX Mini App schema"
if ! pnpm exec drizzle-kit push; then
  echo "[entrypoint] schema sync failed; refusing to start against a stale schema" >&2
  exit 1
fi
exec pnpm dev
