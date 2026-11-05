import { CircleHelp, MessageCircle, UserRound } from "lucide-react";
import Link from "next/link";
import { YleumMark } from "@/components/brand/YleumMark";
import { MaxProjectNav, type MaxProjectNavKey } from "@/components/max/MaxProjectNav";
import { LearningButton } from "@/components/learning/ProductLearning";
import "@/components/max/max-studio.css";
import "@/components/max/max-project-workspace.css";

const helpHref: Record<MaxProjectNavKey, string> = {
  manage: "/max/guide#operations",
  editor: "/max/guide#builder", app: "/max/guide#settings", catalog: "/max/guide#settings",
  integrations: "/max/guide#integrations", bot: "/max/guide#max-bot",
  publish: "/max/guide#publish", dashboard: "/max/guide#operations",
};

export function MaxSectionShell({ projectId, projectName, active, eyebrow, title, lead, children, className = "" }: {
  projectId: string; projectName: string; active: MaxProjectNavKey;
  eyebrow: string; title: string; lead: string; children: React.ReactNode;
  className?: string;
}) {
  const projectHref = `/max/${projectId}`;
  return (
    <div data-product-shell data-max-studio className={`max-project-workspace ${className}`}>
      <header className="max-project-header">
        <div className="max-project-header-identity">
          <Link href="/max" aria-label="Yleum — все проекты" className="max-project-brand"><YleumMark className="max-project-brand-mark" />Yleum</Link>
          <span className="max-project-header-name" title={projectName}>{projectName}</span>
        </div>
        <div className="max-project-header-actions">
          {active !== "editor" && <Link href={projectHref} className="max-project-chat"><MessageCircle aria-hidden="true" className="size-4" />К чату</Link>}
          <LearningButton />
          <Link href="/account" aria-label="Аккаунт" className="max-project-back"><UserRound aria-hidden="true" className="size-4" /></Link>
        </div>
      </header>
      <div data-learning="project-navigation" className="max-project-navigation"><MaxProjectNav projectId={projectId} active={active} showProgress={false} variant="mobile" /></div>
      <main data-learning="project-section" className="max-project-main">
        <div className="max-project-content">
          <header className="max-project-page-heading">
            <div><p className="max-project-eyebrow">{eyebrow}</p><h1>{title}</h1><p className="max-project-lead">{lead}</p></div>
            <Link data-learning="project-help" href={helpHref[active]} aria-label="Помощь" className="max-project-help"><CircleHelp aria-hidden="true" className="size-4" /><span>Помощь</span></Link>
          </header>
          {children}
        </div>
      </main>
    </div>
  );
}
