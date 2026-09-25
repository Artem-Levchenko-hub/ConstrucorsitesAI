import { apiFetch } from "./client";
import type { Snapshot } from "./types";

export type RestoreState = "preparing" | "checking" | "ready" | "needs_changes"
  | "adapting" | "applying" | "completed" | "cancelled" | "failed" | "reconciling";
export interface RestoreInventoryObject {
  object: string;
  kind: string;
  classification: "business" | "technical" | "derived" | "unknown";
  presence: "empty" | "present" | "unknown";
  row_count?: number | null;
  count_kind: "exact" | "estimate" | "not_measured";
}
export interface RestoreCheck {
  code: string;
  status: "compatible" | "incompatible" | "unknown" | "not_applicable";
  severity: "blocking" | "warning" | "info";
  operation: string;
  object: string;
  evidence?: "structural_rule" | "observed_catalog" | "source_scan";
  explanation: string;
  resolution?: string | null;
}
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
  // Format 2 only; absent on older reports (= not measured, never "no data").
  format?: 1 | 2;
  inventory?: {
    presence: "empty" | "present" | "unknown";
    coverage: "complete" | "partial" | "unavailable";
    schema_analysis: "complete" | "partial" | "unavailable";
    objects: RestoreInventoryObject[];
  } | null;
  checks?: RestoreCheck[];
  capabilities?: { lost: { method: string; path: string }[]; restored: { method: string; path: string }[] } | null;
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
  execution_policy: "manual" | "automatic_when_safe";
  selected_branch: "exact" | "adaptive" | null;
  adaptation_run_id: string | null;
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
  execution_policy: "manual" | "automatic_when_safe";
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

// Owner actions on unsaved draft edits. A rollback refuses while the draft's files
// differ from the saved version; these are the two deliberate ways to resolve that.
export interface DraftSaveResponse {
  version_id: string;
  number: number;
  snapshot_id: string;
}
export interface DraftDiscardResponse {
  written: number;
  deleted: number;
  workspace_revision: string;
}
export function saveDraftVersion(projectId: string) {
  return apiFetch<DraftSaveResponse>(`/api/projects/${projectId}/draft/save-version`, {
    method: "POST", json: {}, timeoutMs: 60_000,
  });
}
export function discardDraftChanges(projectId: string) {
  return apiFetch<DraftDiscardResponse>(`/api/projects/${projectId}/draft/discard`, {
    method: "POST", json: {}, timeoutMs: 60_000,
  });
}
