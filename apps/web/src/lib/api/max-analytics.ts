import { apiFetch } from "./client";

export type MaxAnalyticsDay = {
  date: string; users: number; opens: number; actions: number; events: number;
};
export type MaxAnalytics = {
  days: number; from_date: string; to_date: string; timezone: "Europe/Moscow";
  users: number; opens: number; actions: number; events: number;
  measured_since: string | null; daily: MaxAnalyticsDay[];
};

export function getMaxAnalytics(projectId: string, days: number): Promise<MaxAnalytics> {
  return apiFetch<MaxAnalytics>(`/api/projects/${projectId}/max/analytics?days=${days}`, { cache: "no-store" });
}

export type MaxAnalyticsEventKind = "open" | "action" | "event";
export type MaxAnalyticsEventType = "app_open" | "screen_view" | "item_view" | "button_click" |
  "form_submit" | "cart_add" | "checkout_start" | "order_created" | "booking_created" | "lead_created";
export type MaxAnalyticsEvent = {
  id: string;
  kind: MaxAnalyticsEventKind;
  event_type: MaxAnalyticsEventType | null;
  occurred_at: string;
};
export type MaxAnalyticsEvents = {
  days: number; from_date: string; to_date: string; timezone: "Europe/Moscow";
  items: MaxAnalyticsEvent[]; next_cursor: string | null; observed_at: string;
};

export function getMaxAnalyticsEvents(projectId: string, options: {
  days: number; kind?: MaxAnalyticsEventKind; eventType?: MaxAnalyticsEventType;
  cursor?: string | null; signal?: AbortSignal;
}): Promise<MaxAnalyticsEvents> {
  const params = new URLSearchParams({ days: String(options.days), limit: "30" });
  if (options.kind) params.set("kind", options.kind);
  if (options.eventType) params.set("event_type", options.eventType);
  if (options.cursor) params.set("cursor", options.cursor);
  return apiFetch<MaxAnalyticsEvents>(`/api/projects/${projectId}/max/analytics/events?${params}`, {
    cache: "no-store", signal: options.signal,
  });
}
