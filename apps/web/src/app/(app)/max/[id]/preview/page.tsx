import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { headers } from "next/headers";

import { MaxOwnerPreviewPage } from "@/components/max/MaxOwnerPreviewPage";
import { getSession } from "@/lib/auth-mock";
import { loadMaxProject } from "@/lib/max-project-server";
import { isMaxOwnerPreviewPath } from "@/lib/max-owner-preview-path";

export const metadata: Metadata = {
  title: "Предпросмотр приложения — Yleum",
  robots: { index: false, follow: false },
};

export default async function MaxPreviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const canonicalPath = `/max/${id}/preview`;
  const requestTarget = (await headers()).get("x-omnia-return-to");
  const nextPath = requestTarget && isMaxOwnerPreviewPath(requestTarget)
    && requestTarget.split("?", 1)[0] === canonicalPath ? requestTarget : canonicalPath;
  // Preserve admission if this page resolves before its outer app layout.
  if (!await getSession()) redirect(`/login?next=${encodeURIComponent(nextPath)}`);
  const project = await loadMaxProject(id, nextPath);
  return <MaxOwnerPreviewPage project={project} />;
}
