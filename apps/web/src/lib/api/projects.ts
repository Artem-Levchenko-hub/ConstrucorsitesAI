import { ApiError, apiFetch, apiUrl } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { Project, ProjectTemplate } from "./types";

export async function listProjects(): Promise<Project[]> {
  if (USE_MOCKS) return mockApi.listProjects();
  return apiFetch<Project[]>("/api/projects");
}

export async function getProject(id: string): Promise<Project> {
  if (USE_MOCKS) return mockApi.getProject(id);
  return apiFetch<Project>(`/api/projects/${id}`);
}

export async function createProject(input: {
  name: string;
  template: ProjectTemplate;
}): Promise<Project> {
  if (USE_MOCKS) return mockApi.createProject(input.name, input.template);
  return apiFetch<Project>("/api/projects", { method: "POST", json: input });
}

export type ProjectDeletionResult = { state: "deleted" | "deleting" };

export async function deleteProject(id: string): Promise<ProjectDeletionResult> {
  if (USE_MOCKS) {
    await mockApi.deleteProject(id);
    return { state: "deleted" };
  }
  const result = await apiFetch<{ state: "deleting"; project_id: string } | undefined>(
    `/api/projects/${id}`, { method: "DELETE", timeoutMs: 15_000 },
  );
  if (result === undefined) return { state: "deleted" };
  if (result.state !== "deleting" || result.project_id !== id) {
    throw new Error("Не удалось подтвердить запрос на удаление проекта.");
  }
  return { state: "deleting" };
}

/** The server owns deletion. Observation never sends another DELETE or claims
 * success from 202/200, a lost connection, or an expired browser deadline. */
export async function waitForProjectDeletion(id: string): Promise<ProjectDeletionResult> {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    try {
      await apiFetch<Project>(`/api/projects/${id}`, {
        cache: "no-store", timeoutMs: Math.min(10_000, deadline - Date.now()),
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return { state: "deleted" };
      // The durable intent remains active even if observation is unavailable.
      return { state: "deleting" };
    }
    const remaining = deadline - Date.now();
    if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, Math.min(2_000, remaining)));
  }
  return { state: "deleting" };
}

/**
 * Download ALL files of the project's current snapshot as a single .zip (owner
 * 2026-06-19 — one obvious button, zero thinking). Cookie-auth (credentials:
 * include) so it works cross-origin; we fetch the blob then trigger a client-side
 * download — a cross-origin `<a download>` href would be ignored by the browser.
 */
export async function downloadProjectFiles(
  projectId: string,
  slug?: string,
): Promise<void> {
  const res = await fetch(apiUrl(`/api/projects/${projectId}/download`), {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(
      res.status === 404
        ? "Пока нечего скачивать — сначала сгенерируйте проект."
        : `Не удалось скачать (${res.status})`,
    );
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug || "project"}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export type ProjectUpdate = {
  image_gen_enabled?: boolean;
};

