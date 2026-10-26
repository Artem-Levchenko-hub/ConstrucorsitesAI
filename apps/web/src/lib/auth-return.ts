/** Keep auth and confirmation returns on this origin, including payment queries. */
export function safeReturnPath(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return null;
  return raw;
}
