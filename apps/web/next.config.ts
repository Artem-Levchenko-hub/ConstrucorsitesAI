import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  eslint: {
    // Lint is run separately via `pnpm lint`. The build only enforces
    // typecheck (which is the source of truth for type safety anyway).
    ignoreDuringBuilds: true,
  },
  // Build a self-contained server bundle for slim production Docker images.
  output: "standalone",
};

// withSentryConfig is safe to apply even when SENTRY_DSN is empty — it only
// affects the build pipeline (source-map upload, error route generation) and
// runtime Sentry SDK is gated separately by DSN in sentry.*.config.ts.
export default withSentryConfig(nextConfig, {
  // Source-map upload is no-op without SENTRY_AUTH_TOKEN. Provide via CI env.
  silent: true,
  hideSourceMaps: true,
  disableLogger: true,
  tunnelRoute: "/monitoring",
});
