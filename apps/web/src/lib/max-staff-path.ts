export const maxStaffEnabled = process.env.NEXT_PUBLIC_MAX_STAFF_ENABLED === "true";

export const MAX_STAFF_PATH = "/max/staff";
export const MAX_STAFF_JOIN_PATH = "/max/staff/join";
// Next strips a bare "#"; a non-empty fragment overwrites the invitation token.
export const MAX_STAFF_DISABLED_REDIRECT = "/max#top";

export function maxStaffProjectPath(projectId: string): string {
  return `${MAX_STAFF_PATH}/${encodeURIComponent(projectId)}`;
}

/** Pass only a known staff pathname across auth, never an invitation token. */
export function maxStaffReturnPath(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const path = raw.split(/[?#]/, 1)[0];
  return path === MAX_STAFF_PATH || path === MAX_STAFF_JOIN_PATH ||
    /^\/max\/staff\/[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(path) ? path : null;
}
