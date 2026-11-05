import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { MaxStaffQueue } from "@/components/max/MaxStaffQueue";
import { MaxStaffShell } from "@/components/max/MaxStaffShell";
import { getSession } from "@/lib/auth-mock";
import { maxStaffProjectPath, maxStaffReturnPath } from "@/lib/max-staff-path";

export const metadata: Metadata = { title: "Рабочий кабинет — Yleum", robots: { index: false, follow: false } };

export default async function MaxStaffProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const path = maxStaffProjectPath(id);
  if (!maxStaffReturnPath(path)) notFound();
  const session = await getSession();
  if (!session || session.isAnon) redirect(`/login?next=${encodeURIComponent(path)}`);
  return <MaxStaffShell project><MaxStaffQueue key={`${session.id}:${id}`} projectId={id} userId={session.id} /></MaxStaffShell>;
}
