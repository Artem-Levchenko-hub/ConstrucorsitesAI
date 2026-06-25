/**
 * Auth.js catch-all route — exposes Auth's REST endpoints (signin, signout,
 * session, csrf) under /api/auth/*. FIXED template file. Custom sign-up lives in
 * /api/auth/register because the Credentials provider has no registration.
 */

import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
