# syntax=docker/dockerfile:1.6
#
# Production image for the Omnia web app.
#
# Build:
#   docker build -t omnia-web .
# Run:
#   docker run --env-file .env -p 3000:3000 omnia-web
#
# Stage 1: install deps (pnpm).
# Stage 2: next build (with output: "standalone" in next.config.ts).
# Stage 3: minimal runtime — node + standalone bundle + static + public.

FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# Keep downloads conservative and retry transient registry failures. Using
# Dockerfile-level `--network=host` here makes the build require an insecure
# BuildKit entitlement and causes production builds to fail before this step.
RUN pnpm config set network-concurrency 4 \
 && pnpm config set fetch-retries 5 \
 && pnpm config set fetch-retry-maxtimeout 120000 \
 && pnpm install --frozen-lockfile


FROM node:20-alpine AS builder
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Every NEXT_PUBLIC_* that exists at build time is inlined into ALL bundles
# (browser, node server and edge), so only domain-independent values may be
# build args. The public domain is deliberately NOT one of them — one image
# must run under any domain:
#   - the browser calls the API and opens the WebSocket on the same origin that
#     served the page (the edge proxy routes /api/ to the api);
#   - the server reads PUBLIC_ORIGIN and INTERNAL_API_URL at run time.
# Do NOT re-add NEXT_PUBLIC_API_URL / NEXT_PUBLIC_WS_URL here: `ENV X=$X`
# defines X even when no --build-arg is passed, as an EMPTY string, and that
# empty string is what gets inlined. CI greps the client bundle for the
# production domain (.github/workflows/ci.yml, job image-build).
ARG NEXT_PUBLIC_USE_MOCKS=false
ARG NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION=2026-07-30
ENV NEXT_PUBLIC_USE_MOCKS=$NEXT_PUBLIC_USE_MOCKS \
    NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION=$NEXT_PUBLIC_LEGAL_DOCUMENT_VERSION \
    NEXT_TELEMETRY_DISABLED=1
# next/font downloads Google fonts at build time; a transient network hiccup fails the
# whole image build after everything else passed (CI image-build, 25.09.2026). One retry
# separates a flake from a real break; self-hosting the four fonts is the durable follow-up.
RUN pnpm build || (echo "next build failed once (next/font download?) — retrying" && sleep 20 && pnpm build)


FROM node:20-alpine AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S nextjs && adduser -S nextjs -G nextjs
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nextjs /app/public ./public
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD wget --quiet --spider http://127.0.0.1:3000/api/health || exit 1
CMD ["node", "server.js"]
