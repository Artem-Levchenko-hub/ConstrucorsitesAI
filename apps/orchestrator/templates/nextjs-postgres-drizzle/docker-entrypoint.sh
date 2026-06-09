#!/bin/sh
# Dev container entrypoint — sync the Drizzle schema with the per-project
# Postgres schema, THEN start Next. `db:push --force` is the right primitive
# for a dev container: it materialises whatever `src/lib/db/schema.ts`
# declares without managing migration files (which would race AI writes).
#
# Fail-soft: if push fails (e.g. DB unreachable on first boot), we still
# start Next so the user sees something. The DB-touching pages will error
# at request time — that's better than an opaque "container won't start".
#
# Auth tables (users/sessions/accounts/verification_tokens) live in the
# same schema.ts, so this push is what makes signup/signin work from the
# very first request after provision.

set -e

echo "[entrypoint] syncing drizzle schema -> postgres"
# Run drizzle-kit directly (not via the `db:push` pnpm script) so the sync still
# works if the script is ever missing from package.json — drizzle-kit is a
# devDependency, always present in node_modules/.bin inside the dev image.
pnpm exec drizzle-kit push --force || echo "[entrypoint] drizzle push failed (continuing — pages will error on DB access)"

echo "[entrypoint] starting Next.js dev server"
exec pnpm dev
