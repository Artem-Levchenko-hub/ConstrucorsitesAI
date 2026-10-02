# Build context: apps/orchestrator. Use build-public-max-core.sh to verify the base.
# Reuse its exact dependencies; no npm/pnpm install or package-version changes.
ARG BASE_IMAGE
ARG RUNTIME_BASE_IMAGE
FROM ${BASE_IMAGE} AS build
WORKDIR /app
COPY --chown=node:node templates/max-miniapp-nextjs/src ./src
COPY --chown=node:node templates/max-miniapp-nextjs/scripts ./scripts
COPY --chown=node:node scripts/public-max-core/bootstrap-database.mjs ./scripts/bootstrap-database.mjs
COPY --chown=node:node templates/max-miniapp-nextjs/drizzle ./drizzle
COPY --chown=node:node templates/max-miniapp-nextjs/next.config.ts ./next.config.ts
COPY --chown=node:node scripts/public-max-core/prepare.mjs ./prepare-managed-core.mjs
COPY --chown=node:node scripts/public-max-core/preview-auth.test.cjs ./preview-auth.test.cjs
COPY --chown=node:node scripts/public-max-core/action-write-contract.test.cjs ./action-write-contract.test.cjs
RUN node prepare-managed-core.mjs
RUN node --test preview-auth.test.cjs
RUN OMNIA_ACTION_TEST_TEMPLATE_ROOT=/app node --test action-write-contract.test.cjs
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_OPTIONS=--max-old-space-size=1536
# Import-time pool construction needs a DSN; no connection, secret or runtime
# setting is provided to the build, and the network is disabled.
RUN --network=none DATABASE_URL=postgresql://build@127.0.0.1/build_only node node_modules/next/dist/bin/next build

# Runtime retains only traced production dependencies and the verified Node
# ancestor; development node_modules and downloaded pnpm caches stay in build.
FROM ${RUNTIME_BASE_IMAGE} AS runtime
WORKDIR /app
# Normal controller overlays atomically rename owner configuration as node.
RUN chown node:node /app
# Compiled runtime uses Node directly; keep package-manager tooling in the kit.
# Validate each exact target before deleting it, preserving Node and Yarn.
RUN node <<'NODE'
const fs = require("node:fs");
const roots = ["/usr/local/lib/node_modules/npm", "/usr/local/lib/node_modules/corepack"];
const commands = ["/usr/local/bin/npm", "/usr/local/bin/npx", "/usr/local/bin/corepack"];
for (const root of roots) {
  if (!fs.lstatSync(root).isDirectory()) throw new Error("Unexpected package-manager directory");
}
for (const command of commands) {
  if (!fs.lstatSync(command).isSymbolicLink()) throw new Error("Unexpected package-manager command");
  const target = fs.realpathSync(command);
  if (!roots.some(root => target.startsWith(root + "/"))) throw new Error("Unexpected package-manager target");
}
for (const command of commands) fs.unlinkSync(command);
for (const root of roots) fs.rmSync(root, { recursive: true });
NODE
LABEL omnia.max-core.protocol="1"
LABEL omnia.max-core.preview-protocol="1"
LABEL omnia.max-core.db-role-protocol="1"
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/scripts ./scripts
COPY --from=build --chown=node:node /app/drizzle ./drizzle
USER node
EXPOSE 3000
CMD ["node", "server.js"]
