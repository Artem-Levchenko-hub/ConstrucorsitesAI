import { apiFetch } from "./client";
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

export async function deleteProject(id: string): Promise<void> {
  if (USE_MOCKS) return mockApi.deleteProject(id);
  return apiFetch<void>(`/api/projects/${id}`, { method: "DELETE" });
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
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const res = await fetch(`${base}/api/projects/${projectId}/download`, {
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

/**
 * Fork ("ремикс") a project into a new editable copy owned by the caller — the
 * transitive remix chain (V4 #3). Reuses the same POST /fork seam the public
 * zero-signup CTA uses; for an authed owner the fork binds to them. Returns the
 * new fork (its `id` is where the workspace navigates next).
 */
export async function forkProject(id: string): Promise<Project> {
  return apiFetch<Project>(`/api/projects/${id}/fork`, { method: "POST" });
}

/**
 * Import an existing GitHub repository as an Omnia project. The backend clones
 * the repo, stores the files as a snapshot, and returns a Project with
 * `source:"imported"` and `template:"blank"|"code"`. No container is
 * provisioned — imported projects use the static /p/<slug> preview path
 * (blank template) or the «Код» tab (code template).
 */
export async function importProject(input: {
  repo_url: string;
  ref?: string;
  name?: string;
}): Promise<Project> {
  return apiFetch<Project>("/api/projects/import", { method: "POST", json: input });
}

export type ProjectUpdate = {
  image_gen_enabled?: boolean;
};

export async function updateProject(
  id: string,
  payload: ProjectUpdate,
): Promise<Project> {
  if (USE_MOCKS) {
    const current = await mockApi.getProject(id);
    return { ...current, ...payload };
  }
  return apiFetch<Project>(`/api/projects/${id}`, {
    method: "PATCH",
    json: payload,
  });
}
