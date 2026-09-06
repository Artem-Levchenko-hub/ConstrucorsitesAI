import { ArrowLeft, CircleHelp, Rocket, UserRound } from "lucide-react";
import Link from "next/link";
import { MaxProjectNav, type MaxProjectNavKey } from "@/components/max/MaxProjectNav";
import "@/components/max/max-studio.css";
import "@/components/max/max-project-workspace.css";

const helpHref: Record<MaxProjectNavKey, string> = {
  editor: "/max/guide#builder", app: "/max/guide#settings",
  integrations: "/max/guide#integrations", bot: "/max/guide#max-bot",
  publish: "/max/guide#publish", dashboard: "/max/guide#operations",
};

export function MaxSectionShell({ projectId, projectName, active, eyebrow, title, lead, children }: {
  projectId: string; projectName: string; active: MaxProjectNavKey;
  eyebrow: string; title: string; lead: string; children: React.ReactNode;
}) {
  return (
    <div data-product-shell data-max-studio className="max-project-workspace">
      <header className="max-project-header">
        <div className="max-project-header-identity">
          <Link href="/max" aria-label="Все проекты" className="max-project-back"><ArrowLeft className="size-4" /></Link>
          <Link href="/max" className="max-project-brand">MAX <span>Studio</span></Link>
          <span className="max-project-header-name" title={projectName}>{projectName}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {active !== "publish" && <Link href={`/max/${projectId}/publish`} className="max-project-launch"><Rocket className="size-4" />Запуск</Link>}
          <Link href="/account" aria-label="Аккаунт" className="max-project-back"><UserRound className="size-4" /></Link>
        </div>
      </header>
      <div className="max-project-navigation"><MaxProjectNav projectId={projectId} active={active} showProgress={false} variant="mobile" /></div>
      <main className="max-project-main">
        <div className="max-project-content">
          <header className="max-project-page-heading">
            <div><p className="max-project-eyebrow">{eyebrow}</p><h1>{title}</h1><p className="max-project-lead">{lead}</p></div>
            <Link href={helpHref[active]} aria-label="Помощь" className="max-project-help"><CircleHelp className="size-4" /><span>Помощь</span></Link>
          </header>
          {children}
        </div>
      </main>
    </div>
  );
}
