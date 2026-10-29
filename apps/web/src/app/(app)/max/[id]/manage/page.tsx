import type { Metadata } from "next";
import { MaxOwnerConsole } from "@/components/max/MaxOwnerConsole";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = { title: "Управление — Yleum", robots: { index: false, follow: false } };

export default async function MaxManagePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/manage`);
  return <MaxOwnerConsole key={project.id} projectId={project.id} projectName={project.name} />;
}
