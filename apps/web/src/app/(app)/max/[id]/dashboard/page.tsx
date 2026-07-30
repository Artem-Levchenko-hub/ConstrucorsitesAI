import type { Metadata } from "next";

import { MaxPostLaunchDashboard } from "@/components/max/MaxPostLaunchDashboard";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Управление приложением — MAX Studio",
  robots: { index: false, follow: false },
};

export default async function MaxDashboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/dashboard`);
  return <MaxPostLaunchDashboard projectId={project.id} projectName={project.name} />;
}
