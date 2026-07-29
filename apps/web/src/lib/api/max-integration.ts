import { apiFetch } from "./client";
import type { MaxIntegration, Uuid } from "./types";

const path = (projectId: Uuid) =>
  `/api/projects/${projectId}/integrations/max`;

export function getMaxIntegration(projectId: Uuid): Promise<MaxIntegration> {
  return apiFetch<MaxIntegration>(path(projectId));
}

export function connectMaxIntegration(
  projectId: Uuid,
  token: string,
): Promise<MaxIntegration> {
  return apiFetch<MaxIntegration>(`${path(projectId)}/connect`, {
    method: "POST",
    json: { token },
  });
}

export function verifyMaxIntegration(projectId: Uuid): Promise<MaxIntegration> {
  return apiFetch<MaxIntegration>(`${path(projectId)}/verify`, {
    method: "POST",
  });
}

export function activateMaxIntegration(
  projectId: Uuid,
): Promise<MaxIntegration> {
  return apiFetch<MaxIntegration>(`${path(projectId)}/activate`, {
    method: "POST",
  });
}

export function disconnectMaxIntegration(projectId: Uuid): Promise<void> {
  return apiFetch<void>(path(projectId), { method: "DELETE" });
}
