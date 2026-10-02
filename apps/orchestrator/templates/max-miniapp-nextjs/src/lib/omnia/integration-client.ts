"use client";

import { getMaxWebApp } from "@/lib/max/bridge";
import type { YleumMaxConfig } from "@/lib/omnia/max-config";

/** Owner-maintained app data; independent of MAX login or connected providers. */
export async function getYleumAppConfig(): Promise<YleumMaxConfig> {
  const response = await fetch("/api/omnia/config", {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Данные приложения временно недоступны");
  return response.json() as Promise<YleumMaxConfig>;
}

export class YleumIntegrationError extends Error {
  constructor(message: string, public readonly code: string | null, public readonly status: number) {
    super(message);
    this.name = "YleumIntegrationError";
  }
}

async function invoke<T>(
  path: "status" | "payments" | "payment-status" | "leads" | "lead-list" | "lead-status" | "catalog" | "orders" | "ai",
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
    throw new YleumIntegrationError(
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

async function invokeWrite<T>(path: "leads" | "payments" | "orders", input: Record<string, unknown>): Promise<T> {
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
    if (error instanceof YleumIntegrationError && error.status === 422 && error.code === "integration_request_rejected") {
      window.sessionStorage.removeItem(storageKey);
    }
    throw error;
  }).finally(() => { inFlightWrites.delete(storageKey); });
  inFlightWrites.set(storageKey, pending);
  return pending;
}

export type YleumIntegrationStatus = {
  providers: string[];
  capabilities: string[];
  analytics_counter_id: string | null;
};

export function getYleumIntegrations(): Promise<YleumIntegrationStatus> {
  return invoke("status");
}

export function createYleumPayment(input: {
  amount: number;
  description: string;
  return_url: string;
  idempotency_key?: string;
  metadata?: Record<string, string>;
  receipt?: Record<string, unknown>;
}): Promise<{ id: string; status: string; confirmation_url: string | null }> {
  return invokeWrite("payments", input);
}

export function getYleumPayment(paymentId: string): Promise<{
  id: string;
  status: string;
  confirmation_url: string | null;
}> {
  return invoke("payment-status", { payment_id: paymentId });
}

export function createYleumLead(input: {
  idempotency_key?: string;
  name: string;
  phone?: string;
  email?: string;
  comment?: string;
  source?: string;
}): Promise<{ provider: string; id: string; details_status: "recorded" | "unknown"; warning: string | null }> {
  return invokeWrite("leads", input);
}

export type YleumLeadStatus = {
  provider: "amocrm";
  id: string;
  name: string;
  pipeline_id: number;
  pipeline_name: string;
  status_id: number;
  status_name: string;
  updated_at: number;
  checked_at: string;
  details_status: "recorded" | "unknown";
  warning: string | null;
};

/** Latest 20 receipts belonging to this authenticated MAX user in this project.
 * Statuses are read from amoCRM now. Refresh on focus and show checked_at/errors.
 */
export function getYleumLeads(): Promise<{ items: YleumLeadStatus[]; has_more: boolean }> {
  return invoke("lead-list");
}

/** Authorization is checked against a durable project/user/account receipt. */
export function getYleumLeadStatus(leadId: string): Promise<YleumLeadStatus> {
  return invoke("lead-status", { lead_id: leadId });
}

export function getYleumCatalog(): Promise<{
  provider: string;
  items: Array<{
    id: string;
    name: string;
    description: string;
    price: number | null;
    currency: string;
    available: boolean | null;
    available_quantity: number | null;
    image_url: string | null;
  }>;
}> {
  return invoke("catalog");
}

export function createYleumOrder(input: {
  idempotency_key?: string;
  buyer_name: string;
  phone?: string;
  lines: Array<{ product_id: string; quantity: number }>;
}): Promise<{ provider: "moysklad"; id: string }> {
  return invokeWrite("orders", input);
}

export type MaxActionCreateOptions = {
  /** Stable identity supplied by a caller that owns the submission's lifetime. */
  operationKey?: string;
  /** Start a separate deliberate intent even when an earlier result is unknown. */
  newIntent?: boolean;
};

const activeActionWrites = new Set<string>();

type PendingAction = { key: string };

async function actionBrowserScope(): Promise<{ actor: string; scope: string }> {
  const response = await fetch("/api/max/session", { credentials: "include", cache: "no-store" });
  const body = await response.json();
  if (!response.ok || typeof body?.user?.id !== "string" || !body.user.id) {
    throw new YleumIntegrationError("MAX authentication required", null, response.status);
  }
  return { actor: body.user.id, scope: `${window.location.origin}:${body.user.id}` };
}

async function readActionResponse(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json();
  if (!response.ok) {
    throw new YleumIntegrationError(typeof body?.error === "string" ? body.error : "Action save failed",
      typeof body?.code === "string" ? body.code : null, response.status);
  }
  return body as Record<string, unknown>;
}

export async function createMaxAction(
  actionType: string,
  payload: Record<string, unknown> = {},
  options: MaxActionCreateOptions = {},
): Promise<Record<string, unknown>> {
  // JSON defines the durable request, including Date/toJSON and omitted fields.
  // Fingerprint and send the same normalized wire value exactly once.
  const wireInput = JSON.parse(JSON.stringify({ actionType, payload })) as {
    actionType: string; payload: Record<string, unknown>;
  };
  const { actor, scope } = await actionBrowserScope();
  let storageKey: string | null = null;
  let operationKey = options.operationKey;
  function pending(): PendingAction[] {
    const entries: unknown = JSON.parse(window.sessionStorage.getItem(storageKey!) || "[]");
    if (!Array.isArray(entries) || entries.some(item => typeof item?.key !== "string")) {
      throw new Error("Invalid pending action identities");
    }
    return entries;
  }
  function forget() {
    if (!storageKey) return;
    const remaining = pending().filter(item => item.key !== operationKey);
    if (remaining.length) window.sessionStorage.setItem(storageKey, JSON.stringify(remaining));
    else window.sessionStorage.removeItem(storageKey);
  }
  if (!operationKey) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
      JSON.stringify(canonical(wireInput)),
    ));
    const fingerprint = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, "0")).join("");
    storageKey = `omnia:action:${scope}:${fingerprint}`;
    const entries = pending();
    const uncertain = entries.filter(item => !activeActionWrites.has(item.key));
    if (!options.newIntent && uncertain.length > 1) {
      throw new Error("Several action outcomes are unknown; provide an explicit operation identity");
    }
    operationKey = !options.newIntent && uncertain.length === 1 ? uncertain[0].key : crypto.randomUUID();
    if (!entries.some(item => item.key === operationKey)) entries.push({ key: operationKey });
    // Persist before sending; unavailable storage must fail without a write.
    window.sessionStorage.setItem(storageKey, JSON.stringify(entries));
  }
  activeActionWrites.add(operationKey);
  try {
    const response = await fetch("/api/omnia/actions", {
      method: "POST", credentials: "include", headers: {
        "Content-Type": "application/json", "X-Omnia-Actor": actor,
      },
      body: JSON.stringify({ ...wireInput, operationKey }),
    });
    const body = await readActionResponse(response);
    if (!body.action || typeof (body.action as Record<string, unknown>).id !== "string") {
      throw new Error("Action outcome could not be confirmed");
    }
    forget();
    return body;
  } finally {
    // A later rejection cannot prove that an earlier uncertain attempt never
    // committed. Retain its identity until success or an explicit new intent.
    activeActionWrites.delete(operationKey);
  }
}

/** Preserve this returned revision for the entire edit lifetime. */
export async function getMaxAction(id: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/omnia/actions/${encodeURIComponent(id)}`, {
    credentials: "include", cache: "no-store",
  });
  return readActionResponse(response);
}

/** A stale revision is an explicit conflict; reload and reconcile the edit. */
export async function updateMaxAction(id: string, input: {
  actionType?: string; status?: string; payload?: Record<string, unknown>;
}, expectedRevision: string): Promise<Record<string, unknown>> {
  const response = await fetch(`/api/omnia/actions/${encodeURIComponent(id)}`, {
    method: "PATCH", credentials: "include", headers: {
      "Content-Type": "application/json", "If-Match": expectedRevision,
    }, body: JSON.stringify(input),
  });
  return readActionResponse(response);
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
  const result = await invoke<{ answer: string; model: string }>("ai", {
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
export const getOmniaAppConfig = getYleumAppConfig;
export const OmniaIntegrationError = YleumIntegrationError;
export type OmniaIntegrationStatus = YleumIntegrationStatus;
export const getOmniaIntegrations = getYleumIntegrations;
export const createOmniaPayment = createYleumPayment;
export const getOmniaPayment = getYleumPayment;
export const createOmniaLead = createYleumLead;
export const getOmniaCatalog = getYleumCatalog;
export const createOmniaOrder = createYleumOrder;
export const requestOmniaAI = requestYleumAI;
export const trackOmniaGoal = trackYleumGoal;
