"use client";

import { MaxOwnerAnalytics } from "@/components/max/MaxOwnerAnalytics";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";

export function MaxPostLaunchDashboard({ projectId, projectName }: { projectId: string; projectName: string }) {
  return (
    <MaxSectionShell projectId={projectId} projectName={projectName} active="dashboard" eyebrow={projectName} title="Статистика" lead="Пользователи и действия в приложении">
      <MaxOwnerAnalytics projectId={projectId} />
    </MaxSectionShell>
  );
}
