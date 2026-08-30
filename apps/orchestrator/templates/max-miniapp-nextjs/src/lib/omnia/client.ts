"use client";

import { getMaxWebApp } from "@/lib/max/bridge";

async function post(path: string, payload: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function trackMaxEvent(
  eventName: string,
  properties: Record<string, unknown> = {},
): Promise<void> {
  const response = await post("/api/omnia/events", { eventName, properties });
  if (!response.ok && response.status !== 401) throw new Error("Analytics event failed");
}

export async function saveMaxConsent(
  consentType: string,
  granted: boolean,
  policyVersion = "1",
): Promise<void> {
  const response = await post("/api/omnia/consents", {
    consentType,
    granted,
    policyVersion,
  });
  if (!response.ok) throw new Error("Consent save failed");
}

export async function createMaxAction(
  actionType: string,
  payload: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const response = await post("/api/omnia/actions", { actionType, payload });
  if (!response.ok) throw new Error("Action save failed");
  return response.json() as Promise<Record<string, unknown>>;
}

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
// same authenticated, tenant-filtered MAX Studio endpoint.
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

export function getOmniaIntegrations(): Promise<{
  providers: string[];
  capabilities: string[];
  analytics_counter_id: string | null;
}> {
  return integration("status");
}

export function createOmniaPayment(input: {
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

export function getOmniaPayment(paymentId: string): Promise<{
  id: string;
  status: string;
  confirmation_url: string | null;
}> {
  return integration("payment-status", { payment_id: paymentId });
}

export function createOmniaLead(input: {
  name: string;
  phone?: string;
  email?: string;
  comment?: string;
  source?: string;
}): Promise<{ provider: string; id: string }> {
  return integration("leads", input);
}

export function getOmniaCatalog(): Promise<{
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

type OmniaAIInput = {
  message?: string;
  prompt?: string;
  instructions?: string;
  context?: Record<string, unknown>;
};

export async function requestOmniaAI(
  input: OmniaAIInput,
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

export async function trackOmniaGoal(
  goal: string,
  parameters: Record<string, unknown> = {},
): Promise<void> {
  const status = await getOmniaIntegrations();
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
