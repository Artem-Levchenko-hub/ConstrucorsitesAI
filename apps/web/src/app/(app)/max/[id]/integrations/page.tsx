import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Интеграции — MAX Studio",
  robots: { index: false, follow: false },
};

export default async function MaxIntegrationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/integrations`);
  redirect(`/max/${project.id}?panel=services`);
}
