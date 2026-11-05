import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { MaxProjectSetupDialog } from "@/components/max/MaxProjectSetupDialog";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Данные приложения — Yleum",
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
  const tab = query.tab === "app" || query.tab === "vps" ? query.tab : "bot";
  const project = await loadMaxProject(id, `/max/${id}/settings?tab=${tab}`);
  if (tab !== "app") redirect(`/max/${project.id}?panel=${tab === "vps" ? "publish" : "max"}`);
  return (
    <MaxSectionShell projectId={project.id} projectName={project.name} active="app"
      eyebrow="Настройка проекта" title="Данные приложения"
      lead="Обновляйте описание, контент и документы перед публикацией приложения в MAX.">
      <MaxProjectSetupDialog key={project.id} projectId={project.id} display="page" />
    </MaxSectionShell>
  );
}
