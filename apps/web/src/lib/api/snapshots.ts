import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Snapshot, SnapshotWithFiles } from "./types";

export async function listSnapshots(
  projectId: string,
  limit?: number,
): Promise<Snapshot[]> {
  if (USE_MOCKS) return mockApi.listSnapshots(projectId);
  const query = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  return apiFetch<Snapshot[]>(`/api/projects/${projectId}/snapshots${query}`);
}

export async function prepareSnapshotPreview(
  projectId: string,
  snapshotId: string,
): Promise<Snapshot> {
  if (USE_MOCKS) {
    const snapshots = await mockApi.listSnapshots(projectId);
    const snapshot = snapshots.find((item) => item.id === snapshotId);
    if (!snapshot) throw new Error("snapshot not found");
    return snapshot;
  }
  return apiFetch<Snapshot>(
    `/api/projects/${projectId}/snapshots/${snapshotId}/preview`,
    { method: "POST" },
  );
}

export type SnapshotSession = {
  project_id: string;
  snapshot_id: string;
  session_id: string;
  bootstrap_url: string;
  expires_at: string;
};

export async function startSnapshotSession(
  projectId: string,
  snapshotId: string,
): Promise<SnapshotSession> {
  if (USE_MOCKS) {
    return {
      project_id: projectId,
      snapshot_id: snapshotId,
      session_id: `mock-${snapshotId}`,
      bootstrap_url: `/p/mock-history-${snapshotId}`,
      expires_at: new Date(Date.now() + 120_000).toISOString(),
    };
  }
  return apiFetch<SnapshotSession>(
    `/api/projects/${projectId}/snapshots/${snapshotId}/session`,
    { method: "POST", timeoutMs: 450_000 },
  );
}

export async function stopSnapshotSession(
  projectId: string,
  snapshotId: string,
  sessionId: string,
): Promise<void> {
  if (USE_MOCKS) return;
  return apiFetch<void>(
    `/api/projects/${projectId}/snapshots/${snapshotId}/session?session_id=${encodeURIComponent(sessionId)}`,
    { method: "DELETE", keepalive: true },
  );
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
