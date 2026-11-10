import { apiFetch, apiUrl } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { TeamSettings } from "./max-staff";
import type { Project, ProjectTemplate, MaxProjectConfigPayload } from "./types";

export async function listProjects(): Promise<Project[]> {
  if (USE_MOCKS) return mockApi.listProjects();
  return apiFetch<Project[]>("/api/projects", { cache: "no-store" });
}

export async function getProject(id: string): Promise<Project> {
  if (USE_MOCKS) return mockApi.getProject(id);
  return apiFetch<Project>(`/api/projects/${id}`);
}

export async function createProject(input: {
  name: string;
  template: ProjectTemplate;
  max_config?: MaxProjectConfigPayload;
  staff_settings?: TeamSettings;
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

