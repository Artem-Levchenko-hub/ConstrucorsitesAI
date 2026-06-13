/**
 * V4.2b RETURN-EDGE — client-side sanitizers for viral-funnel signup provenance.
 *
 * When a stranger lands on `/register` from a shared `/p/<slug>` surface, the URL
 * carries `?source=...&ref=<project-uuid>`. The register server action forwards
 * these to `POST /api/auth/register`, whose `UserCreate` schema validates `source`
 * against a closed enum and `referrer_project_id` as a UUID (apps/api .../schemas/
 * user.py). Forwarding a value outside those shapes would earn a 422 and BREAK an
 * otherwise-valid signup. So we mirror the backend's accepted shapes here and
 * sanitize anything else to `null` → the field is omitted → the signup proceeds as
 * organic (NULL provenance). This keeps the falsifiable contract honest: a real
 * `ref` rules, junk (or no) `ref` falls back to blank/organic.
 */

/** Mirror of the backend's closed SignupSource enum — keep in lockstep. */
export const SIGNUP_SOURCES = ["share_link", "remix", "direct"] as const;
export type SignupSource = (typeof SIGNUP_SOURCES)[number];

// Canonical 8-4-4-4-12 UUID. The backend's Python UUID() is laxer (it also
// accepts braces/urn forms), so this is intentionally stricter: any value that
// would not round-trip as a plain project id is dropped to organic rather than
// risking a 422.
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Returns the value only if it is one of the known signup sources, else null. */
export function sanitizeSignupSource(raw: unknown): SignupSource | null {
  return typeof raw === "string" &&
    (SIGNUP_SOURCES as readonly string[]).includes(raw)
    ? (raw as SignupSource)
    : null;
}

/** Returns the value only if it is a well-formed UUID, else null. */
export function sanitizeReferrerProjectId(raw: unknown): string | null {
  return typeof raw === "string" && UUID_RE.test(raw) ? raw : null;
}
