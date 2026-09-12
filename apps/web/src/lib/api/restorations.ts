import { apiFetch } from "./client";
import type { Snapshot } from "./types";

export type RestoreState = "preparing" | "checking" | "ready" | "needs_changes"
  | "applying" | "completed" | "cancelled" | "failed" | "reconciling";
export interface RestoreReport {
  revision: number;
  mode: "exact" | "adapted";
  database_state?: "empty" | "present" | "unknown";
  changes: string[];
  retained_data: string[];
  unavailable_features: string[];
  warnings: string[];
  blockers: string[];
  next_actions: string[];
}
export interface RestoreOperation {
  id: string;
  project_id: string;
  target: "draft";
  source_version_id: string;
  source_snapshot_id: string;
  base_draft_snapshot_id: string | null;
  state: RestoreState;
  phase: string;
  updated_at: string;
  revision: number;
  candidate_id: string | null;
  report: RestoreReport | null;
  can_apply: boolean;
  can_cancel: boolean;
  applied_version: string | null;
  applied_snapshot: Snapshot | null;
  error: string | null;
}
export interface PrepareRestorationRequest {
  target_version_id: string;
  expected_draft_snapshot_id: string | null;
  idempotency_key: string;
}
export interface ApplyRestorationRequest {
  report_revision: number;
  expected_draft_snapshot_id: string | null;
  idempotency_key: string;
}
const path = (projectId: string) => `/api/projects/${projectId}/restorations`;
export function listRestorations(projectId: string, signal?: AbortSignal) {
  return apiFetch<{ items: RestoreOperation[]; enabled: boolean }>(path(projectId), { signal, timeoutMs: 15_000 });
}
export function getRestoration(projectId: string, operationId: string, signal?: AbortSignal) {
  return apiFetch<RestoreOperation>(`${path(projectId)}/${operationId}`, { signal, timeoutMs: 15_000 });
}
export function prepareRestoration(projectId: string, payload: PrepareRestorationRequest) {
  return apiFetch<RestoreOperation>(path(projectId), { method: "POST", json: payload, timeoutMs: 30_000 });
}
export function applyRestoration(projectId: string, operationId: string, payload: ApplyRestorationRequest) {
  return apiFetch<RestoreOperation>(`${path(projectId)}/${operationId}/apply`, {
    method: "POST", json: payload, timeoutMs: 30_000,
  });
}
export function cancelRestoration(projectId: string, operationId: string) {
  return apiFetch<RestoreOperation>(`${path(projectId)}/${operationId}/cancel`, {
    method: "POST", json: {}, timeoutMs: 30_000,
  });
}
