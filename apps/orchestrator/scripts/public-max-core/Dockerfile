# Build context: apps/orchestrator. Use build-public-max-core.sh to verify the base.
# Reuse its exact dependencies; no npm/pnpm install or package-version changes.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build
WORKDIR /app
COPY --chown=node:node templates/max-miniapp-nextjs/src ./src
COPY --chown=node:node templates/max-miniapp-nextjs/scripts ./scripts
COPY --chown=node:node templates/max-miniapp-nextjs/drizzle ./drizzle
COPY --chown=node:node templates/max-miniapp-nextjs/next.config.ts ./next.config.ts
COPY --chown=node:node scripts/public-max-core/prepare.mjs ./prepare-managed-core.mjs
COPY --chown=node:node scripts/public-max-core/preview-auth.test.cjs ./preview-auth.test.cjs
RUN node prepare-managed-core.mjs
RUN node --test preview-auth.test.cjs
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_OPTIONS=--max-old-space-size=1536
# Import-time pool construction needs a DSN; no connection, secret or runtime
# setting is provided to the build, and the network is disabled.
RUN --network=none DATABASE_URL=postgresql://build@127.0.0.1/build_only node node_modules/next/dist/bin/next build

FROM ${BASE_IMAGE} AS runtime
WORKDIR /app
LABEL omnia.max-core.protocol="1"
LABEL omnia.max-core.preview-protocol="1"
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
CMD ["sh", "-ec", "timeout 45 node scripts/apply-migrations.mjs\nexec node server.js"]
