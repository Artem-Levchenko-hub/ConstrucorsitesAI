import "server-only";

import { notFound, redirect } from "next/navigation";

import { mockApi, USE_MOCKS } from "@/lib/api/mocks";
import { serverApiFetchResult, type ServerFetchResult } from "@/lib/api/server";
import type { Project } from "@/lib/api/types";

async function fetchProject(id: string): Promise<ServerFetchResult<Project>> {
  if (USE_MOCKS) {
    try {
      return { ok: true, data: await mockApi.getProject(id) };
    } catch {
      return { ok: false, status: 404 };
    }
  }
  return serverApiFetchResult<Project>(`/api/projects/${id}`);
}

export async function loadMaxProject(id: string, nextPath: string): Promise<Project> {
  const result = await fetchProject(id);
  if (!result.ok) {
    if (result.status === 401) redirect(`/login?next=${encodeURIComponent(nextPath)}`);
    if (result.status === 403 || result.status === 404) redirect("/max");
    notFound();
  }
  if (result.data.template !== "max_miniapp") redirect(`/projects/${result.data.id}`);
  return result.data;
}
