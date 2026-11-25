import { apiFetch } from "./client";

export type MonitorPeriod = "24h" | "7d" | "30d";
export type MetricGroup = "infrastructure" | "activity" | "generation" | "ai" | "platform";

export type MetricDefinition = {
  id: number;
  group: MetricGroup;
  title: string;
  unit: string;
  formula: string;
  source: string | null;
  note: string | null;
};

export type MetricPoint = {
  series: string;
  at: string | null;
  value: number;
  p95: number | null;
  mean?: number | null;
  samples: number | null;
};

export type MetricResult = {
  state: "measured" | "no_data" | "stale" | "source_unavailable" | "not_applicable";
  measured_at: string;
  points: MetricPoint[];
  message: string | null;
};

export type MetricScope = {
  period: MonitorPeriod;
  client: string;
  project: string;
  vps: string;
};

export type MetricChoice = { id: string; label: string; client_id: string | null };

export type MonitorFilters = {
  clients: MetricChoice[];
  projects: MetricChoice[];
  servers: MetricChoice[];
};

const BASE = "/api/admin/monitoring";
const options = (signal?: AbortSignal) => ({ signal, timeoutMs: 12000, cache: "no-store" } as const);

export function getMetricCatalog(signal?: AbortSignal) {
  return apiFetch<MetricDefinition[]>(BASE + "/catalog", options(signal));
}

export function getMetricFilters(signal?: AbortSignal) {
  return apiFetch<MonitorFilters>(BASE + "/filters", options(signal));
}

export function getMetric(id: number, scope: MetricScope, signal?: AbortSignal) {
  const params = new URLSearchParams({ period: scope.period });
  for (const field of ["client", "project", "vps"] as const) {
    if (scope[field]) params.set(field, scope[field]);
  }
  return apiFetch<MetricResult>(BASE + "/metrics/" + id + "?" + params, options(signal));
}
