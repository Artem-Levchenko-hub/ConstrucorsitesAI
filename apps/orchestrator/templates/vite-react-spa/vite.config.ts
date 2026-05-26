import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// HMR config tuned for the orchestrator-managed iframe preview:
// - `host: 0.0.0.0` so the container exposes :3000 to the host network
// - `hmr.clientPort: 443` because Omnia's nginx terminates TLS and the
//   browser-side client connects via `wss://<slug>-dev.preview.*` on
//   the public port, NOT the container's :3000.
// - allowed hosts = `.lead-generator.ru` + `.sslip.io` (sslip.io fallback
//   when wildcard DNS isn't set up — see orchestrator/.env.example).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: true,
    hmr: { clientPort: 443 },
    allowedHosts: [".lead-generator.ru", ".sslip.io", ".localhost"],
  },
  preview: {
    host: "0.0.0.0",
    port: 3000,
    allowedHosts: [".lead-generator.ru", ".sslip.io", ".localhost"],
  },
});
