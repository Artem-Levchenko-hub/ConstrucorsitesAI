import type { Metadata } from "next";
import { MaxProjectConnections } from "@/components/max/MaxProjectConnections";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { maxConnectionSearch, type MaxProjectSearch } from "@/lib/max-editor-entry";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Подключения — Yleum",
  robots: { index: false, follow: false },
};

export default async function MaxIntegrationsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<MaxProjectSearch>;
}) {
  const { id } = await params;
  const search = maxConnectionSearch((await searchParams) ?? {});
  const project = await loadMaxProject(id, `/max/${id}/integrations${search.size ? `?${search}` : ""}`);
  const tab = search.size ? "services" : "bot";
  return (
    <MaxSectionShell projectId={project.id} projectName={project.name} active="integrations"
      eyebrow="Настройка проекта" title="Подключения"
      lead="Подключите бота, через которого открывается приложение в MAX, и сервисы, нужные вашему бизнесу.">
      <MaxProjectConnections projectId={project.id} projectName={project.name} tab={tab} />
    </MaxSectionShell>
  );
}
