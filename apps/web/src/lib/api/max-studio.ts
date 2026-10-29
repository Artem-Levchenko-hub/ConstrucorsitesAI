import { apiFetch, postBlob } from "./client";
import type {
  MaxProjectConfig,
  MaxProjectConfigPayload,
  MaxProjectContent,
  MaxContentItem,
  MaxPreviewSession,
  MaxReadiness,
  MaxUsage,
  PublicationMigration,
  Uuid,
} from "./types";

const path = (projectId: Uuid) => `/api/projects/${projectId}/max`;

export function getMaxProjectConfig(projectId: Uuid): Promise<MaxProjectConfig> {
  return apiFetch<MaxProjectConfig>(`${path(projectId)}/config`, { timeoutMs: 15_000 });
}

export function saveMaxProjectConfig(
  projectId: Uuid,
  config: MaxProjectConfigPayload,
  expectedConfigVersion?: number,
): Promise<MaxProjectConfig> {
  return apiFetch<MaxProjectConfig>(`${path(projectId)}/config`, {
    method: "PUT",
    json: config,
    headers: expectedConfigVersion === undefined ? undefined : { "X-Max-Config-Version": String(expectedConfigVersion) },
    timeoutMs: 180_000,
  });
}

export function getMaxProjectContent(projectId: Uuid): Promise<MaxProjectContent> {
  return apiFetch(`${path(projectId)}/content`, { timeoutMs: 15_000 });
}

export function saveMaxProjectContent(projectId: Uuid, expectedConfigVersion: number, content: MaxContentItem[]): Promise<MaxProjectContent> {
  return apiFetch(`${path(projectId)}/content`, {
    method: "PATCH", json: { expected_config_version: expectedConfigVersion, content }, timeoutMs: 180_000,
  });
}

/** Загрузить фото позиции каталога и получить его публичный адрес. */
export function uploadMaxContentImage(
  projectId: Uuid,
  file: File,
): Promise<{ url: string }> {
  return postBlob<{ url: string }>(`${path(projectId)}/content-image`, file, {
    timeoutMs: 60_000,
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

export function createMaxPreviewSession(
  projectId: Uuid,
): Promise<MaxPreviewSession> {
  return apiFetch<MaxPreviewSession>(`${path(projectId)}/preview-session`, {
    method: "POST",
  });
}

export function getMaxUsage(projectId: Uuid): Promise<MaxUsage> {
  return apiFetch<MaxUsage>(`${path(projectId)}/usage`);
}

export function getPublicationMigration(projectId: Uuid): Promise<PublicationMigration | null> {
  return apiFetch(`${path(projectId)}/publication-migrations`, { timeoutMs: 15_000 });
}

export function preparePublicationMigration(projectId: Uuid, idempotencyKey: string): Promise<PublicationMigration> {
  return apiFetch(`${path(projectId)}/publication-migrations`, {
    method: "POST", json: { idempotency_key: idempotencyKey }, timeoutMs: 30_000,
  });
}

export function confirmPublicationMigration(projectId: Uuid, operationId: string, plan: string): Promise<PublicationMigration> {
  return apiFetch(`${path(projectId)}/publication-migrations/${operationId}/confirm`, {
    method: "POST", json: { plan_sha256: plan, explicit_intent: true }, timeoutMs: 30_000,
  });
}
