/** Immutable server hook: fixed categories, no customer payloads or profiles. */
import { createHash, createHmac } from "node:crypto";
import { maxActivityKind, type MaxActivityType } from "@/lib/omnia/activity";

export function analyticsEventId(identity: string): string {
  const hex = createHash("sha256").update(`omnia:analytics:v1:${identity}`).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

export async function forwardMaxAnalytics(
  actor: string, eventId: string, kind: "open" | "action" | "event",
  eventType?: MaxActivityType | "app_open",
): Promise<boolean> {
  // Owner previews and activation probes are never customer traffic.
  const project = process.env.OMNIA_PROJECT_ID || process.env.YLEUM_OWNER_PROJECT_ID || "";
  const token = process.env.MAX_BOT_TOKEN;
  const businessKey = process.env.YLEUM_BUSINESS_ANALYTICS_KEY;
  if (!process.env.OMNIA_PUBLIC_APP_ORIGIN || !/^[1-9][0-9]{0,19}$/.test(actor)) return true;
  if (!project || (!token && !/^[0-9a-f]{64}$/.test(businessKey || ""))) return false;
  if (!token && (kind !== "action" || !eventType || ![
    "order_created", "lead_created", "booking_created",
  ].includes(eventType))) return false;
  if (eventType !== undefined) {
    const family = maxActivityKind(eventType);
    if (eventType === "app_open" ? kind !== "open" :
        !family || kind === "open" || (kind === "action" && family !== "action")) return false;
  }
  const path = `/api/runtime/projects/${project}/analytics`;
  const body = JSON.stringify({ event_id: eventId, kind,
    ...(eventType === undefined ? {} : { event_type: eventType }),
  });
  const issued = Math.floor(Date.now() / 1000);
  const encoded = Buffer.from(JSON.stringify({
    project_id: project, max_user_id: actor, iat: issued, exp: issued + 60,
    method: "POST", path, body_sha256: createHash("sha256").update(body).digest("hex"),
  })).toString("base64url");
  const key = token
    ? createHmac("sha256", token).update("omnia:integration-assertion:v1").digest()
    : Buffer.from(businessKey!, "hex");
  const signed = `v1.${encoded}`;
  const assertion = `${signed}.${createHmac("sha256", key).update(signed).digest("hex")}`;
  const platform = (process.env.OMNIA_PLATFORM_API_URL || "https://yleum.ru").replace(/\/$/, "");
  const relay = process.env.YLEUM_BUSINESS_ANALYTICS_RELAY;
  if (!token && !relay) return false;
  const destination = token ? `${platform}${path}` : relay!;
  // Both attempts carry the same receipt. Callers with committed business data
  // ignore delivery failure; lightweight telemetry reports failure for retry.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(destination, {
        method: "POST", body, redirect: "error", cache: "no-store", signal: AbortSignal.timeout(2_000),
        headers: { "Content-Type": "application/json", [token
          ? "X-Omnia-Integration-Assertion" : "X-Yleum-Business-Assertion"]: assertion },
      });
      if (response.ok) return true;
      if (response.status < 500 && response.status !== 429) return false;
    } catch { /* Unknown delivery outcome: retry only the same receipt. */ }
  }
  return false;
}
