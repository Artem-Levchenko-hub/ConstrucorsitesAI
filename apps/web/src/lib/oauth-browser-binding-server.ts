import "server-only";

import { createHash } from "node:crypto";

type CookieReader = { get(name: string): { value: string } | undefined };

/** Forward only the proof belonging to this ticket; never the browser's whole cookie jar. */
export function oauthTicketBinding(ticket: string, store: CookieReader): {
  name: string; value: string; secure: boolean;
} | null {
  const digest = createHash("sha256").update(ticket).digest("hex");
  for (const prefix of ["__Host-yleum-oauth", "yleum-oauth"]) {
    const name = `${prefix}-ticket-${digest}`;
    const value = store.get(name)?.value;
    if (value && /^[A-Za-z0-9_-]{43}$/.test(value)) {
      return { name, value, secure: prefix.startsWith("__Host-") };
    }
  }
  return null;
}
