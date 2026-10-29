import { apiFetch, ApiError } from "./client";
import type { ManagementModule } from "./types";

export type ResourceId = "orders" | "leads" | "bookings";
export type OwnerStatus = "new" | "in_progress" | "completed" | "cancelled";
export type OwnerRecordItem = { catalog_id: string; title: string; quantity: number; unit_price_kopecks: number; option?: string | null };
export type OwnerRecord = {
  id: string; resource: ResourceId; created_at: string; revision: string; status: OwnerStatus;
  customer_name: string; contact_phone?: string | null; contact_email?: string | null;
  comment?: string | null; requested_for?: string | null; service_title?: string | null;
  service_price_label?: string | null; items: OwnerRecordItem[]; total_kopecks?: number | null;
  currency?: "RUB" | null; note?: string | null;
};
export type ResourceDefinition = {
  id: ResourceId; label: string; singular_label: string;
  action_type: "order_created" | "lead_created" | "booking_created";
  fields: { key: keyof OwnerRecord; label: string; kind: "text" | "date" | "money" | "items" | "status" | "note"; readonly: boolean; editable: boolean; owner_only: boolean }[];
  status_labels: Record<OwnerStatus, string>; transitions: Record<OwnerStatus, OwnerStatus[]>;
};
export type ManagementOverview = {
  state: "active" | "not_published" | "unsupported" | "unavailable";
  configured_modules: ManagementModule[]; active_modules: ManagementModule[];
  resources: ResourceDefinition[]; publication_id: string | null; contract_hash: string | null; catalog_limit: 100;
};
export type OwnerRecordsQuery = {
  resource: ResourceId; limit?: number; cursor?: string; query?: string; status?: OwnerStatus;
  created_from?: string; created_to?: string; requested_from?: string; requested_to?: string;
};
export type OwnerRecordsPage = { records: OwnerRecord[]; next_cursor: string | null };
export type OwnerRecordUpdate = { expected_revision: string; operation_key: string; status?: OwnerStatus; note?: string };
const path = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/max/management`;

export function getMaxManagement(projectId: string, signal?: AbortSignal): Promise<ManagementOverview> {
  return apiFetch(path(projectId), { signal, timeoutMs: 15_000 });
}
export function getMaxOwnerRecords(projectId: string, query: OwnerRecordsQuery, signal?: AbortSignal): Promise<OwnerRecordsPage> {
  const params = new URLSearchParams({ resource: query.resource, limit: String(query.limit ?? 25) });
  for (const [key, value] of Object.entries(query)) if (key !== "resource" && key !== "limit" && value !== undefined && value !== "") params.set(key, String(value));
  return apiFetch(`${path(projectId)}/records?${params}`, { signal, timeoutMs: 15_000 });
}
export function updateMaxOwnerRecord(projectId: string, resource: ResourceId, recordId: string, update: OwnerRecordUpdate): Promise<OwnerRecord> {
  return apiFetch(`${path(projectId)}/records/${encodeURIComponent(recordId)}?resource=${resource}`, { method: "PATCH", json: update, timeoutMs: 20_000 });
}
export function ownerRequestError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Нет соединения или ответ не получен. Проверьте интернет и повторите попытку.";
    if (error.status === 401 || error.status === 403) return "Нет доступа. Войдите в Yleum с аккаунтом владельца приложения.";
    if (String(error.code) === "unavailable" || error.status === 503) return "Рабочее приложение сейчас недоступно. Повторите попытку позже.";
    if (String(error.code) === "unsupported") return "Опубликованное приложение пока не поддерживает этот раздел управления.";
    if (String(error.code) === "operation_conflict") return "Эта попытка уже относится к другим изменениям. Загрузите актуальную версию перед новой правкой.";
  }
  return "Не удалось выполнить запрос. Ваши правки остались в форме. Повторите попытку.";
}

/** Format integer kopecks without rounding away the last kopeck of a large amount. */
export function ownerMoney(kopecks: number): string {
  const whole = BigInt(kopecks);
  return `${(whole / 100n).toLocaleString("ru-RU")},${String(whole % 100n).padStart(2, "0")} ₽`;
}
