/**
 * Next.js 15 instrumentation hook — runs once per runtime at boot.
 * Sentry server/edge configs are gated by SENTRY_DSN at import time, so
 * an empty DSN turns this into a no-op.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

// Sentry recommends exporting onRequestError so server-side errors that the
// framework swallows still reach Sentry. Safe to call when DSN is empty —
// Sentry SDK no-ops if not initialized.
export { captureRequestError as onRequestError } from "@sentry/nextjs";
