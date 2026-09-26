/**
 * Runtime state and publication of a MAX app.
 *
 * What is left after the site builder went: read the cell's runtime state,
 * start it, publish a version, cancel a running publication and read the
 * publication history. Pausing, keep-alive and container logs went with the
 * legacy dev containers — for a cell the server answered them with a refusal.
 */

import { apiFetch } from "./client";
import type { DeployStatus, RuntimeStatus, Uuid } from "./types";

type DeployRequestOptions = { signal?: AbortSignal; timeoutMs?: number };

export async function getRuntime(projectId: Uuid): Promise<RuntimeStatus> {
  return apiFetch<RuntimeStatus>(`/api/projects/${projectId}/runtime`);
}

export async function startRuntime(projectId: Uuid): Promise<RuntimeStatus> {
  return apiFetch<RuntimeStatus>(`/api/projects/${projectId}/runtime/start`, {
    method: "POST",
  });
}

export async function deployProject(
  projectId: Uuid,
  commitSha?: string,
  idempotencyKey: string = crypto.randomUUID(),
  options: DeployRequestOptions = {},
): Promise<DeployStatus> {
  return apiFetch<DeployStatus>(`/api/projects/${projectId}/deploy`, {
    method: "POST",
    json: { commit_sha: commitSha, idempotency_key: idempotencyKey },
    timeoutMs: 30_000,
    ...options,
  });
}

export async function getLastDeploy(projectId: Uuid, options: DeployRequestOptions = {}): Promise<DeployStatus> {
  return apiFetch<DeployStatus>(`/api/projects/${projectId}/deploy`, {
    timeoutMs: 30_000,
    ...options,
  });
}

export async function getDeployHistory(projectId: Uuid): Promise<DeployStatus[]> {
  return apiFetch<DeployStatus[]>(
    `/api/projects/${projectId}/deploy/history`,
  );
}
