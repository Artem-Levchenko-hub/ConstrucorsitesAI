import { CircleHelp, MessageCircle, UserRound } from "lucide-react";
import Link from "next/link";
import { MaxProjectHeader } from "./MaxProjectHeader";
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

export function MaxSectionShell({ projectId, projectName, active, title, lead, children, className = "", learningContext }: {
  projectId: string; projectName: string; active: MaxProjectNavKey;
  eyebrow: string; title: string; lead: string; children: React.ReactNode;
  className?: string; learningContext?: string;
}) {
  const projectHref = `/max/${projectId}`;
  return (
    <div data-product-shell data-max-studio className={`max-project-workspace ${className}`}>
      <header className="max-project-header max-shared-project-header">
        <MaxProjectHeader projectId={projectId} projectName={projectName} active={active} />
        <div className="max-project-header-actions" data-learning-context={learningContext}>
          {active !== "editor" && <Link href={projectHref} className="max-project-chat"><MessageCircle aria-hidden="true" className="size-4" />Чат</Link>}
          <LearningButton />
          <Link href="/account" aria-label="Аккаунт" className="max-project-back"><UserRound aria-hidden="true" className="size-4" /></Link>
        </div>
      </header>
      <div data-learning="project-navigation" className="max-project-navigation"><MaxProjectNav projectId={projectId} active={active} showProgress={false} variant="mobile" /></div>
      <main data-learning="project-section" className="max-project-main">
        <div className="max-project-content">
          <header className="max-project-page-heading">
            <div><h1>{title}</h1><p className="max-project-lead">{lead}</p></div>
            <Link data-learning="project-help" href={helpHref[active]} aria-label="Помощь" className="max-project-help"><CircleHelp aria-hidden="true" className="size-4" /><span>Помощь</span></Link>
          </header>
          {children}
        </div>
      </main>
    </div>
  );
}
