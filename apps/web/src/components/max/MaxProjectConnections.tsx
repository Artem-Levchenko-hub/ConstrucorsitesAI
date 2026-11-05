"use client";

import { Bot, Plug } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { FigmaIntegrationHub } from "./FigmaIntegrationHub";
import { MaxConnectionWizard } from "./MaxConnectionWizard";
import { useMaxWorkspaceDraftGuard } from "./use-max-workspace-draft-guard";
import "./max-connection-wizard.css";

export function MaxProjectConnections({ projectId, projectName, tab }: {
  projectId: string;
  projectName: string;
  tab: "bot" | "services";
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const projectHref = `/max/${projectId}`;
  const connectionsHref = `${projectHref}/integrations`;
  useMaxWorkspaceDraftGuard(false, busy);

  return (
    <div className="max-project-connections">
      <nav className="max-connections-nav" aria-label="Тип подключения">
        <Link href={connectionsHref} aria-current={tab === "bot" ? "page" : undefined}><Bot aria-hidden="true" />Бот MAX</Link>
        <Link href={`${connectionsHref}?tab=services`} aria-current={tab === "services" ? "page" : undefined}><Plug aria-hidden="true" />Сервисы</Link>
      </nav>
      {tab === "bot"
        ? <MaxConnectionWizard key={projectId} projectId={projectId} onBusyChange={setBusy}
            onNavigate={() => router.push(`${projectHref}/publish`)} />
        : <FigmaIntegrationHub key={projectId} projectId={projectId} projectName={projectName}
            embedded onBusyChange={setBusy} onExit={() => router.push(projectHref)} />}
    </div>
  );
}
