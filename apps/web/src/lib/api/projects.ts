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
