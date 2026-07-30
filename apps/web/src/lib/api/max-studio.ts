import { apiFetch } from "./client";
import type {
  MaxProjectConfig,
  MaxProjectConfigPayload,
  MaxReadiness,
  Uuid,
} from "./types";

const path = (projectId: Uuid) => `/api/projects/${projectId}/max`;

export function getMaxProjectConfig(projectId: Uuid): Promise<MaxProjectConfig> {
  return apiFetch<MaxProjectConfig>(`${path(projectId)}/config`);
}

export function saveMaxProjectConfig(
  projectId: Uuid,
  config: MaxProjectConfigPayload,
): Promise<MaxProjectConfig> {
  return apiFetch<MaxProjectConfig>(`${path(projectId)}/config`, {
    method: "PUT",
    json: config,
  });
}

export function syncMaxManagedKit(
  projectId: Uuid,
): Promise<MaxProjectConfig> {
  return apiFetch<MaxProjectConfig>(`${path(projectId)}/sync-kit`, {
    method: "POST",
  });
}

export function saveMaxUrlAttached(
  projectId: Uuid,
  attached: boolean,
): Promise<MaxProjectConfig> {
  return apiFetch<MaxProjectConfig>(`${path(projectId)}/config/url-attached`, {
    method: "PATCH",
    json: { attached },
  });
}

export function getMaxReadiness(projectId: Uuid): Promise<MaxReadiness> {
  return apiFetch<MaxReadiness>(`${path(projectId)}/readiness`);
}
