/**
 * Sentry init for browser code (App Router client components).
 *
 * Empty DSN → SDK no-ops, so dev/preview builds without secrets stay quiet.
 * Replay & feedback intentionally off until product needs them — keeps the
 * bundle small and the free-tier event budget healthy.
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_ENV ?? "dev",
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
    beforeSend(event) {
      // The workspace can carry the user's prompt in route state. Drop it
      // before shipping — prompts can contain personal context.
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
