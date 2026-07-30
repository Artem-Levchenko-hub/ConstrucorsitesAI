import { apiFetch } from "./client";
import type {
  AppIntegration,
  IntegrationCatalog,
  Uuid,
} from "./types";

const path = (projectId: Uuid) =>
  `/api/projects/${projectId}/app-integrations`;

export function getIntegrationCatalog(
  projectId: Uuid,
): Promise<IntegrationCatalog> {
  return apiFetch<IntegrationCatalog>(path(projectId));
}

export function connectAppIntegration(
  projectId: Uuid,
  provider: string,
  values: Record<string, string>,
): Promise<AppIntegration> {
  return apiFetch<AppIntegration>(`${path(projectId)}/${provider}`, {
    method: "PUT",
    json: { values },
    timeoutMs: 20_000,
  });
}

export function verifyAppIntegration(
  projectId: Uuid,
  provider: string,
): Promise<AppIntegration> {
  return apiFetch<AppIntegration>(
    `${path(projectId)}/${provider}/verify`,
    { method: "POST", timeoutMs: 20_000 },
  );
}

export function bindAppIntegration(
  projectId: Uuid,
  provider: string,
): Promise<AppIntegration> {
  return apiFetch<AppIntegration>(`${path(projectId)}/${provider}/bind`, {
    method: "POST",
  });
}

export function applyIntegrationPack(
  projectId: Uuid,
): Promise<{
  bound_provider_keys: string[];
  remaining_provider_keys: string[];
}> {
  return apiFetch(`${path(projectId)}/pack/apply`, { method: "POST" });
}

export function startIntegrationOAuth(
  projectId: Uuid,
  provider: string,
): Promise<{ authorization_url: string }> {
  return apiFetch(`${path(projectId)}/${provider}/oauth/start`, {
    method: "POST",
  });
}

export function disconnectAppIntegration(
  projectId: Uuid,
  provider: string,
): Promise<void> {
  return apiFetch<void>(`${path(projectId)}/${provider}`, {
    method: "DELETE",
  });
}
