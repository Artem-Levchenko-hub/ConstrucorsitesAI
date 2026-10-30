import { apiFetch } from "./client";

export type SupportStatus = "new" | "in_progress" | "done";
export type SupportContext = { user_id: string | null; email: string | null; is_admin: boolean };
export type SupportMessage = { id: string; author: "customer" | "support"; body: string; created_at: string };
export type SupportTicket = {
  id: string; reference: string; subject: string; status: SupportStatus; version: number;
  requester_email: string; created_at: string; updated_at: string; resolved_at: string | null;
  archived: boolean; source_path: string | null; assigned_admin_email: string | null;
  message_count: number; last_message_preview: string;
  first_response_due_at?: string | null; first_responded_at?: string | null;
  first_response_overdue?: boolean; support_hours?: string;
};
export type SupportDetail = SupportTicket & { messages: SupportMessage[] };
export type SupportList = { items: SupportTicket[]; total: number; new_count?: number };
export type SupportCreate = { request_id: string; subject: string; message: string; email?: string; privacy_accepted?: boolean; guest_token?: string; source_path: string; website: string };
export type GuestTicket = { id: string; reference: string; token: string };
export const supportHours = "Пн–пт, 10:00–19:00 МСК";
export const supportPromise = "Первый ответ оператора — в течение одного рабочего дня";
export function supportTiming(ticket: SupportTicket, now: number): string {
  if (ticket.first_responded_at) return `Первый ответ оператора: ${new Date(ticket.first_responded_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК`;
  if (!ticket.first_response_due_at) return "Срок первого ответа для этого обращения не установлен";
  const deadline = new Date(ticket.first_response_due_at);
  const minutes = Math.max(0, Math.ceil(Math.abs(deadline.getTime() - now) / 60_000));
  const duration = `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
  return `Первый ответ оператора до ${deadline.toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК · ${deadline.getTime() < now ? "Просрочено на" : "Осталось"} ${duration}`;
}
/** A fragment never reaches HTTP logs/referrers; remove it before any API request. */
export function takeSupportRecovery(): GuestTicket | null {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const id = fragment.get("support"), token = fragment.get("token");
  if (!id || !token) return null;
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id) || !/^[0-9a-f]{64}$/.test(token)) return null;
  return { id, reference: "Обращение", token };
}
export const supportStatus: Record<SupportStatus, string> = { new: "Новые", in_progress: "В работе", done: "Готово" };
export const supportContextOptions = { queryKey: ["support-context"], queryFn: ({ signal }: { signal: AbortSignal }) => apiFetch<SupportContext>("/api/support/context", { signal, cache: "no-store", timeoutMs: 10_000 }), staleTime: 0, refetchInterval: 15_000, retry: false };
const path = (admin: boolean) => admin ? "/api/admin/support/tickets" : "/api/support/tickets";
const tokenHeaders = (token?: string) => token ? { "X-Support-Token": token } : undefined;
export const listSupport = (query = "", admin = false, signal?: AbortSignal) => apiFetch<SupportList>(`${path(admin)}${query ? `?${query}` : ""}`, { signal, cache: "no-store" });
export const getSupport = (id: string, admin = false, token?: string, signal?: AbortSignal) => apiFetch<SupportDetail>(`${path(admin)}/${encodeURIComponent(id)}`, { headers: tokenHeaders(token), signal, cache: "no-store" });
export const createSupport = (json: SupportCreate) => apiFetch<SupportDetail>(path(false), { method: "POST", json, timeoutMs: 20_000 });
export const replySupport = (id: string, json: { request_id: string; message: string }, admin = false, token?: string) => apiFetch<SupportDetail>(`${path(admin)}/${encodeURIComponent(id)}/messages`, { method: "POST", json, headers: tokenHeaders(token), timeoutMs: 20_000 });
export const updateSupport = (id: string, status: SupportStatus, expected_version: number) => apiFetch<SupportDetail>(`${path(true)}/${encodeURIComponent(id)}`, { method: "PATCH", json: { status, expected_version }, timeoutMs: 20_000 });
export function guestCapability(): string { return Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, "0")).join(""); }
const guestKey = "yleum-support-guests";
export function readGuestTickets(): GuestTicket[] {
  try { const value: unknown = JSON.parse(localStorage.getItem(guestKey) || "[]"); return Array.isArray(value) ? value.filter((item): item is GuestTicket => typeof item?.id === "string" && typeof item?.reference === "string" && /^[a-f0-9]{64}$/.test(item?.token)).slice(-50) : []; } catch { return []; }
}
export function saveGuestTickets(items: GuestTicket[]) { try { localStorage.setItem(guestKey, JSON.stringify(items.slice(-50))); } catch { /* Still available in this tab when storage is disabled. */ } }
export function clearGuestTickets() { try { localStorage.removeItem(guestKey); } catch { /* Storage may be disabled. */ } }

const pendingKey = "yleum-support-pending";
export function readSupportDraft(scope: string): SupportCreate | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
    const draft = value?.draft;
    if (value?.scope !== scope || !draft || typeof draft.request_id !== "string" || typeof draft.subject !== "string" || typeof draft.message !== "string" || (scope === "guest" && !/^[a-f0-9]{64}$/.test(draft.guest_token))) return null;
    return draft as SupportCreate;
  } catch { return null; }
}
export function saveSupportDraft(scope: string, draft: SupportCreate) { try { sessionStorage.setItem(pendingKey, JSON.stringify({ scope, draft })); } catch { /* In-memory retry still works. */ } }
export function clearSupportDraft() { try { sessionStorage.removeItem(pendingKey); } catch { /* Storage may be disabled. */ } }

const replyKey = "yleum-support-reply";
export type SupportReply = { request_id: string; message: string };
export function readSupportReply(scope: string, ticketId: string): SupportReply | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(replyKey) || "null");
    return value?.scope === scope && value?.ticketId === ticketId && typeof value?.draft?.request_id === "string" && typeof value?.draft?.message === "string" ? value.draft as SupportReply : null;
  } catch { return null; }
}
export function saveSupportReply(scope: string, ticketId: string, draft: SupportReply) { try { sessionStorage.setItem(replyKey, JSON.stringify({ scope, ticketId, draft })); } catch { /* In-memory retry still works. */ } }
export function clearSupportReply() { try { sessionStorage.removeItem(replyKey); } catch { /* Storage may be disabled. */ } }

/** Full-page OAuth navigation loses the previous React identity, but not storage. */
export function clearGuestSupportDrafts() {
  for (const key of [pendingKey, replyKey]) {
    try {
      const stored = sessionStorage.getItem(key);
      if (stored && JSON.parse(stored)?.scope === "guest") sessionStorage.removeItem(key);
    } catch { /* Storage may be disabled; do not discard a valid signed-in draft. */ }
  }
}
