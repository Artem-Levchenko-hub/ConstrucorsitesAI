/**
 * GitHub "Export to GitHub" client.
 *
 * Mirrors apps/api routers `github` (OAuth connect/status/disconnect under
 * /api/integrations/github) and `github_export` (POST .../export/github).
 */

import { apiFetch } from "./client";
import type {
  GithubConnectResponse,
  GithubExportRequest,
  GithubExportResult,
  GithubStatus,
  Uuid,
} from "./types";

export async function getGithubStatus(): Promise<GithubStatus> {
  return apiFetch<GithubStatus>("/api/integrations/github/status");
}

/** Returns the GitHub authorize URL; the caller redirects the browser to it. */
export async function getGithubConnectUrl(): Promise<GithubConnectResponse> {
  return apiFetch<GithubConnectResponse>("/api/integrations/github/connect");
}

export async function disconnectGithub(): Promise<void> {
  return apiFetch<void>("/api/integrations/github", { method: "DELETE" });
}

export async function exportToGithub(
  projectId: Uuid,
  body: GithubExportRequest = {},
): Promise<GithubExportResult> {
  return apiFetch<GithubExportResult>(
    `/api/projects/${projectId}/export/github`,
    { method: "POST", json: body },
  );
}
