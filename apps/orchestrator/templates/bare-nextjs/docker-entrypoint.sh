#!/bin/sh
# Bare stack entrypoint: just the dev server. No schema init — the bare stack
# ships no fixed DB; whatever data layer the agent builds, it initializes itself
# (a route, a script, or a migration the agent writes). Postgres/Redis are
# reachable via DATABASE_URL / REDIS_URL when the agent decides to use them.
set -e
exec pnpm dev
