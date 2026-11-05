import type { Metadata } from "next";

import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Публикация — Yleum",
  robots: { index: false, follow: false },
};

export default async function MaxPublishPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/publish`);
  return (
    <MaxSectionShell projectId={project.id} projectName={project.name} active="publish"
      eyebrow="Выпуск приложения" title="Публикация"
      lead="Проверьте готовность, опубликуйте приложение и получите постоянный адрес для кнопки в MAX.">
      <MaxLaunchPanel project={project} standalone />
    </MaxSectionShell>
  );
}
