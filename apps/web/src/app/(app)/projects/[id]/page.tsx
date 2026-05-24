import { notFound } from "next/navigation";
import { getSession } from "@/lib/auth-mock";
import { TopBar } from "@/components/workspace/TopBar";
import { Workspace } from "@/components/workspace/Workspace";
import { mockApi, USE_MOCKS } from "@/lib/api/mocks";
import { serverApiFetch } from "@/lib/api/server";
import type { Project } from "@/lib/api/types";

async function loadProject(id: string): Promise<Project | null> {
  if (USE_MOCKS) {
    try {
      return await mockApi.getProject(id);
    } catch {
      return null;
    }
  }
  // serverApiFetch attaches the omnia_session cookie from next/headers
  // (browser cookies don't reach server fetches automatically).
  return await serverApiFetch<Project>(`/api/projects/${id}`);
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
      <TopBar
        user={session}
        projectName={project.name}
        projectId={project.id}
        projectSlug={project.slug}
      />
      <Workspace project={project} />
    </>
  );
}
