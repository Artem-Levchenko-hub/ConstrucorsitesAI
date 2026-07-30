import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { MaxWorkspaceShell } from "@/components/max/MaxWorkspaceShell";
import { getSession } from "@/lib/auth-mock";
import { mockApi, USE_MOCKS } from "@/lib/api/mocks";
import { serverApiFetchResult, type ServerFetchResult } from "@/lib/api/server";
import type { Project } from "@/lib/api/types";

export const metadata: Metadata = {
  title: "Редактор MAX Mini App",
  robots: { index: false, follow: false },
};

async function loadProject(id: string): Promise<ServerFetchResult<Project>> {
  if (USE_MOCKS) {
    try {
      return { ok: true, data: await mockApi.getProject(id) };
    } catch {
      return { ok: false, status: 404 };
    }
  }
  return serverApiFetchResult<Project>(`/api/projects/${id}`);
}

export default async function MaxWorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await getSession();
  if (!session) return null;

  const result = await loadProject(id);
  if (!result.ok) {
    if (result.status === 401) redirect(`/login?next=/max/${id}`);
    if (result.status === 403 || result.status === 404) redirect("/max");
    notFound();
  }
  const project = result.data;
  if (project.template !== "max_miniapp") {
    redirect(`/projects/${project.id}`);
  }

  return <MaxWorkspaceShell project={project} email={session.email} />;
}
