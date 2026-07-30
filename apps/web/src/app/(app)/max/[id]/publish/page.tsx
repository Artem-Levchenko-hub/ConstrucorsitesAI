import type { Metadata } from "next";

import { MaxPublishWorkspace } from "@/components/max/MaxPublishWorkspace";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Публикация — MAX Studio",
  robots: { index: false, follow: false },
};

export default async function MaxPublishPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/publish`);
  return <MaxPublishWorkspace projectId={project.id} projectName={project.name} />;
}
