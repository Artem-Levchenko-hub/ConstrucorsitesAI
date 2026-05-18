/**
 * Sentry init for Node.js runtime (RSC + route handlers).
 *
 * Server-side DSN is the same Sentry project as the client — Sentry de-dups
 * across runtimes via event ID. The SERVER token is private (no NEXT_PUBLIC_
 * prefix), so it never leaks into the client bundle.
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_ENV ?? "dev",
    tracesSampleRate: 0.1,
    profilesSampleRate: 0.1,
    sendDefaultPii: false,
    beforeSend(event) {
      if (event.request?.cookies) event.request.cookies = {};
      if (event.request?.headers) {
        for (const k of Object.keys(event.request.headers)) {
          if (/authorization|cookie/i.test(k)) {
            event.request.headers[k] = "[Filtered]";
          }
        }
      }
      return event;
    },
  });
}
