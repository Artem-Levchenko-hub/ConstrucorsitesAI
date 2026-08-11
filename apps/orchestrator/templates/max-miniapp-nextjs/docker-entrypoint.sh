#!/bin/sh
set -e

echo "[entrypoint] syncing MAX Mini App schema"
pnpm db:push
exec pnpm dev
