import type { Metadata } from "next";

import { MaxSettingsWorkspace } from "@/components/max/MaxSettingsWorkspace";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "MAX и приложение — MAX Studio",
  robots: { index: false, follow: false },
};

export default async function MaxSettingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const project = await loadMaxProject(id, `/max/${id}/settings`);
  return (
    <MaxSettingsWorkspace
      projectId={project.id}
      projectName={project.name}
      initialTab={query.tab === "vps" || query.tab === "app" ? query.tab : "bot"}
    />
  );
}
