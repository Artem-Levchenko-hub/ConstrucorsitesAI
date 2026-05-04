import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Snapshot } from "./types";

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
