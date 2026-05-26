/**
 * V2 — runtime + deploy client.
 *
 * Wraps the four endpoints introduced by `apps/api/routers/runtime.py`. We
 * deliberately keep the surface minimal — no optimistic caching here, since
 * runtime mutations are user-initiated (button click) and the same WS feed
 * that delivers `runtime.started` / `deploy.progress` already invalidates
 * the relevant queries in `usePromptStream` (Phase A wiring).
 */

import { apiFetch } from "./client";
import type { DeployStatus, RuntimeStatus, Uuid } from "./types";

export type RuntimeLogs = {
  container_name: string | null;
  tail: number;
  logs: string;
};

export async function getRuntimeLogs(
  projectId: Uuid,
  tail: number = 200,
  kind: "dev" | "prod" = "dev",
): Promise<RuntimeLogs> {
  return apiFetch<RuntimeLogs>(
    `/api/projects/${projectId}/runtime/logs?tail=${tail}&kind=${kind}`,
  );
}

export async function getRuntime(projectId: Uuid): Promise<RuntimeStatus> {
  return apiFetch<RuntimeStatus>(`/api/projects/${projectId}/runtime`);
}

export async function startRuntime(projectId: Uuid): Promise<RuntimeStatus> {
  return apiFetch<RuntimeStatus>(`/api/projects/${projectId}/runtime/start`, {
    method: "POST",
  });
}

export async function stopRuntime(
  projectId: Uuid,
  pause = true,
): Promise<RuntimeStatus> {
  return apiFetch<RuntimeStatus>(`/api/projects/${projectId}/runtime/stop`, {
    method: "POST",
    json: { pause },
  });
}

export async function deployProject(
  projectId: Uuid,
  commitSha?: string,
): Promise<DeployStatus> {
  return apiFetch<DeployStatus>(`/api/projects/${projectId}/deploy`, {
    method: "POST",
    json: commitSha ? { commit_sha: commitSha } : {},
  });
}

export async function getLastDeploy(projectId: Uuid): Promise<DeployStatus> {
  return apiFetch<DeployStatus>(`/api/projects/${projectId}/deploy`);
}
