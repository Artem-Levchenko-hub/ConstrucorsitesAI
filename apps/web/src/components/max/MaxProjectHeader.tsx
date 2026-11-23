"use client";

import { useId, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, LayoutGrid, Smartphone, X } from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";
import { LearningButton } from "@/components/learning/ProductLearning";
import { Button } from "@/components/ui/button";
import { listProjects } from "@/lib/api/projects";
import { getOwnerProfile } from "@/lib/api/owner-profile";
import { MaxInstallButton } from "./MaxInstallApp";
import { MaxAccountMenu } from "./MaxAccountMenu";
import { MaxProjectNav, type MaxProjectNavKey } from "./MaxProjectNav";
import "./max-project-header.css";

function ProjectNavigation({ projectId, active, email, onNavigate }: {
  projectId: string; active: MaxProjectNavKey; email?: string; onNavigate: () => void;
}) {
  // Each dialog opening owns its reads; shared in-flight queries may belong to an earlier session.
  const openScope = useId();
  const projects = useQuery({
    queryKey: ["max-project-navigation", openScope, "projects"], queryFn: listProjects, gcTime: 0,
  });
  const profile = useQuery({
    queryKey: ["max-project-navigation", openScope, "profile"], queryFn: getOwnerProfile,
    enabled: !email, retry: false, gcTime: 0,
  });
  const projectsConfirmed = projects.isSuccess && !projects.isFetching;
  const applications = projectsConfirmed
    ? projects.data.filter(project => project.template === "max_miniapp") : [];
  const confirmedEmail = email || (profile.isSuccess && !profile.isFetching
    ? profile.data?.email : undefined);

  return (
    <>
      <div className="max-project-menu-navigation">
        <Link href="/max" className="max-project-menu-link"><LayoutGrid aria-hidden="true" className="size-4" />Все проекты</Link>
        <h3>Ваши приложения</h3>
        {(projects.isPending || projects.isFetching) && <p role="status" className="max-project-menu-status">Загружаем проекты…</p>}
        {projects.isError && <div className="max-project-menu-status" role="status">
          <p>Не удалось загрузить список проектов.</p>
          <Button variant="outline" onClick={() => void projects.refetch()}>Повторить</Button>
        </div>}
        {projectsConfirmed && (applications.length ? (
          <nav className="max-project-menu-projects" aria-label="Ваши Mini Apps" data-testid="max-projects-scroll">
            {applications.map(project => <Link key={project.id} href={"/max/" + project.id} className="max-project-menu-link"
              aria-current={project.id === projectId ? "page" : undefined}>
              <Smartphone aria-hidden="true" className="size-4 shrink-0" /><span title={project.name}>{project.name}</span>
            </Link>)}
          </nav>
        ) : <p className="max-project-menu-status">Других приложений пока нет.</p>)}
        <h3>Текущий проект</h3>
        <MaxProjectNav projectId={projectId} active={active} showProgress={false} />
      </div>
      <div className="max-project-menu-account">
        <MaxInstallButton />
        <MaxAccountMenu email={confirmedEmail} onNavigate={onNavigate} />
      </div>
    </>
  );
}

/** The same identity and real navigation task on editor and project sections. */
export function MaxProjectHeader({ projectId, projectName, active, email, navigation }: {
  projectId: string; projectName: string; active: MaxProjectNavKey; email?: string; navigation?: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="max-shared-project-identity" data-testid="max-project-header-identity">
      <div className="max-shared-project-brand"><BrandMark href="/max" ariaLabel="Yleum — все проекты" /></div>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Trigger asChild>
          <button type="button" className="max-shared-project-trigger" data-learning="editor-projects"
            data-testid="max-navigation-open" aria-label="Проекты и аккаунт" title={projectName}>
            <span>
              <strong>{projectName}</strong>
              <small>Yleum</small>
              <small className="max-project-menu-caption" data-learning="project-navigation">Меню проекта</small>
            </span>
            <ChevronDown aria-hidden="true" className="size-4 shrink-0" />
          </button>
        </Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="max-project-menu-overlay" />
          <Dialog.Content data-product-shell data-max-studio data-max-editor data-learning-context="navigation"
            className="max-project-menu" data-testid="max-editor-modal" aria-describedby={undefined}>
            <div className="max-project-menu-heading">
              <div><p title={projectName}>{projectName}</p><Dialog.Title>Меню</Dialog.Title></div>
              <div className="flex shrink-0 items-center gap-2"><LearningButton /><Dialog.Close asChild>
                <Button variant="outline" size="icon" aria-label="Закрыть окно"><X aria-hidden="true" className="size-5" /></Button>
              </Dialog.Close></div>
            </div>
            <div className="max-project-menu-scroll" data-testid="max-navigation-scroll" data-learning="editor-panel"
              onClickCapture={event => {
                // Document draft guards run first; Next Link prevents default later for SPA navigation.
                if (!event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
                  && event.target instanceof Element && event.target.closest("a[href]")) setOpen(false);
              }}>
              {navigation ?? <ProjectNavigation projectId={projectId} active={active} email={email} onNavigate={() => setOpen(false)} />}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
