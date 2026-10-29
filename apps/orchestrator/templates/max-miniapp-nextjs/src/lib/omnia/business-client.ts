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
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ManagedBusinessError(
      data.error?.code || "unavailable",
      response.status,
      data.quote,
    );
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
