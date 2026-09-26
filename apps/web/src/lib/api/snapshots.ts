import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Snapshot, SnapshotWithFiles } from "./types";

export async function listSnapshots(projectId: string): Promise<Snapshot[]> {
  if (USE_MOCKS) return mockApi.listSnapshots(projectId);
  return apiFetch<Snapshot[]>(`/api/projects/${projectId}/snapshots`);
}

export async function rollback(
  projectId: string,
  snapshotId: string,
): Promise<Snapshot> {
  if (USE_MOCKS) return mockApi.rollback(projectId, snapshotId);
  return apiFetch<Snapshot>(`/api/projects/${projectId}/rollback`, {
    method: "POST",
    json: { snapshot_id: snapshotId },
  });
}


export async function listProjectVersions(
  projectId: string,
  before?: number,
  signal?: AbortSignal,
): Promise<import("./types").ProjectVersionPage> {
  const params = new URLSearchParams({ limit: "30" });
  if (before !== undefined) params.set("before", String(before));
  return apiFetch(`/api/projects/${projectId}/versions?${params}`, { signal });
}
