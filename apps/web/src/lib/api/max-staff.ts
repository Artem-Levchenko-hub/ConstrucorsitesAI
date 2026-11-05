import { apiFetch, ApiError } from "./client";

export type StaffPermission = "orders" | "leads";
export type StaffStatus = "new" | "in_progress" | "ready" | "completed" | "cancelled";
export type StaffAction = "claim" | "ready" | "hand_over" | "close" | "reassign";

export type StaffProject = {
  project_id: string;
  project_name: string;
  permissions: StaffPermission[];
};
export type StaffAccess = StaffProject & { actor_id: string; is_owner: boolean };

type StaffRecordBase = {
  id: string;
  created_at: string;
  revision: string;
  status: StaffStatus;
  comment: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  outcome: string | null;
};
export type StaffOrder = StaffRecordBase & {
  resource: "orders";
  items: { title: string; quantity: number; option: string | null }[];
  scheduled_for: string | null;
  prepare_at: string | null;
  payment: "pickup" | "paid" | "test";
};
export type StaffLead = StaffRecordBase & {
  resource: "leads";
  customer_name: string;
  contact_phone: string | null;
  contact_email: string | null;
};
export type StaffRecord = StaffOrder | StaffLead;
export type StaffRecordsPage = { records: StaffRecord[]; next_cursor: string | null; server_time: string };
export type StaffRecordUpdate = {
  resource: StaffPermission;
  action: StaffAction;
  expected_revision: string;
  operation_key: string;
  outcome?: string;
  assignee_id?: string | null;
};
export type StaffHistory = {
  actor_name: string;
  action: StaffAction;
  created_at: string;
  assignee_name: string | null;
  outcome: string | null;
};

type TeamIdentity = { email: string; name: string; permissions: StaffPermission[] };
export type TeamMember = TeamIdentity & { user_id: string };
export type TeamInvitation = TeamIdentity & { id: string; expires_at: string };
export type TeamSettings = { leads_enabled: boolean; orders_mode: "pickup" | "yookassa" | null; prep_minutes: number };
export type TeamView = { members: TeamMember[]; invitations: TeamInvitation[]; settings: TeamSettings };
type CreatedInvitation = { token: string; expires_at: string };

const projectPath = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/max`;
const staffPath = (projectId: string) => `${projectPath(projectId)}/staff`;
const teamPath = (projectId: string) => `${projectPath(projectId)}/team`;
const recordPath = (projectId: string, id: string) => `${staffPath(projectId)}/records/${encodeURIComponent(id)}`;
const readTimeout = 15_000;
const writeTimeout = 20_000;

export function getStaffProjects(signal?: AbortSignal): Promise<StaffProject[]> {
  return apiFetch("/api/max/staff/projects", { signal, timeoutMs: readTimeout });
}
export function getStaffAccess(projectId: string, signal?: AbortSignal): Promise<StaffAccess> {
  return apiFetch(staffPath(projectId), { signal, timeoutMs: readTimeout });
}
export function getStaffRecords(projectId: string, resource: StaffPermission, cursor?: string, signal?: AbortSignal): Promise<StaffRecordsPage> {
  const params = new URLSearchParams({ resource });
  if (cursor) params.set("cursor", cursor);
  return apiFetch(`${staffPath(projectId)}/records?${params}`, { signal, timeoutMs: readTimeout });
}
export function updateStaffRecord(projectId: string, id: string, update: StaffRecordUpdate): Promise<StaffRecord> {
  return apiFetch(recordPath(projectId, id), { method: "PATCH", json: update, timeoutMs: writeTimeout });
}
export function getStaffHistory(projectId: string, record: StaffRecord, signal?: AbortSignal): Promise<StaffHistory[]> {
  return apiFetch(`${recordPath(projectId, record.id)}/history?resource=${record.resource}`, { signal, timeoutMs: readTimeout });
}
export function getMaxTeam(projectId: string, signal?: AbortSignal): Promise<TeamView> {
  return apiFetch(teamPath(projectId), { signal, timeoutMs: readTimeout });
}
export function saveMaxTeamSettings(projectId: string, settings: TeamSettings): Promise<TeamView> {
  return apiFetch(`${teamPath(projectId)}/settings`, { method: "PUT", json: settings, timeoutMs: writeTimeout });
}
export function createMaxInvitation(projectId: string, invitation: TeamIdentity): Promise<CreatedInvitation> {
  return apiFetch(`${teamPath(projectId)}/invitations`, { method: "POST", json: invitation, timeoutMs: writeTimeout });
}
export function revokeMaxInvitation(projectId: string, id: string): Promise<void> {
  return apiFetch(`${teamPath(projectId)}/invitations/${encodeURIComponent(id)}`, { method: "DELETE", timeoutMs: writeTimeout });
}
export function updateMaxMember(projectId: string, userId: string, permissions: StaffPermission[]): Promise<TeamMember> {
  return apiFetch(`${teamPath(projectId)}/members/${encodeURIComponent(userId)}`, { method: "PATCH", json: { permissions }, timeoutMs: writeTimeout });
}
export function revokeMaxMember(projectId: string, userId: string): Promise<void> {
  return apiFetch(`${teamPath(projectId)}/members/${encodeURIComponent(userId)}`, { method: "DELETE", timeoutMs: writeTimeout });
}
export function acceptMaxInvitation(token: string): Promise<{ project_id: string }> {
  return apiFetch("/api/max/staff/invitations/accept", { method: "POST", json: { token }, timeoutMs: writeTimeout });
}

export function staffAccessDenied(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403 || error.status === 404);
}

export function staffRequestError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (String(error.code)) {
      case "staff_verification_required": return "Подтвердите email в аккаунте Yleum.";
      case "staff_queue_already_enabled": return "Очередь уже запущена. Можно изменить время приготовления.";
      case "staff_member_exists": return "У этого аккаунта уже есть доступ к проекту.";
      case "staff_team_limit": return "Достигнут лимит сотрудников и приглашений.";
      case "staff_assignee_unavailable": return "У сотрудника больше нет доступа к этой очереди.";
      case "staff_order_scheduled": return "Время приготовления ещё не наступило.";
      case "unsupported": return "Для рабочей очереди нужна совместимая опубликованная версия приложения.";
      case "unavailable": return "Опубликованное приложение пока недоступно. Повторите загрузку.";
    }
  }
  if (staffAccessDenied(error)) return "Доступ к рабочему кабинету закрыт.";
  if (error instanceof ApiError) {
    if (error.status === 0) return "Нет соединения или ответ не получен. Проверьте интернет.";
    if (error.status === 409) return "Данные изменились. Откройте актуальную запись.";
  }
  return "Не удалось выполнить запрос. Повторите попытку.";
}
