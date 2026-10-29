import type { Metadata } from "next";
import { MaxOwnerCatalog } from "@/components/max/MaxOwnerCatalog";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = { title: "Товары и услуги — Yleum", robots: { index: false, follow: false } };

export default async function MaxCatalogPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/catalog`);
  return <MaxOwnerCatalog key={project.id} projectId={project.id} projectName={project.name} />;
}
