/**
 * GitHub OAuth + Push клиент. Зеркалит apps/api/src/omnia_api/routers/github.py.
 * Контракт — single source of truth: docs/01-api-contract.md (раздел GitHub).
 */

import { apiFetch } from "./client";
import type {
  GithubConnectResponse,
  GithubPushRequest,
  GithubPushResponse,
  GithubStatus,
  Uuid,
} from "./types";

export async function getGithubStatus(): Promise<GithubStatus> {
  return apiFetch<GithubStatus>("/api/github/status");
}

/** Возвращает authorize_url — caller должен сделать window.location.assign. */
export async function getGithubConnectUrl(): Promise<GithubConnectResponse> {
  return apiFetch<GithubConnectResponse>("/api/github/connect");
}

export async function disconnectGithub(): Promise<void> {
  return apiFetch<void>("/api/github/disconnect", { method: "DELETE" });
}

export async function pushProjectToGithub(
  projectId: Uuid,
  body: GithubPushRequest,
): Promise<GithubPushResponse> {
  return apiFetch<GithubPushResponse>(
    `/api/github/projects/${projectId}/push`,
    { method: "POST", json: body },
  );
}
