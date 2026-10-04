/** Immutable server hook: fixed categories, no customer payloads or profiles. */
import { createHash, createHmac } from "node:crypto";

export function analyticsEventId(identity: string): string {
  const hex = createHash("sha256").update(`omnia:analytics:v1:${identity}`).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

export async function forwardMaxAnalytics(
  actor: string, eventId: string, kind: "open" | "action" | "event",
): Promise<void> {
  // Owner previews and activation probes are never customer traffic.
  const project = process.env.OMNIA_PROJECT_ID || "";
  const token = process.env.MAX_BOT_TOKEN;
  if (!project || !token || !process.env.OMNIA_PUBLIC_APP_ORIGIN ||
      !/^[1-9][0-9]{0,19}$/.test(actor)) return;
  const path = `/api/runtime/projects/${project}/analytics`;
  const body = JSON.stringify({ event_id: eventId, kind });
  const issued = Math.floor(Date.now() / 1000);
  const encoded = Buffer.from(JSON.stringify({
    project_id: project, max_user_id: actor, iat: issued, exp: issued + 60,
    method: "POST", path, body_sha256: createHash("sha256").update(body).digest("hex"),
  })).toString("base64url");
  const key = createHmac("sha256", token).update("omnia:integration-assertion:v1").digest();
  const signed = `v1.${encoded}`;
  const assertion = `${signed}.${createHmac("sha256", key).update(signed).digest("hex")}`;
  const platform = (process.env.OMNIA_PLATFORM_API_URL || "https://yleum.ru").replace(/\/$/, "");
  // Both attempts carry the same durable event receipt. Collection failure must
  // never change an already committed user action or expose credentials in logs.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(`${platform}${path}`, {
        method: "POST", body, cache: "no-store", signal: AbortSignal.timeout(2_000),
        headers: { "Content-Type": "application/json", "X-Omnia-Integration-Assertion": assertion },
      });
      if (response.ok || (response.status < 500 && response.status !== 429)) return;
    } catch { /* The app-local durable record remains authoritative. */ }
  }
}
