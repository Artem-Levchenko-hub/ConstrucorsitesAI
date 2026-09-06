"use client";

import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";

export function MaxPublishWorkspace({ projectId, projectName }: { projectId: string; projectName: string }) {
  return (
    <MaxSectionShell projectId={projectId} projectName={projectName} active="publish" eyebrow="Публикация приложения" title="Запуск в MAX" lead="Проверьте готовность и опубликуйте текущую версию по постоянному адресу.">
      <div className="max-publish-workspace"><MaxLaunchPanel project={{ id: projectId, name: projectName, template: "max_miniapp" }} standalone /></div>
    </MaxSectionShell>
  );
}
