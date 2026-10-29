import { createHash } from "node:crypto";

export type ResourceId = "orders" | "leads" | "bookings";
export type OwnerStatus = "new" | "in_progress" | "completed" | "cancelled";
export type ManagedItem = {
  catalog_id: string;
  title: string;
  quantity: number;
  unit_price_kopecks: number;
  option?: string;
};
export type ManagedOrderFulfillment = {
  type: "pickup";
  delay_minutes: number;
  scheduled_for: string;
};
export type ManagedSnapshot = {
  management_protocol: 1;
  resource: ResourceId;
  customer_name: string;
  contact_phone?: string;
  contact_email?: string;
  comment?: string;
  requested_for?: string;
  service_title?: string;
  service_price_label?: string;
  items: ManagedItem[];
  total_kopecks?: number;
  currency?: "RUB";
  fulfillment?: ManagedOrderFulfillment;
};
export type ResourceDefinition = {
  id: ResourceId;
  label: string;
  singular_label: string;
  action_type: "order_created" | "lead_created" | "booking_created";
  fields: {
    key: string;
    label: string;
    kind: string;
    readonly: boolean;
    editable: boolean;
    owner_only: boolean;
  }[];
  status_labels: Record<OwnerStatus, string>;
  transitions: Record<OwnerStatus, OwnerStatus[]>;
};
export type ManagementContract = {
  protocol: 1;
  modules: string[];
  resources: ResourceDefinition[];
  contract_hash: string;
};
export type ManagedRecord = Omit<ManagedSnapshot, "management_protocol"> & {
  id: string;
  created_at: string;
  revision: string;
  status: OwnerStatus;
  note?: string;
};
export type RecordsQuery = {
  resource: ResourceId;
  limit: number;
  cursor?: string;
  query: string;
  status?: OwnerStatus;
  created_from?: string;
  created_to?: string;
  requested_from?: string;
  requested_to?: string;
};
export type RecordUpdate = {
  expected_revision: string;
  operation_key: string;
  status?: OwnerStatus;
  note?: string;
};
export class ManagementError extends Error {
  code: string;
  status: number;
  quote?: unknown;
  constructor(code: string, status = 400, quote?: unknown) {
    super("Операция недоступна. Обновите данные и попробуйте ещё раз.");
    this.code = code;
    this.status = status;
    this.quote = quote;
  }
}
export const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const HASH = /^[a-f0-9]{64}$/;
export const ACTION_TYPES = {
  orders: "order_created",
  leads: "lead_created",
  bookings: "booking_created",
} as const;
const statuses: OwnerStatus[] = [
  "new",
  "in_progress",
  "completed",
  "cancelled",
];
const field = (key: string, label: string, kind: string) => ({
  key,
  label,
  kind,
  readonly: !["status", "note"].includes(key),
  editable: ["status", "note"].includes(key),
  owner_only: key === "note",
});
const common = () => [
  field("created_at", "Создана", "date"),
  field("status", "Статус", "status"),
  field("customer_name", "Клиент", "text"),
  field("contact_phone", "Телефон", "text"),
  field("contact_email", "Email", "text"),
];
const tail = () => [
  field("comment", "Комментарий клиента", "text"),
  field("note", "Заметка владельца", "note"),
];
function definition(id: ResourceId): ResourceDefinition {
  const transitions = {
    new: ["in_progress", "cancelled"],
    in_progress: ["completed", "cancelled"],
    completed: [],
    cancelled: [],
  } as Record<OwnerStatus, OwnerStatus[]>;
  if (id === "orders")
    return {
      id,
      label: "Заказы",
      singular_label: "Заказ",
      action_type: ACTION_TYPES[id],
      fields: [
        ...common(),
        field("items", "Состав заказа", "items"),
        field("total_kopecks", "Сумма", "money"),
        ...tail(),
      ],
      status_labels: {
        new: "Новый",
        in_progress: "В работе",
        completed: "Выполнен",
        cancelled: "Отменён",
      },
      transitions,
    };
  return {
    id,
    label: id === "leads" ? "Заявки" : "Записи",
    singular_label: id === "leads" ? "Заявка" : "Запись",
    action_type: ACTION_TYPES[id],
    fields: [
      ...common(),
      ...(id === "bookings"
        ? [
            field("requested_for", "Желаемая дата", "date"),
            field("service_title", "Услуга", "text"),
            field("service_price_label", "Стоимость услуги", "text"),
          ]
        : []),
      ...tail(),
    ],
    status_labels: {
      new: "Новая",
      in_progress: id === "bookings" ? "Подтверждена" : "В работе",
      completed: "Завершена",
      cancelled: "Отменена",
    },
    transitions,
  };
}
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map(
        (k) =>
          `${JSON.stringify(k)}:${canonicalJson((value as Record<string, unknown>)[k])}`,
      )
      .join(",")}}`;
  return JSON.stringify(value);
}
export function managementHash(value: unknown): string {
  const copy = { ...(value as Record<string, unknown>) };
  delete copy.contract_hash;
  return createHash("sha256").update(canonicalJson(copy)).digest("hex");
}
export function object(
  value: unknown,
  allowed: string[],
): Record<string, unknown> {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((k) => !allowed.includes(k))
  )
    throw new ManagementError("invalid_request");
  return value as Record<string, unknown>;
}
export function resourceId(value: unknown): ResourceId {
  if (typeof value !== "string" || !Object.hasOwn(ACTION_TYPES, value))
    throw new ManagementError("invalid_request");
  return value as ResourceId;
}
export function boundedText(value: unknown, max: number, min = 0): string {
  if (
    typeof value !== "string" ||
    [...value].length > max ||
    [...value].length < min ||
    value.includes("\u0000")
  )
    throw new ManagementError("invalid_request");
  return value;
}
export function safeInteger(value: unknown, min = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min)
    throw new ManagementError("invalid_request");
  return value;
}
export function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value))
    throw new ManagementError("invalid_request");
  return value.toLowerCase();
}
export function isoDate(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/.test(
      value,
    ) ||
    !Number.isFinite(Date.parse(value))
  )
    throw new ManagementError("invalid_request");
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month, 0);
  if (month < 1 || month > 12 || day < 1 || day > calendar.getUTCDate())
    throw new ManagementError("invalid_request");
  return new Date(value).toISOString();
}
export function parseManagementContract(value: unknown): ManagementContract {
  try {
    const v = object(value, [
      "protocol",
      "modules",
      "resources",
      "contract_hash",
    ]);
    if (v.protocol !== 1 || !Array.isArray(v.modules)) throw Error();
    const modules = ["catalog", "orders", "leads", "bookings"].filter((m) =>
      (v.modules as unknown[]).includes(m),
    );
    if (
      canonicalJson(modules) !== canonicalJson(v.modules) ||
      ((modules.includes("orders") || modules.includes("bookings")) &&
        !modules.includes("catalog"))
    )
      throw Error();
    const expected = {
      protocol: 1 as const,
      modules,
      resources: (["orders", "leads", "bookings"] as ResourceId[])
        .filter((id) => modules.includes(id))
        .map(definition),
    };
    const contract = { ...expected, contract_hash: managementHash(expected) };
    if (canonicalJson(contract) !== canonicalJson(v)) throw Error();
    return contract;
  } catch {
    throw new ManagementError("unsupported", 409);
  }
}
export function requireResource(
  contract: ManagementContract,
  id: unknown,
): ResourceId {
  const resource = resourceId(id);
  if (!contract.resources.some((r) => r.id === resource))
    throw new ManagementError("unsupported", 409);
  return resource;
}
export function parseRecordsQuery(value: unknown): RecordsQuery {
  const v = object(value, [
    "resource",
    "limit",
    "cursor",
    "query",
    "status",
    "created_from",
    "created_to",
    "requested_from",
    "requested_to",
  ]);
  const result: RecordsQuery = {
    resource: resourceId(v.resource),
    limit: v.limit === undefined ? 25 : safeInteger(v.limit, 1),
    query: v.query === undefined ? "" : boundedText(v.query, 120),
  };
  if (result.limit > 100) throw new ManagementError("invalid_request");
  if (v.cursor != null) result.cursor = boundedText(v.cursor, 4096, 1);
  if (v.status != null) {
    if (!statuses.includes(v.status as OwnerStatus))
      throw new ManagementError("invalid_request");
    result.status = v.status as OwnerStatus;
  }
  for (const [from, to] of [["created_from", "created_to"], ["requested_from", "requested_to"]] as const) {
    if (from === "requested_from" && result.resource !== "bookings" && (v[from] != null || v[to] != null))
      throw new ManagementError("invalid_request");
    if (v[from] != null) result[from] = isoDate(v[from]);
    if (v[to] != null) result[to] = isoDate(v[to]);
    if (result[from] && result[to] && result[from]! > result[to]!)
      throw new ManagementError("invalid_request");
  }
  return result;
}
export function parseRecordUpdate(value: unknown): RecordUpdate {
  const v = object(value, [
    "expected_revision",
    "operation_key",
    "status",
    "note",
  ]);
  if (
    typeof v.expected_revision !== "string" ||
    !HASH.test(v.expected_revision)
  )
    throw new ManagementError("invalid_request");
  const result: RecordUpdate = {
    expected_revision: v.expected_revision,
    operation_key: uuid(v.operation_key),
  };
  if (v.status != null) {
    if (!statuses.includes(v.status as OwnerStatus))
      throw new ManagementError("invalid_request");
    result.status = v.status as OwnerStatus;
  }
  if (v.note != null) result.note = boundedText(v.note, 2000);
  if (result.status === undefined && result.note === undefined)
    throw new ManagementError("invalid_request");
  return result;
}
export function allowedTransition(
  resource: ResourceId,
  from: OwnerStatus,
  to: OwnerStatus,
): boolean {
  return from === to || definition(resource).transitions[from].includes(to);
}
export function projectManagedRecord(
  row: Record<string, unknown>,
  owner: boolean,
): ManagedRecord {
  const payload = row.payload as ManagedSnapshot;
  const result: Record<string, unknown> = {
    id: row.id,
    resource: row.resource,
    created_at: new Date(row.created_at as string).toISOString(),
    revision: row.revision,
    status: row.status,
    items: [],
  };
  for (const key of [
    "customer_name",
    "contact_phone",
    "contact_email",
    "comment",
    "requested_for",
    "service_title",
    "service_price_label",
    "total_kopecks",
    "currency",
  ] as const)
    if (payload[key] !== undefined) result[key] = payload[key];
  result.items = (payload.items || []).map((item) => ({
    catalog_id: item.catalog_id,
    title: item.title,
    quantity: item.quantity,
    unit_price_kopecks: item.unit_price_kopecks,
    ...(item.option !== undefined ? { option: item.option } : {}),
  }));
  if (payload.fulfillment !== undefined) {
    try {
      const pickup = object(payload.fulfillment, [
        "type", "delay_minutes", "scheduled_for",
      ]);
      const delay = safeInteger(pickup.delay_minutes);
      if (row.resource !== "orders" || pickup.type !== "pickup" || delay > 1440)
        throw Error();
      result.fulfillment = {
        type: "pickup", delay_minutes: delay,
        scheduled_for: isoDate(pickup.scheduled_for),
      };
    } catch {
      throw new ManagementError("unavailable", 503);
    }
  }
  if (owner) result.note = row.note ?? "";
  return result as ManagedRecord;
}
export function managementResponse(error: unknown): Response {
  const e =
    error instanceof ManagementError
      ? error
      : new ManagementError("unavailable", 503);
  return Response.json(
    {
      error: { code: e.code, message: e.message },
      ...(e.quote ? { quote: e.quote } : {}),
    },
    { status: e.status, headers: { "Cache-Control": "no-store" } },
  );
}
export async function readManagementBody(
  request: Request,
  limit = 65536,
): Promise<Buffer> {
  const reader = request.body?.getReader();
  if (!reader) return Buffer.alloc(0);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new ManagementError("invalid_request");
      }
      chunks.push(value);
    }
    return Buffer.concat(chunks);
  } finally {
    reader.releaseLock();
  }
}
export function parseManagementBody(body: Buffer): unknown {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    throw new ManagementError("invalid_request");
  }
}
