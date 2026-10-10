import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { loadMaxProject } from "@/lib/max-project-server";

export const metadata: Metadata = {
  title: "Интеграции — Yleum",
  robots: { index: false, follow: false },
};

export default async function MaxIntegrationsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ oauth?: string | string[] }>;
}) {
  const { id } = await params;
  const project = await loadMaxProject(id, `/max/${id}/integrations`);
  const outcome = (await searchParams)?.oauth;
  const suffix = typeof outcome === "string" && ["connected", "cancelled", "error", "conflict"].includes(outcome)
    ? `&oauth=${outcome}` : "";
  redirect(`/max/${project.id}?panel=services${suffix}`);
}
