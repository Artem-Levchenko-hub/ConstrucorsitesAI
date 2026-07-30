import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";
import { getSession } from "@/lib/auth-mock";
import { mockApi, USE_MOCKS } from "@/lib/api/mocks";
import { serverApiFetchResult, type ServerFetchResult } from "@/lib/api/server";
import type { Project } from "@/lib/api/types";

export const metadata: Metadata = {
  title: "Интеграции — MAX Studio",
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

export default async function MaxIntegrationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await getSession();
  if (!session) return null;

  const result = await loadProject(id);
  if (!result.ok) {
    if (result.status === 401) redirect(`/login?next=/max/${id}/integrations`);
    if (result.status === 403 || result.status === 404) redirect("/max");
    notFound();
  }
  if (result.data.template !== "max_miniapp") {
    redirect(`/projects/${result.data.id}`);
  }

  return (
    <FigmaIntegrationHub
      projectId={result.data.id}
      projectName={result.data.name}
    />
  );
}
