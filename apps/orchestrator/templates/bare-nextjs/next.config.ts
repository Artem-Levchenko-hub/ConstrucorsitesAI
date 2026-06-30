import type { NextConfig } from "next";

// Dev-tolerant config (mirrors the other stacks): the live preview must keep
// running even while the agent's code has transient TS/ESLint errors. The
// orchestrator forces the prod-build settings at deploy time.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
