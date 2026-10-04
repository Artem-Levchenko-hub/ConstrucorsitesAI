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
