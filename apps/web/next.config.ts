import type { NextConfig } from "next";

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

export default nextConfig;
