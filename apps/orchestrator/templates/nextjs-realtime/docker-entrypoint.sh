#!/bin/sh
# Dev container entrypoint — ensure the FIXED schema (Auth.js tables + the
# realtime channels/messages store) exists, THEN start Next.
#
# We use a deterministic `node scripts/init-db.mjs` (plain CREATE TABLE IF NOT
# EXISTS via the `pg` driver) instead of `drizzle-kit push`. push introspects
# the DB first ("Pulling schema from database…"), and on the shared per-project
# Postgres that step hangs for minutes — run synchronously it would block the
# dev server from ever coming up. Our schema is fixed, so no introspection is
# needed. `timeout` is a hard backstop so DB trouble can never wedge boot.
#
# Fail-soft: if init errors/times out we still start Next; DB-touching requests
# error until the tables land. Auth + channels/messages all live in the same
# schema, so this is what makes signup and realtime work from the first request.

set -e

echo "[entrypoint] ensuring schema (auth tables + channels/messages)"
timeout 30 node scripts/init-db.mjs || echo "[entrypoint] init-db slow/failed — starting dev anyway"

echo "[entrypoint] starting Next.js dev server"
exec pnpm dev
