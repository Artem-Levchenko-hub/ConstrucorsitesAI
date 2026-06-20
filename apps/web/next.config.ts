import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

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

export default withNextIntl(nextConfig);
