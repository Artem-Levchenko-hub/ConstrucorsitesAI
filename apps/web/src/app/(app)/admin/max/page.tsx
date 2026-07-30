import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AccountShell } from "@/components/account/AccountShell";
import { AdminVerificationPanel } from "@/components/account/AdminVerificationPanel";
import { getSession } from "@/lib/auth-mock";

export const metadata: Metadata = {
  title: "Проверка организаций — Omnia",
  robots: { index: false, follow: false },
};

export default async function MaxAdminPage() {
  const session = await getSession();
  if (!session) redirect("/login?next=/admin/max");
  return (
    <AccountShell email={session.email} active="admin">
      <AdminVerificationPanel />
    </AccountShell>
  );
}
