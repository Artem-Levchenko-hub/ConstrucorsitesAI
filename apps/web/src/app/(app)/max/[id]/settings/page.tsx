import type { Metadata } from "next";
import { redirect } from "next/navigation";

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
  const tab = query.tab === "app" || query.tab === "vps" ? query.tab : "bot";
  const project = await loadMaxProject(id, `/max/${id}/settings?tab=${tab}`);
  if (tab === "app") redirect(`/max/${project.id}?data=details`);
  redirect(`/max/${project.id}?panel=${tab === "vps" ? "hosting" : "max"}`);
}
