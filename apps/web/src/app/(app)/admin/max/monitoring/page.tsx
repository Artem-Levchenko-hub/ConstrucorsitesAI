import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { MonitoringDashboard } from "@/components/account/MonitoringDashboard";
import { getMaxAdminAccessServer, getSession } from "@/lib/auth-mock";

export const metadata: Metadata = {
  title: "Мониторинг",
  robots: { index: false, follow: false },
};

export default async function MonitoringPage() {
  const session = await getSession();
  if (!session) redirect("/login?next=/admin/max/monitoring");
  if (!(await getMaxAdminAccessServer())) redirect("/account");

  return <MonitoringDashboard />;
}
