"use client";
import {
  IdentityScopeError, assertIdentityScope, captureIdentityScope, reportIdentityInvalidation,
  type IdentityScope,
} from "@/lib/omnia/identity-scope";
import type {
  ResourceId,
  ManagedRecord,
} from "@/lib/omnia/management-resources";
import type {
  ManagedOrderInput,
  ManagedLeadInput,
  ManagedBookingInput,
} from "@/lib/omnia/business-server";
export type ManagedQuote = {
  configVersion: number;
  items: {
    catalog_id: string;
    title: string;
    quantity: number;
    unit_price_kopecks: number;
    option?: string;
  }[];
  total_kopecks: number;
  currency: "RUB";
};
export class ManagedBusinessError extends Error {
  code: string;
  status: number;
  quote?: ManagedQuote;
  constructor(code: string, status: number, quote?: ManagedQuote) {
    super(
      "Не удалось сохранить обращение. Обновите данные и попробуйте ещё раз.",
    );
    this.code = code;
    this.status = status;
    this.quote = quote;
  }
}
type ManagedInput = ManagedOrderInput | ManagedLeadInput | ManagedBookingInput;
type IntentContext = { actor: string; origin: string; resource: ResourceId; key: string };
type PendingIntent = IntentContext & { input: ManagedInput; version: 1 };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const INTENT_LIMIT = 65536;
// A valid receipt stays acknowledged even when browser cleanup temporarily fails.
// Remember that distinction until the old entry is demonstrably removed.
const acknowledgedIntents = new Map<string, string>();
const intentProvenance = new Map<string, { actor: string; origin: string }>();
function intentError(code = "pending_intent", status = 409): never {
  throw new ManagedBusinessError(code, status);
}
function currentScope(): IdentityScope | null {
  if (typeof window === "undefined") return null;
  try { return captureIdentityScope(); }
  catch (error) {
    if (error instanceof IdentityScopeError) throw new ManagedBusinessError(error.code, error.status);
    throw error;
  }
}
function requireScope(scope: IdentityScope | null): void {
  if (!scope) return;
  try { assertIdentityScope(scope); }
  catch (error) {
    if (error instanceof IdentityScopeError) throw new ManagedBusinessError(error.code, error.status);
    throw error;
  }
}
function unauthorized(scope: IdentityScope | null, status: number): never {
  if (status === 401 && scope) reportIdentityInvalidation(scope);
  return intentError(status === 401 ? "unauthorized" : "forbidden", status);
}
function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every(key => allowed.includes(key));
}
function text(value: unknown, max: number, min = 0): value is string {
  return typeof value === "string" && value.length <= max * 2 &&
    [...value].length >= min && [...value].length <= max && !value.includes("\0");
}
function integer(value: unknown, min = 0): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min;
}
function validInput(resource: ResourceId, value: unknown): value is ManagedInput {
  if (!record(value) || !keys(value, ["operationKey", "customerName", "contactPhone", "contactEmail", "comment",
    ...(resource === "orders" ? ["configVersion", "items", "fulfillment"] : resource === "bookings" ? ["serviceId", "requestedFor"] : [])]) ||
    typeof value.operationKey !== "string" || !UUID.test(value.operationKey) || !text(value.customerName, 120, 1) || !value.customerName.trim()) return false;
  for (const [key, max] of [["contactPhone", 64], ["contactEmail", 254], ["comment", 2000]] as const)
    if (value[key] !== undefined && !text(value[key], max)) return false;
  if (resource === "bookings" && (!text(value.serviceId, 64, 1) || !validTimestamp(value.requestedFor))) return false;
  if (resource === "orders") {
    if (!integer(value.configVersion) || !Array.isArray(value.items) || value.items.length < 1 || value.items.length > 100 ||
      !value.items.every(item => record(item) && keys(item, ["catalogId", "quantity", "unitPriceKopecks", "option"]) &&
        text(item.catalogId, 64, 1) && /^[A-Za-z0-9_-]+$/.test(item.catalogId) && integer(item.quantity, 1) &&
        integer(item.unitPriceKopecks) && (item.option === undefined || text(item.option, 40)))) return false;
    if (value.fulfillment !== undefined && (!record(value.fulfillment) || !keys(value.fulfillment, ["type", "delayMinutes"]) ||
      value.fulfillment.type !== "pickup" || !integer(value.fulfillment.delayMinutes) || value.fulfillment.delayMinutes > 1440)) return false;
  }
  return true;
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (record(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}
function freeze<T>(value: T): Readonly<T> {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function bounded(value: string): boolean {
  return value.length <= INTENT_LIMIT && new TextEncoder().encode(value).byteLength <= INTENT_LIMIT;
}
function body(input: ManagedInput): string {
  const { operationKey: _, ...rest } = input;
  return canonical(rest);
}
function bindIntent(context: IntentContext, operationKey: string): void {
  const key = `${context.resource}:${operationKey.toLowerCase()}`, previous = intentProvenance.get(key);
  if (previous && (previous.actor !== context.actor || previous.origin !== context.origin)) intentError("actor_changed", 409);
  if (!previous) intentProvenance.set(key, { actor: context.actor, origin: context.origin });
}
async function verifiedContext(resource: ResourceId, scope: IdentityScope | null): Promise<IntentContext> {
  if (!["orders", "leads", "bookings"].includes(resource)) intentError("invalid_request", 400);
  let response: Response, data: unknown;
  try {
    response = await fetch("/api/max/session", { credentials: "include", cache: "no-store", redirect: "error" });
  } catch { requireScope(scope); return intentError("unavailable", 503); }
  requireScope(scope);
  if (response.status === 401 || response.status === 403) return unauthorized(scope, response.status);
  try { data = await response.json(); }
  catch { requireScope(scope); return intentError("unavailable", 503); }
  requireScope(scope);
  if (!response.ok) return intentError("unavailable", 503);
  if (!record(data) || !record(data.user) || !text(data.user.id, 128, 1) || data.user.id.length > 128 || !data.user.id.trim() || /[\u0000-\u001f\u007f]/.test(data.user.id)) intentError("unavailable", 503);
  const actor = data.user.id, origin = window.location.origin;
  if (scope && actor !== scope.actor) {
    reportIdentityInvalidation(scope);
    return intentError("actor_changed", 409);
  }
  try { if (!["https:", "http:"].includes(new URL(origin).protocol) || new URL(origin).origin !== origin) intentError("unavailable", 503); }
  catch { return intentError("unavailable", 503); }
  return { actor, origin, resource, key: `yleum.managed-intent.v1:${encodeURIComponent(origin)}:${encodeURIComponent(actor)}:${resource}` };
}
function encoded(context: IntentContext, input: ManagedInput): string {
  const value = canonical({ version: 1, actor: context.actor, origin: context.origin, resource: context.resource, input });
  if (!bounded(value)) intentError("invalid_request", 400);
  return value;
}
function parseIntent(raw: string, context: IntentContext): PendingIntent {
  try {
    if (!bounded(raw)) intentError();
    const value: unknown = JSON.parse(raw);
    if (!record(value) || Object.keys(value).sort().join(",") !== "actor,input,origin,resource,version" || value.version !== 1 ||
      value.actor !== context.actor || value.origin !== context.origin || value.resource !== context.resource || !validInput(context.resource, value.input)) intentError();
    return freeze({ ...context, version: 1 as const, input: freeze(value.input) });
  } catch { return intentError(); }
}
function readIntent(context: IntentContext): { input: ManagedInput | null; stores: Storage[] } {
  const stores: Storage[] = []; let input: ManagedInput | null = null;
  for (const name of ["localStorage", "sessionStorage"] as const) {
    let storage: Storage, raw: string | null;
    try { storage = window[name]; raw = storage.getItem(context.key); } catch { continue; }
    stores.push(storage);
    if (raw !== null) {
      const found = parseIntent(raw, context).input;
      if (input && canonical(input) !== canonical(found)) intentError();
      input = found;
    }
  }
  // A hidden pending entry must never be mistaken for absence. An accessible
  // existing intent can still replay its old key; a fresh intent needs both reads.
  if (!stores.length || (!input && stores.length !== 2)) intentError("intent_storage_unavailable", 503);
  return { input, stores };
}
function persist(context: IntentContext, input: ManagedInput, stores: Storage[], fresh: boolean): string {
  const value = encoded(context, input); let saved = false;
  for (const storage of stores) {
    let previous: string | null;
    try { previous = storage.getItem(context.key); }
    catch { if (fresh) intentError("intent_storage_unavailable", 503); continue; }
    if (previous !== null && encoded(context, parseIntent(previous, context).input) !== value) intentError();
    try {
      storage.setItem(context.key, value);
      if (storage.getItem(context.key) === value) saved = true;
    } catch (error) { if (error instanceof ManagedBusinessError) throw error; }
  }
  if (!saved) intentError("intent_storage_unavailable", 503);
  return value;
}
function clearIntent(context: IntentContext, value: string, stores: Storage[]): boolean {
  let cleared = stores.length === 2;
  for (const storage of stores) {
    try {
      const previous = storage.getItem(context.key);
      if (previous === null) continue;
      if (encoded(context, parseIntent(previous, context).input) !== value) { cleared = false; continue; }
      storage.removeItem(context.key);
      if (storage.getItem(context.key) !== null) cleared = false;
    } catch { cleared = false; }
  }
  return cleared;
}
function pendingIntent(context: IntentContext): ReturnType<typeof readIntent> {
  const pending = readIntent(context), acknowledged = acknowledgedIntents.get(context.key);
  if (!acknowledged) return pending;
  if (pending.input && encoded(context, pending.input) !== acknowledged) intentError();
  if (!clearIntent(context, acknowledged, pending.stores)) intentError("intent_storage_unavailable", 503);
  acknowledgedIntents.delete(context.key);
  return { input: null, stores: pending.stores };
}
/** Load this freshly verified actor's unresolved body; clean known acknowledgements without submitting. */
export function loadPendingManagedIntent(resource: "orders"): Promise<Readonly<ManagedOrderInput> | null>;
export function loadPendingManagedIntent(resource: "leads"): Promise<Readonly<ManagedLeadInput> | null>;
export function loadPendingManagedIntent(resource: "bookings"): Promise<Readonly<ManagedBookingInput> | null>;
export function loadPendingManagedIntent(resource: ResourceId): Promise<Readonly<ManagedInput> | null>;
export async function loadPendingManagedIntent(resource: ResourceId): Promise<Readonly<ManagedInput> | null> {
  if (typeof window === "undefined") return null;
  const scope = currentScope(), context = await verifiedContext(resource, scope);
  requireScope(scope);
  const input = pendingIntent(context).input;
  if (input) bindIntent(context, input.operationKey);
  requireScope(scope);
  return input;
}
async function create(resource: ResourceId, input: ManagedInput): Promise<ManagedRecord> {
  if (typeof window === "undefined") return request(resource, input);
  const scope = currentScope(), context = await verifiedContext(resource, scope);
  requireScope(scope);
  let copy: unknown;
  try { const raw = JSON.stringify(input); if (!bounded(raw)) intentError("invalid_request", 400); copy = JSON.parse(raw); }
  catch { return intentError("invalid_request", 400); }
  if (!validInput(resource, copy)) intentError("invalid_request", 400);
  // A typed object copy retains its operation key, so provenance survives UI
  // reconstruction without trusting object identity or reading another actor's slot.
  bindIntent(context, copy.operationKey);
  const pending = pendingIntent(context);
  if (pending.input) bindIntent(context, pending.input.operationKey);
  if (pending.input && body(pending.input) !== body(copy)) intentError();
  const saved = pending.input || freeze(JSON.parse(canonical(copy)) as ManagedInput), value = persist(context, saved, pending.stores, !pending.input);
  let receipt: ManagedRecord;
  try { receipt = await request(resource, saved, "", context.actor, scope); requireScope(scope); }
  catch (error) {
    if (error instanceof ManagedBusinessError && ["unauthorized", "actor_changed"].includes(error.code)) throw error;
    requireScope(scope);
    if (error instanceof ManagedBusinessError && ((error.code === "quote_changed" && error.status === 409) ||
      (!pending.input && error.code === "invalid_request" && error.status === 400))) clearIntent(context, value, pending.stores);
    throw error;
  }
  acknowledgedIntents.set(context.key, value);
  if (clearIntent(context, value, pending.stores)) acknowledgedIntents.delete(context.key);
  requireScope(scope);
  return receipt;
}
// Validate the receipt before callers may discard their input and operation key.
// Keep this browser module independent of the server registry's node:crypto import.
function validTimestamp(value: unknown): boolean {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts) return false;
  const [year, month, day, hour, minute, second] = parts.slice(1).map(p => Number(p || 0));
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month, 0);
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= calendar.getUTCDate() && hour <= 23 && minute <= 59 && second <= 59;
}
function validFulfillment(value: unknown, resource: ResourceId): boolean {
  if (value === undefined) return true;
  return resource === "orders" && record(value) && Object.keys(value).sort().join(",") === "delay_minutes,scheduled_for,type" &&
    value.type === "pickup" && integer(value.delay_minutes) && value.delay_minutes <= 1440 && validTimestamp(value.scheduled_for);
}
function validRecord(value: unknown, resource: ResourceId): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  if (Object.hasOwn(v, "note")) return false;
  const date = (d: unknown) => typeof d === "string" && /^\d{4}-\d{2}-\d{2}T/.test(d) && Number.isFinite(Date.parse(d));
  return validFulfillment(v.fulfillment, resource) && typeof v.id === "string" && UUID.test(v.id) && v.resource === resource &&
    date(v.created_at) && typeof v.revision === "string" && /^[0-9a-f]{64}$/.test(v.revision) &&
    ["new", "in_progress", "completed", "cancelled"].includes(String(v.status)) &&
    typeof v.customer_name === "string" && v.customer_name.length > 0 &&
    Array.isArray(v.items) && v.items.every(item => item && typeof item.catalog_id === "string" &&
      typeof item.title === "string" && Number.isSafeInteger(item.quantity) && item.quantity > 0 &&
      Number.isSafeInteger(item.unit_price_kopecks) && item.unit_price_kopecks >= 0) &&
    (resource !== "bookings" || (date(v.requested_for) && typeof v.service_title === "string")) &&
    (resource !== "orders" || (Number.isSafeInteger(v.total_kopecks) && Number(v.total_kopecks) >= 0 && v.currency === "RUB"));
}
async function request<T>(
  resource: ResourceId,
  input?: unknown,
  query = "",
  actor?: string,
  scope: IdentityScope | null = currentScope(),
): Promise<T> {
  requireScope(scope);
  let response: Response;
  try {
    response = await fetch(`/api/omnia/business/${resource}${query}`, {
      method: input === undefined ? "GET" : "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...((actor || scope?.actor) ? { "X-Max-Expected-Actor": actor || scope!.actor } : {}) },
      ...(input === undefined ? {} : { body: JSON.stringify(input) }),
    });
  } catch {
    requireScope(scope);
    throw new ManagedBusinessError("unavailable", 503);
  }
  requireScope(scope);
  if (response.status === 401 || response.status === 403) return unauthorized(scope, response.status);
  let data;
  try { data = await response.json(); }
  catch { requireScope(scope); throw new ManagedBusinessError("unavailable", 503); }
  requireScope(scope);
  if (!response.ok) {
    if (response.status === 409 && data?.error?.code === "actor_changed" && scope) reportIdentityInvalidation(scope);
    throw new ManagedBusinessError(
      data?.error?.code || "unavailable",
      response.status,
      data?.quote,
    );
  }
  const valid = input === undefined
    ? data && Array.isArray(data.records) && data.records.length <= 100 &&
      (data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length > 0 && data.next_cursor.length <= 4096)) &&
      data.records.every((record: unknown) => validRecord(record, resource))
    : validRecord(data, resource);
  if (!valid) throw new ManagedBusinessError("unavailable", 503);
  requireScope(scope);
  return data as T;
}
/** Preserve operationKey after ambiguous transport failures. Never auto-confirm a new quote. */
export function createManagedOrder(
  input: ManagedOrderInput,
): Promise<ManagedRecord> {
  return create("orders", input);
}
export function createManagedLead(
  input: ManagedLeadInput,
): Promise<ManagedRecord> {
  return create("leads", input);
}
/** Booking is a request until the owner explicitly confirms it. */
export function createManagedBooking(
  input: ManagedBookingInput,
): Promise<ManagedRecord> {
  return create("bookings", input);
}
export function getManagedRecords(
  resource: ResourceId,
  options: { limit?: number; cursor?: string } = {},
): Promise<{ records: ManagedRecord[]; next_cursor: string | null }> {
  const query = new URLSearchParams();
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  if (options.cursor) query.set("cursor", options.cursor);
  return request(resource, undefined, query.size ? `?${query}` : "");
}
