import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { MaxStaffProjects } from "@/components/max/MaxStaffProjects";
import { MaxStaffShell } from "@/components/max/MaxStaffShell";
import { getSession } from "@/lib/auth-mock";
import { MAX_STAFF_PATH } from "@/lib/max-staff-path";

export const metadata: Metadata = { title: "Рабочие приложения — Yleum", robots: { index: false, follow: false } };

export default async function MaxStaffPage() {
  const session = await getSession();
  if (!session || session.isAnon) redirect(`/login?next=${encodeURIComponent(MAX_STAFF_PATH)}`);
  return <MaxStaffShell><MaxStaffProjects userId={session.id} /></MaxStaffShell>;
}
