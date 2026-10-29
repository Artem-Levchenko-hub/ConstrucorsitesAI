import {
  ManagementError,
  object,
  resourceId,
  boundedText,
  safeInteger,
  uuid,
  isoDate,
} from "@/lib/omnia/management-resources";
import type {
  ManagedSnapshot,
  ManagedItem,
  ResourceId,
} from "@/lib/omnia/management-resources";
import type { YleumMaxConfig } from "@/lib/omnia/max-config";
export type ContactInput = {
  operationKey: string;
  customerName: string;
  contactPhone?: string;
  contactEmail?: string;
  comment?: string;
};
export type ManagedOrderInput = ContactInput & {
  configVersion: number;
  items: {
    catalogId: string;
    quantity: number;
    unitPriceKopecks: number;
    option?: string;
  }[];
};
export type ManagedBookingInput = ContactInput & {
  serviceId: string;
  requestedFor: string;
};
export type ManagedLeadInput = ContactInput;
type Catalog = {
  configVersion: number;
  config: Pick<YleumMaxConfig, "content">;
};
/** Strict numeric RUB, never guess a zero or interpret a textual quote as money. */
export function parseRubPrice(value: unknown): number {
  if (typeof value !== "string") throw new ManagementError("invalid_request");
  const match = value
    .trim()
    .match(/^(\d+)(?:[.,](\d{2}))?\s*(?:₽|руб\.?|RUB)?$/i);
  if (!match) throw new ManagementError("invalid_request");
  const amount =
    BigInt(match[1]) * 100n + BigInt((match[2] || "").padEnd(2, "0"));
  if (amount > BigInt(Number.MAX_SAFE_INTEGER))
    throw new ManagementError("invalid_request");
  return Number(amount);
}
export function validateManagedInput(
  resource: ResourceId,
  input: unknown,
): Record<string, unknown> {
  const common = [
    "operationKey",
    "customerName",
    "contactPhone",
    "contactEmail",
    "comment",
  ];
  const value = object(input, [
    ...common,
    ...(resource === "orders"
      ? ["configVersion", "items"]
      : resource === "bookings"
        ? ["serviceId", "requestedFor"]
        : []),
  ]);
  const result: Record<string, unknown> = {
    operationKey: uuid(value.operationKey),
    customerName: boundedText(value.customerName, 120, 1),
  };
  if (!(result.customerName as string).trim())
    throw new ManagementError("invalid_request");
  for (const [key, max] of [
    ["contactPhone", 64],
    ["contactEmail", 254],
    ["comment", 2000],
  ] as const)
    if (value[key] !== undefined) result[key] = boundedText(value[key], max);
  if (resource === "bookings") {
    result.serviceId = boundedText(value.serviceId, 64, 1);
    result.requestedFor = isoDate(value.requestedFor);
  }
  if (resource === "orders") {
    result.configVersion = safeInteger(value.configVersion);
    if (
      !Array.isArray(value.items) ||
      !value.items.length ||
      value.items.length > 100
    )
      throw new ManagementError("invalid_request");
    result.items = value.items.map((item) => {
      const i = object(item, [
        "catalogId",
        "quantity",
        "unitPriceKopecks",
        "option",
      ]);
      const catalogId = boundedText(i.catalogId, 64, 1);
      if (!/^[A-Za-z0-9_-]+$/.test(catalogId))
        throw new ManagementError("invalid_request");
      return {
        catalogId,
        quantity: safeInteger(i.quantity, 1),
        unitPriceKopecks: safeInteger(i.unitPriceKopecks),
        ...(i.option !== undefined
          ? { option: boundedText(i.option, 40) }
          : {}),
      };
    });
  }
  return result;
}
export function prepareManagedSnapshot(
  resource: ResourceId,
  input: unknown,
  catalog?: Catalog,
): ManagedSnapshot {
  const v = validateManagedInput(resource, input);
  const snapshot: ManagedSnapshot = {
    management_protocol: 1,
    resource,
    customer_name: v.customerName as string,
    items: [],
  };
  for (const [from, to] of [
    ["contactPhone", "contact_phone"],
    ["contactEmail", "contact_email"],
    ["comment", "comment"],
  ] as const)
    if (v[from] !== undefined) snapshot[to] = v[from] as string;
  if (resource === "leads") return snapshot;
  if (!catalog) throw new ManagementError("unavailable", 503);
  const find = (id: string) => {
    const item = catalog.config.content.find((i) => i.id === id);
    if (!item || !item.active || item.availability === "out_of_stock")
      throw new ManagementError("invalid_request");
    return item;
  };
  if (resource === "bookings") {
    const item = find(v.serviceId as string);
    snapshot.requested_for = v.requestedFor as string;
    snapshot.service_title = item.title;
    if (item.price) snapshot.service_price_label = item.price;
    return snapshot;
  }
  let changed = v.configVersion !== catalog.configVersion,
    total = 0;
  snapshot.items = (v.items as ManagedOrderInput["items"]).map((i) => {
    const item = find(i.catalogId);
    if (
      (item.options.length > 0 && !i.option) ||
      (i.option !== undefined && !item.options.includes(i.option))
    )
      throw new ManagementError("invalid_request");
    const price = parseRubPrice(item.price);
    if (i.unitPriceKopecks !== price) changed = true;
    const line = price * i.quantity;
    if (!Number.isSafeInteger(line) || !Number.isSafeInteger(total + line))
      throw new ManagementError("invalid_request");
    total += line;
    return {
      catalog_id: item.id,
      title: item.title,
      quantity: i.quantity,
      unit_price_kopecks: price,
      ...(i.option !== undefined ? { option: i.option } : {}),
    } as ManagedItem;
  });
  snapshot.total_kopecks = total;
  snapshot.currency = "RUB";
  if (changed)
    throw new ManagementError("quote_changed", 409, {
      configVersion: catalog.configVersion,
      items: snapshot.items,
      total_kopecks: total,
      currency: "RUB",
    });
  return snapshot;
}
export async function createManagedBusinessRecord(
  actor: string,
  resource: ResourceId,
  input: unknown,
) {
  const validated = validateManagedInput(resourceId(resource), input);
  const { createManagedRecord } = await import("@/lib/omnia/management-store");
  const result = await createManagedRecord(
    actor,
    resource,
    validated,
    async () => {
      const catalog =
        resource === "leads"
          ? undefined
          : await (
              await import("@/lib/omnia/catalog-server")
            ).getYleumServerAppConfig();
      return prepareManagedSnapshot(resource, validated, catalog);
    },
  );
  if (result.created) {
    try {
      const { forwardMaxAnalytics } = await import("@/lib/omnia/analytics");
      await forwardMaxAnalytics(
        actor,
        result.record.id,
        "action",
        resource === "orders"
          ? "order_created"
          : resource === "leads"
            ? "lead_created"
            : "booking_created",
      );
    } catch {
      /* Durable business success is independent of analytics delivery. */
    }
  }
  return result.record;
}
