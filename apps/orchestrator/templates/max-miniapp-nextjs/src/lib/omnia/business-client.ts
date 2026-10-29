"use client";
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
// Validate the receipt before callers may discard their input and operation key.
// Keep this browser module independent of the server registry's node:crypto import.
function validRecord(value: unknown, resource: ResourceId): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const date = (d: unknown) => typeof d === "string" && /^\d{4}-\d{2}-\d{2}T/.test(d) && Number.isFinite(Date.parse(d));
  return typeof v.id === "string" && uuid.test(v.id) && v.resource === resource &&
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
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/omnia/business/${resource}${query}`, {
      method: input === undefined ? "GET" : "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      ...(input === undefined ? {} : { body: JSON.stringify(input) }),
    });
  } catch {
    throw new ManagedBusinessError("unavailable", 503);
  }
  let data;
  try { data = await response.json(); }
  catch { throw new ManagedBusinessError("unavailable", 503); }
  if (!response.ok)
    throw new ManagedBusinessError(
      data?.error?.code || "unavailable",
      response.status,
      data?.quote,
    );
  const valid = input === undefined
    ? data && Array.isArray(data.records) && data.records.length <= 100 &&
      (data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length > 0 && data.next_cursor.length <= 4096)) &&
      data.records.every((record: unknown) => validRecord(record, resource))
    : validRecord(data, resource);
  if (!valid) throw new ManagedBusinessError("unavailable", 503);
  return data as T;
}
/** Preserve operationKey after ambiguous transport failures. Never auto-confirm a new quote. */
export function createManagedOrder(
  input: ManagedOrderInput,
): Promise<ManagedRecord> {
  return request("orders", input);
}
export function createManagedLead(
  input: ManagedLeadInput,
): Promise<ManagedRecord> {
  return request("leads", input);
}
/** Booking is a request until the owner explicitly confirms it. */
export function createManagedBooking(
  input: ManagedBookingInput,
): Promise<ManagedRecord> {
  return request("bookings", input);
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
