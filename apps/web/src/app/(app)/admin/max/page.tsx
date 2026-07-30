import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { AccountShell } from "@/components/account/AccountShell";
import { AdminControlCenter } from "@/components/account/AdminControlCenter";
import { getMaxAdminAccessServer, getSession } from "@/lib/auth-mock";

export const metadata: Metadata = {
  title: "Админ-центр — Omnia",
  robots: { index: false, follow: false },
};

export default async function MaxAdminPage() {
  const session = await getSession();
  if (!session) redirect("/login?next=/admin/max");
  if (!(await getMaxAdminAccessServer())) redirect("/account");
  return (
    <AccountShell email={session.email} active="admin">
      <AdminControlCenter currentEmail={session.email} />
    </AccountShell>
  );
}
