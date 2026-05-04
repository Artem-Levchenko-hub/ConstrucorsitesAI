import { notFound } from "next/navigation";
import { getSession } from "@/lib/auth-mock";
import { TopBar } from "@/components/workspace/TopBar";
import { Workspace } from "@/components/workspace/Workspace";
import { mockApi, USE_MOCKS } from "@/lib/api/mocks";
import { apiFetch } from "@/lib/api/client";
import type { Project } from "@/lib/api/types";

async function loadProject(id: string): Promise<Project | null> {
  try {
    if (USE_MOCKS) return await mockApi.getProject(id);
    return await apiFetch<Project>(`/api/projects/${id}`);
  } catch {
    return null;
  }
}

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await getSession();
  if (!session) return null;

  const project = await loadProject(id);
  if (!project) notFound();

  return (
    <>
      <TopBar user={session} projectName={project.name} />
      <Workspace project={project} />
    </>
  );
}
