/**
 * Sentry init for Edge runtime (middleware + edge route handlers).
 *
 * Limited integration set — Edge runtime forbids most Node APIs.
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_ENV ?? "dev",
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
  });
}
