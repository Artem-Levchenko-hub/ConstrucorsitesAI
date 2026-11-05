import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { MaxOwnerConsole } from "@/components/max/MaxOwnerConsole";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = { title: "Управление — Yleum", robots: { index: false, follow: false } };

export default async function MaxManagePage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ section?: string | string[] }>;
}) {
  const { id } = await params;
  const legacyCatalog = (await searchParams)?.section === "catalog";
  const nextPath = `/max/${id}/manage${legacyCatalog ? "?section=catalog" : ""}`;
  const project = await loadMaxProject(id, nextPath);
  if (legacyCatalog) redirect(`/max/${encodeURIComponent(project.id)}/catalog`);
  return <MaxOwnerConsole key={project.id} projectId={project.id} projectName={project.name} />;
}
