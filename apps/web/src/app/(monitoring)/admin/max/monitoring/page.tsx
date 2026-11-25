import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { MonitoringDashboard } from "@/components/account/MonitoringDashboard";
import { serverApiFetchResult } from "@/lib/api/server";
import {
  MONITORING_COOKIE, MONITORING_LOGIN_PATH, MONITORING_SESSION_API, type MonitoringRole,
} from "@/lib/monitoring-access";

export const metadata: Metadata = {
  title: "Мониторинг",
  robots: { index: false, follow: false },
};

export default async function MonitoringPage() {
  const access = process.env.NEXT_PUBLIC_USE_MOCKS !== "false"
    ? { ok: true as const, data: { role: "admin" as const } }
    : await serverApiFetchResult<{ role: MonitoringRole }>(
      MONITORING_SESSION_API, [MONITORING_COOKIE],
    );
  if (!access.ok) {
    if (access.status === 401 || access.status === 403) redirect(MONITORING_LOGIN_PATH);
    throw new Error("Monitoring access check failed");
  }

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden">
      <MonitoringDashboard role={access.data.role} />
    </div>
  );
}
