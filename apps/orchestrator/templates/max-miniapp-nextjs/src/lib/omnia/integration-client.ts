"use client";

import { getMaxWebApp } from "@/lib/max/bridge";
import type { OmniaMaxConfig } from "@/lib/omnia/max-config";

/** Owner-maintained app data; independent of MAX login or connected providers. */
export async function getOmniaAppConfig(): Promise<OmniaMaxConfig> {
  const response = await fetch("/api/omnia/config", {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Данные приложения временно недоступны");
  return response.json() as Promise<OmniaMaxConfig>;
}

export class OmniaIntegrationError extends Error {
  constructor(message: string, public readonly code: string | null, public readonly status: number) {
    super(message);
    this.name = "OmniaIntegrationError";
  }
}

async function invoke<T>(
  path: "status" | "payments" | "payment-status" | "leads" | "catalog" | "ai",
  payload: Record<string, unknown> = {},
): Promise<T> {
  const initData = getMaxWebApp()?.initData;
  const response = await fetch(`/api/omnia/integrations/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData: initData || "", payload }),
    credentials: "same-origin",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new OmniaIntegrationError(
      typeof body?.error?.message === "string" ? body.error.message : "Интеграция временно недоступна",
      typeof body?.error?.code === "string" ? body.error.code : null,
      response.status,
    );
  }
  return body as T;
}

// Keep an operation identity through a lost response/reload without persisting
// customer data. A fresh confirmed submission gets a new identity.
const inFlightWrites = new Map<string, Promise<unknown>>();

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, canonical(item)]));
  }
  return value;
}

async function invokeWrite<T>(path: "leads" | "payments", input: Record<string, unknown>): Promise<T> {
  if (input.idempotency_key) return invoke<T>(path, input);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
    JSON.stringify(canonical(input)),
  ));
  const fingerprint = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, "0")).join("");
  const storageKey = `omnia:integration:${path}:${fingerprint}`;
  const active = inFlightWrites.get(storageKey);
  if (active) return active as Promise<T>;
  // Fail before sending if durable browser state cannot be saved. Sending and
  // forgetting the key would permit duplicate effects after a reload.
  let operationKey = window.sessionStorage.getItem(storageKey);
  if (!operationKey) {
    operationKey = crypto.randomUUID();
    window.sessionStorage.setItem(storageKey, operationKey);
  }
  const pending = invoke<T>(path, {...input, idempotency_key: operationKey}).then(result => {
    window.sessionStorage.removeItem(storageKey);
    return result;
  }).catch(error => {
    // Only an explicit, confirmed rejection makes a new deliberate submission
    // safe. Network failures and unknown outcomes must retain their identity.
    if (error instanceof OmniaIntegrationError && error.status === 422 && error.code === "integration_request_rejected") {
      window.sessionStorage.removeItem(storageKey);
    }
    throw error;
  }).finally(() => { inFlightWrites.delete(storageKey); });
  inFlightWrites.set(storageKey, pending);
  return pending;
}

export type OmniaIntegrationStatus = {
  providers: string[];
  capabilities: string[];
  analytics_counter_id: string | null;
};

export function getOmniaIntegrations(): Promise<OmniaIntegrationStatus> {
  return invoke("status");
}

export function createOmniaPayment(input: {
  amount: number;
  description: string;
  return_url: string;
  idempotency_key?: string;
  metadata?: Record<string, string>;
  receipt?: Record<string, unknown>;
}): Promise<{ id: string; status: string; confirmation_url: string | null }> {
  return invokeWrite("payments", input);
}

export function getOmniaPayment(paymentId: string): Promise<{
  id: string;
  status: string;
  confirmation_url: string | null;
}> {
  return invoke("payment-status", { payment_id: paymentId });
}

export function createOmniaLead(input: {
  idempotency_key?: string;
  name: string;
  phone?: string;
  email?: string;
  comment?: string;
  source?: string;
}): Promise<{ provider: string; id: string }> {
  return invokeWrite("leads", input);
}

export function getOmniaCatalog(): Promise<{
  provider: string;
  items: Array<{
    id: string;
    name: string;
    description: string;
    price: number | null;
    currency: string;
    available: boolean | null;
    image_url: string | null;
  }>;
}> {
  return invoke("catalog");
}

export async function createMaxAction(
  actionType: string,
  payload: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const response = await fetch("/api/omnia/actions", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actionType, payload }),
  });
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

export const getActionHistory = getMaxActions;

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
  const result = await invoke<{ answer: string; model: string }>("ai", {
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
