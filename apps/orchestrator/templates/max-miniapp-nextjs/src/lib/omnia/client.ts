"use client";

import { getMaxWebApp } from "@/lib/max/bridge";
import { maxActivityKind, type MaxActivityType } from "@/lib/omnia/activity";

export type { MaxActivityType } from "@/lib/omnia/activity";

async function post(path: string, payload: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function activityReceipt(options: { eventId?: string }): string {
  const eventId = options.eventId === undefined ? crypto.randomUUID() : options.eventId;
  if (typeof eventId !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(eventId)) {
    throw new Error("Analytics event failed");
  }
  return eventId.toLowerCase();
}

async function deliverActivity(path: string, body: string, ignoreUnauthorized = false): Promise<void> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const response = await fetch(path, {
        method: "POST", credentials: "include", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body,
      });
      if (response.ok || (ignoreUnauthorized && response.status === 401)) return;
      if (response.status < 500 && response.status !== 429) break;
    } catch { /* Unknown delivery outcome: retry only the same receipt. */ }
    finally { clearTimeout(timeout); }
  }
  throw new Error("Analytics event failed");
}

/** Reuse eventId only when retrying the same logical event, including reloads. */
export async function trackMaxEvent(
  eventName: string,
  properties: Record<string, unknown> = {},
  options: { eventId?: string } = {},
): Promise<void> {
  // Preserve the existing local-properties contract and its anonymous no-op.
  const body = JSON.stringify({ eventName, properties, eventId: activityReceipt(options) });
  await deliverActivity("/api/omnia/events", body, true);
}

/** Call after confirmed UI/business success. No properties or stock DB required. */
export async function trackMaxActivity(
  eventType: MaxActivityType,
  options: { eventId?: string } = {},
): Promise<void> {
  if (!maxActivityKind(eventType)) throw new Error("Analytics event failed");
  // Freeze once: retries never generate a second receipt or change the body.
  const body = JSON.stringify({ eventType, eventId: activityReceipt(options) });
  await deliverActivity("/api/omnia/telemetry", body);
}

export async function saveMaxConsent(
  consentType: string,
  granted: boolean,
  policyVersion = "1",
): Promise<void> {
  // This function is called only for an explicit grant/revoke. Freeze its event
  // and revision before delivery; a retry must never refresh a revoked baseline.
  const context = await fetch("/api/omnia/consents?platform=1", { credentials: "include", cache: "no-store" });
  if (!context.ok) throw new Error("Consent save failed");
  const snapshot = await context.json() as {
    actor?: string; policy?: { enabled?: boolean; policy_version?: string | null; policy_hash?: string | null };
    state?: { revision?: number } | null;
  };
  if (!snapshot.actor || typeof snapshot.policy?.enabled !== "boolean") throw new Error("Consent save failed");
  const authoritative = snapshot.policy.enabled && consentType === "personal_data";
  if (authoritative && (!Number.isSafeInteger(snapshot.state?.revision) || Number(snapshot.state?.revision) < 0 ||
      typeof snapshot.policy.policy_version !== "string" || typeof snapshot.policy.policy_hash !== "string")) throw new Error("Consent save failed");
  const body = JSON.stringify({ consentType, granted, policyVersion,
    ...(authoritative ? { eventId: crypto.randomUUID(), expectedRevision: snapshot.state!.revision, policyHash: snapshot.policy.policy_hash } : {}),
  });
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch("/api/omnia/consents", {
        method: "POST", credentials: "include", signal: AbortSignal.timeout(10_000),
        headers: { "Content-Type": "application/json", "X-Omnia-Actor": snapshot.actor }, body,
      });
      if (response.ok) {
        if (!authoritative) return;
        const receipt = await response.json().catch(() => null) as {
          state?: { granted?: boolean; policy_version?: string | null; policy_hash?: string | null };
        } | null;
        if (receipt?.state?.granted === granted && (!granted ||
            (receipt.state.policy_version === snapshot.policy.policy_version && receipt.state.policy_hash === snapshot.policy.policy_hash))) return;
        // A replay may acknowledge an old receipt after a revoke. It must not
        // report a fresh grant or trigger another event with a newer baseline.
        break;
      }
      if (response.status < 500) break;
    } catch { /* Unknown delivery: retry only this same frozen consent event. */ }
  }
  throw new Error("Consent save failed");
}

// Keep the supported legacy import on the same durable write contract.
export { createMaxAction } from "@/lib/omnia/integration-client";

export type MaxActionHistoryPage = {
  actions: Array<Record<string, unknown>>;
  nextCursor: string | null;
};

export async function getMaxActions(options: {
  limit?: number;
  cursor?: string | null;
} = {}): Promise<MaxActionHistoryPage> {
  const params = new URLSearchParams();
  if (typeof options.limit === "number" && Number.isFinite(options.limit)) {
    params.set("limit", String(options.limit));
  }
  if (options.cursor) params.set("cursor", options.cursor);
  const query = params.toString();
  const response = await fetch(`/api/omnia/actions${query ? `?${query}` : ""}`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error("История действий временно недоступна");
  return response.json() as Promise<MaxActionHistoryPage>;
}

// Compatibility alias for model-generated product code. Both names use the
// same authenticated, tenant-filtered Yleum endpoint.
export const getActionHistory = getMaxActions;

async function integration<T>(
  path: "status" | "payments" | "payment-status" | "leads" | "catalog" | "ai",
  payload: Record<string, unknown> = {},
): Promise<T> {
  const initData = getMaxWebApp()?.initData;
  if (!initData) throw new Error("Откройте приложение внутри MAX");
  const response = await post(`/api/omnia/integrations/${path}`, {
    initData,
    payload,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body?.error?.message || "Интеграция временно недоступна");
  }
  return body as T;
}

export function getYleumIntegrations(): Promise<{
  providers: string[];
  capabilities: string[];
  analytics_counter_id: string | null;
}> {
  return integration("status");
}

export function createYleumPayment(input: {
  amount: number;
  description: string;
  return_url: string;
  idempotency_key?: string;
  metadata?: Record<string, string>;
  receipt?: Record<string, unknown>;
}): Promise<{ id: string; status: string; confirmation_url: string | null }> {
  return integration("payments", {
    ...input,
    idempotency_key: input.idempotency_key || crypto.randomUUID(),
  });
}

export function getYleumPayment(paymentId: string): Promise<{
  id: string;
  status: string;
  confirmation_url: string | null;
}> {
  return integration("payment-status", { payment_id: paymentId });
}

export function createYleumLead(input: {
  name: string;
  phone?: string;
  email?: string;
  comment?: string;
  source?: string;
}): Promise<{ provider: string; id: string }> {
  return integration("leads", input);
}

export function getYleumCatalog(): Promise<{
  provider: string;
  items: Array<{
    id: string;
    name: string;
    description: string;
    price: number | null;
    currency: string;
    available: boolean;
    image_url: string | null;
  }>;
}> {
  return integration("catalog");
}

type YleumAIInput = {
  message?: string;
  prompt?: string;
  instructions?: string;
  context?: Record<string, unknown>;
};

export async function requestYleumAI(
  input: YleumAIInput,
): Promise<{ answer: string; text: string; model: string }> {
  const message = input.message || input.prompt;
  if (!message?.trim()) throw new Error("Введите сообщение для ИИ-тренера");
  const result = await integration<{ answer: string; model: string }>("ai", {
    message,
    instructions: input.instructions,
    context: input.context,
  });
  return { ...result, text: result.answer };
}

export async function trackYleumGoal(
  goal: string,
  parameters: Record<string, unknown> = {},
): Promise<void> {
  const status = await getYleumIntegrations();
  const counterId = status.analytics_counter_id;
  if (!counterId || typeof window === "undefined") return;
  const target = window as typeof window & { ym?: (...args: unknown[]) => void };
  if (!target.ym) {
    target.ym = (...args: unknown[]) => {
      (target.ym as unknown as { a?: unknown[] }).a =
        (target.ym as unknown as { a?: unknown[] }).a || [];
      (target.ym as unknown as { a: unknown[] }).a.push(args);
    };
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://mc.yandex.ru/metrika/tag.js";
    document.head.appendChild(script);
    target.ym(Number(counterId), "init", {
      clickmap: true,
      trackLinks: true,
      accurateTrackBounce: true,
    });
  }
  target.ym(Number(counterId), "reachGoal", goal, parameters);
}

/* Старые имена оставлены навсегда как синонимы: приложения, опубликованные до
   переименования, содержат вызовы с ними, и перегенерировать их мы не будем. */
export const getOmniaIntegrations = getYleumIntegrations;
export const createOmniaPayment = createYleumPayment;
export const getOmniaPayment = getYleumPayment;
export const createOmniaLead = createYleumLead;
export const getOmniaCatalog = getYleumCatalog;
export const requestOmniaAI = requestYleumAI;
export const trackOmniaGoal = trackYleumGoal;
