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
    // `true` = accept ANY Host header. Required because Omnia's preview-render
    // worker screenshots the app container-to-container at `http://omnia-dev-
    // <slug>:3000` (the only address reachable without public egress — see
    // services/dev_container). A restrictive allowedHosts list rejects that
    // internal hostname with Vite's "Blocked request" page, so every snapshot
    // thumbnail came out BLANK WHITE (owner report 2026-07-18). Safe here: the
    // container binds 127.0.0.1 only and is reachable solely via Omnia's nginx
    // (which sets the real *-dev.preview host) or the internal docker network —
    // no public IP, so the DNS-rebinding class this guards against can't occur.
    allowedHosts: true,
  },
  preview: {
    host: "0.0.0.0",
    port: 3000,
    allowedHosts: true,
  },
});
