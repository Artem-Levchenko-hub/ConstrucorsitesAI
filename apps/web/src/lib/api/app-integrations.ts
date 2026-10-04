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

export type MoyskladInstallInfo = {
  available: boolean;
  install_url: string | null;
};

export function getMoyskladInstall(projectId: Uuid): Promise<MoyskladInstallInfo> {
  return apiFetch(`${path(projectId)}/moysklad/install`);
}

export function startMoyskladInstall(
  projectId: Uuid,
): Promise<{ install_url: string }> {
  return apiFetch(`${path(projectId)}/moysklad/install/start`, {
    method: "POST",
  });
}

export function claimMoyskladIntegration(
  projectId: Uuid,
  code: string,
): Promise<{ status: "connected" | "vendor_sync_pending" }> {
  return apiFetch(`${path(projectId)}/moysklad/claim`, {
    method: "POST",
    json: { code },
    timeoutMs: 20_000,
  });
}

export type MoyskladOption = { id: string; name: string };

export function getMoyskladOptions(projectId: Uuid): Promise<{
  organizations: MoyskladOption[];
  stores: MoyskladOption[];
}> {
  return apiFetch(`${path(projectId)}/moysklad/options`);
}

export function saveMoyskladSettings(
  projectId: Uuid,
  organizationId: string,
  storeId: string,
): Promise<{ status: "saved" }> {
  return apiFetch(`${path(projectId)}/moysklad/settings`, {
    method: "PUT",
    json: { organization_id: organizationId, store_id: storeId },
  });
}

export type AmocrmStatusOption = { id: number; name: string };
export type AmocrmPipelineOption = {
  id: number;
  name: string;
  statuses: AmocrmStatusOption[];
};

export function getAmocrmOptions(projectId: Uuid): Promise<{
  pipelines: AmocrmPipelineOption[];
}> {
  return apiFetch(`${path(projectId)}/amocrm/options`);
}

export function saveAmocrmSettings(
  projectId: Uuid,
  pipelineId: number,
  statusId: number,
): Promise<{ status: "saved" }> {
  return apiFetch(`${path(projectId)}/amocrm/settings`, {
    method: "PUT",
    json: { pipeline_id: pipelineId, status_id: statusId },
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

export function setPlatformAiEnabled(
  projectId: Uuid,
  enabled: boolean,
): Promise<{ enabled: boolean }> {
  return apiFetch(`/api/projects/${projectId}/platform-ai`, {
    method: "PUT",
    json: { enabled },
  });
}
