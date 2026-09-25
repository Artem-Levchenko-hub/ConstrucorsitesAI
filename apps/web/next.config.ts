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
  // The site-builder editor moved out of this app (see
  // docs/plans/2026-09-19-max-only-separation.md). Old bookmarks, `?next=/projects`
  // login links and the API's remix redirect land in the Yleum cabinet
  // instead of a 404.
  async redirects() {
    return [
      { source: "/projects", destination: "/max", permanent: false },
      { source: "/projects/:path*", destination: "/max", permanent: false },
      { source: "/deep-research", destination: "/max", permanent: false },
    ];
  },
};

export default withNextIntl(nextConfig);
