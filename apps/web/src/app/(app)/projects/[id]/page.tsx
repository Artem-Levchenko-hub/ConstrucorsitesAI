import { notFound, redirect } from "next/navigation";
import { getSession } from "@/lib/auth-mock";
import { TopBar } from "@/components/workspace/TopBar";
import { Workspace } from "@/components/workspace/Workspace";
import { mockApi, USE_MOCKS } from "@/lib/api/mocks";
import { serverApiFetchResult, type ServerFetchResult } from "@/lib/api/server";
import { remixSource } from "@/lib/project-lineage";
import type { Project } from "@/lib/api/types";

async function loadProject(
  id: string,
): Promise<ServerFetchResult<Project>> {
  if (USE_MOCKS) {
    try {
      return { ok: true, data: await mockApi.getProject(id) };
    } catch {
      return { ok: false, status: 404 };
    }
  }
  // serverApiFetchResult attaches the omnia_session cookie from next/headers
  // (browser cookies don't reach server fetches automatically) and reports the
  // status so we can tell "not yours/gone" apart from a transient api error.
  return await serverApiFetchResult<Project>(`/api/projects/${id}`);
}

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await getSession();
  if (!session) return null;

  const result = await loadProject(id);
  if (!result.ok) {
    // The api scopes `/api/projects/{id}` to the owner, so 403/404 both mean
    // "this signed-in user can't open this project" — typically a fresh signup
    // whose post-auth `next` pointed at someone else's project, or a stale/
    // shared workspace link. Stranding them on a bare 404 reads as "сайт
    // сломан / регистрация не прошла", so send them to their own projects list
    // instead. A 401 means the session went bad mid-request → back to /login.
    // Only a genuinely unexpected failure (5xx / network, status 0) keeps the
    // 404 degraded path.
    if (result.status === 401) redirect("/login");
    if (result.status === 403 || result.status === 404) redirect("/projects");
    notFound();
  }
  const project = result.data;

  return (
    <>
      <TopBar
        user={session}
        projectName={project.name}
        projectId={project.id}
        projectSlug={project.slug}
        imageGenEnabled={project.image_gen_enabled ?? true}
        remixSource={remixSource(project)}
      />
      <Workspace project={project} />
    </>
  );
}
