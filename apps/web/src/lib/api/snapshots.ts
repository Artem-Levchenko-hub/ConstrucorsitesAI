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

/**
 * Возвращает снапшот + dict путь→содержимое всех файлов в коммите.
 * Бэк читает из MinIO, см. apps/api/src/omnia_api/routers/snapshots.py:55.
 */
export async function getSnapshotWithFiles(
  projectId: string,
  snapshotId: string,
): Promise<SnapshotWithFiles> {
  if (USE_MOCKS) {
    const all = await mockApi.listSnapshots(projectId);
    const snap = all.find((s) => s.id === snapshotId) ?? all[0];
    return {
      ...snap,
      files: {
        "index.html":
          "<!doctype html><html><body><h1>Mock preview</h1><p>USE_MOCKS=true</p></body></html>",
      },
    };
  }
  return apiFetch<SnapshotWithFiles>(
    `/api/projects/${projectId}/snapshots/${snapshotId}`,
  );
}
