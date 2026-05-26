/**
 * Re-export Auth.js HTTP handlers for the `/api/auth/*` catch-all route.
 * Lives in `lib/` so the route file stays a 1-liner and we don't
 * accidentally execute Auth.js setup twice in different bundle chunks.
 */

import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
