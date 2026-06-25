import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Lint is advisory in this template; the orchestrator's agent loop runs a real
  // `tsc --noEmit` as the correctness gate, so a missing eslint setup must not
  // block a prod build.
  eslint: { ignoreDuringBuilds: true },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
      {
        // SSE must never be buffered or cached by an intermediary, or events
        // arrive in bursts instead of in real time.
        source: "/api/realtime/:path*",
        headers: [{ key: "X-Accel-Buffering", value: "no" }],
      },
    ];
  },
};

export default nextConfig;
