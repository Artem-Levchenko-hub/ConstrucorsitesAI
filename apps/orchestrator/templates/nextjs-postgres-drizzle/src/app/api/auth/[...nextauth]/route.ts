/**
 * Auth.js catch-all route — wires Auth's REST endpoints into Next's app
 * router. Don't add custom handlers here; both signin and signout funnel
 * through Auth's exported handlers below. Custom sign-up lives in
 * `app/api/auth/signup/route.ts` because Auth.js Credentials provider
 * doesn't include registration out of the box.
 */

export { GET, POST } from "@/lib/auth-handlers";
