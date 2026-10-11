/** Trusted core only: platform policy/state, never inferred from local consent history. */
import { createHash, createHmac, randomUUID } from "node:crypto";

export type ConsentPolicy = { enabled: boolean; policy_version: string | null; policy_hash: string | null };
export type ConsentState = { granted: boolean; policy_version: string | null; policy_hash: string | null; revision: number };
export class ConsentBridgeError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); }
}
const unavailable = () => new ConsentBridgeError(503, "consent_service_unavailable", "Не удалось проверить согласие. Повторите запрос позже.");

async function requestPlatform(actor: string, suffix: "consent-policy" | "consents", payload?: object): Promise<unknown> {
  const project = process.env.OMNIA_PROJECT_ID || "";
  const token = process.env.MAX_BOT_TOKEN;
  if (!project || !token || !/^[1-9][0-9]{0,19}$/.test(actor)) throw unavailable();
  const method = payload ? "POST" : "GET";
  const path = `/api/runtime/projects/${project}/${suffix}`;
  const body = payload ? JSON.stringify(payload) : "";
  const issued = Math.floor(Date.now() / 1000);
  const encoded = Buffer.from(JSON.stringify({
    project_id: project, max_user_id: actor, iat: issued, exp: issued + 60,
    method, path, body_sha256: createHash("sha256").update(body).digest("hex"),
  })).toString("base64url");
  const key = createHmac("sha256", token).update("omnia:integration-assertion:v1").digest();
  const signed = `v1.${encoded}`;
  const assertion = `${signed}.${createHmac("sha256", key).update(signed).digest("hex")}`;
  const platform = (process.env.OMNIA_PLATFORM_API_URL || "https://yleum.ru").replace(/\/$/, "");
  // Only a transport retry: signed actor, body, event and baseline remain frozen.
  // In particular, a 409 never triggers a read-and-regrant with a new revision.
  for (let attempt = 0; attempt < (payload ? 2 : 1); attempt++) {
    try {
      const response = await fetch(`${platform}${path}`, {
        method, body: payload ? body : undefined, cache: "no-store", signal: AbortSignal.timeout(5_000),
        headers: { "Content-Type": "application/json", "X-Omnia-Integration-Assertion": assertion },
      });
      if (!response.ok) {
        if (response.status >= 500 && payload && attempt === 0) continue;
        throw new ConsentBridgeError(response.status >= 500 ? 503 : response.status, "consent_record_rejected", "Согласие не сохранено. Обновите условия и повторите действие.");
      }
      return await response.json();
    } catch (error) {
      if (error instanceof ConsentBridgeError) throw error;
      if (!payload || attempt > 0) throw unavailable();
    }
  }
  throw unavailable();
}

export async function getConsentPolicy(actor: string): Promise<ConsentPolicy> {
  const data = await requestPlatform(actor, "consent-policy") as Partial<ConsentPolicy> | null;
  if (!data || typeof data.enabled !== "boolean" ||
      !(data.policy_version === null || typeof data.policy_version === "string") ||
      !(data.policy_hash === null || typeof data.policy_hash === "string") ||
      (data.enabled && (!data.policy_version || !/^[a-f0-9]{64}$/i.test(data.policy_hash || "")))) throw unavailable();
  return data as ConsentPolicy;
}

function parseState(value: unknown): ConsentState {
  const state = value as Partial<ConsentState> | null;
  if (!state || typeof state.granted !== "boolean" || !Number.isSafeInteger(state.revision) || Number(state.revision) < 0 ||
      !(state.policy_version === null || typeof state.policy_version === "string") ||
      !(state.policy_hash === null || typeof state.policy_hash === "string")) throw unavailable();
  return state as ConsentState;
}

export async function getConsentContext(actor: string): Promise<{ policy: ConsentPolicy; state: ConsentState | null }> {
  const policy = await getConsentPolicy(actor);
  return { policy, state: policy.enabled ? parseState(await requestPlatform(actor, "consents")) : null };
}

export async function recordConsent(actor: string, input: {
  granted: boolean; policyVersion: string; eventId?: string; expectedRevision?: number; policyHash?: string;
}, context: { policy: ConsentPolicy; state: ConsentState | null }): Promise<{ state: ConsentState }> {
  const { policy, state } = context;
  if (!policy.enabled || !policy.policy_version || !policy.policy_hash || !state) throw unavailable();
  if (input.granted && ((input.policyVersion !== "1" && input.policyVersion !== policy.policy_version) ||
      (input.policyHash !== undefined && input.policyHash !== policy.policy_hash))) {
    throw new ConsentBridgeError(409, "consent_policy_changed", "Условия согласия изменились. Прочитайте актуальную политику.");
  }
  // A legacy explicit POST is a new grant/revoke intent. Never call this from a
  // lead write or migrate an existing local grant. New SDKs provide stable IDs.
  const eventId = input.eventId || randomUUID();
  parseState(await requestPlatform(actor, "consents", {
    event_id: eventId, expected_revision: input.expectedRevision ?? state.revision,
    policy_version: policy.policy_version, policy_hash: policy.policy_hash, granted: input.granted,
  }));
  // A mutation receipt may predate a concurrent revoke. Read current state for
  // acknowledgement only: never use this read to retry or renew the mutation.
  return { state: parseState(await requestPlatform(actor, "consents")) };
}
