#!/bin/sh
set -e

echo "[entrypoint] syncing MAX Mini App schema"
pnpm exec drizzle-kit push --force || echo "[entrypoint] schema sync failed; starting app"
exec pnpm dev
