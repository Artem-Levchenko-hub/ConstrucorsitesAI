"use client";

import { lazy, Suspense, useRef, useState, useSyncExternalStore, type MouseEvent, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, ArrowUpRight, ChevronDown, Ellipsis, PanelRightOpen, X } from "lucide-react";
import { MaxEditorDataDialog } from "./MaxEditorDataDialog";
import { useMaxEditorEntry } from "./useMaxEditorEntry";
import type { SetupSection } from "./MaxProjectSetupSections";
import { maxEditorLinkEntry, type MaxEditorEntry } from "@/lib/max-editor-entry";
import { BrandMark } from "@/components/marketing/BrandMark";
import { Button } from "@/components/ui/button";
import type { Project } from "@/lib/api/types";
import "./max-editor.css";
import "./max-editor-modals.css";

const MaxConnectionWizard = lazy(() => import("./MaxConnectionWizard").then(module => ({ default: module.MaxConnectionWizard })));
const FigmaIntegrationHub = lazy(() => import("./FigmaIntegrationHub").then(module => ({ default: module.FigmaIntegrationHub })));
const ExternalDeployWizard = lazy(() => import("@/components/workspace/ExternalDeployWizard").then(module => ({ default: module.ExternalDeployWizard })));

const desktopQuery = "(min-width: 1024px)";
function subscribeViewport(onChange: () => void) {
  const media = window.matchMedia?.(desktopQuery);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}
const isDesktop = () => window.matchMedia?.(desktopQuery).matches ?? true;
const serverViewport = () => false;
const titles: Record<string, string> = {
  navigation: "Проекты и аккаунт", tools: "Инструменты редактора",
  preview: "Предпросмотр приложения", publish: "Публикация приложения",
  max: "Подключение MAX", hosting: "Собственный сервер", services: "Подключение сервисов",
};

/** A single dialog surface over a persistent editor. Only published management is a page. */
export function MaxEditorLayout({ project, children, navigation, tools, preview, launch, launchStatus }: {
  project: Project;
  children: ReactNode;
  navigation: ReactNode;
  tools: ReactNode;
  preview: ReactNode;
  launch: ReactNode;
  launchStatus: string;
  // Kept optional for existing embedding callers; URL-backed state owns the dialog.
  launchOpen?: boolean;
  onLaunchChange?: (open: boolean) => void;
}) {
  const { entry, select: selectEntry } = useMaxEditorEntry(project.id);
  const [modalBusy, setModalBusy] = useState(false);
  function select(next: MaxEditorEntry | null) { if (!modalBusy) selectEntry(next); }
  const desktop = useSyncExternalStore(subscribeViewport, isDesktop, serverViewport);
  const trigger = useRef<HTMLElement | null>(null);
  const dataSection = entry?.startsWith("data:") ? entry.slice(5) as SetupSection : null;
  const modal = entry && !dataSection && !(entry === "preview" && desktop) ? entry : null;
  function open(next: MaxEditorEntry, target?: HTMLElement) {
    if (target) trigger.current = target;
    select(next);
  }
  function interceptLink(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element).closest<HTMLAnchorElement>("a[href]");
    if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
    const next = maxEditorLinkEntry(anchor.href, project.id, window.location.origin);
    if (next === undefined) return;
    // Capture also receives React portal events. Do not allow Link/router or old onClose handlers to remount the chat.
    event.preventDefault(); event.stopPropagation();
    select(next);
  }
  return (
    <div data-product-shell data-max-editor className="max-editor" onClickCapture={interceptLink}>
      <header className="max-editor-header">
        <div className="max-editor-project">
          <div className="max-editor-brand"><BrandMark href="/max" /></div>
          <button className="max-editor-project-trigger" data-testid="max-navigation-open" aria-label="Проекты и аккаунт"
            onClick={event => open("navigation", event.currentTarget)}>
            <span><strong>{project.name}</strong><small>MAX Studio</small></span><ChevronDown className="size-4 shrink-0" />
          </button>
        </div>
        <div className="max-editor-header-actions">
          <MaxEditorDataDialog projectId={project.id} controlled={{ section: dataSection, onOpenChange: value => select(value ? "data:details" : null) }} />
          <Button variant="outline" size="icon" aria-label="Инструменты редактора" onClick={event => open("tools", event.currentTarget)}><Ellipsis className="size-5" /></Button>
          <Button variant="outline" size="icon" onClick={event => open("preview", event.currentTarget)} className="max-editor-mobile-preview-trigger" aria-label="Открыть живое превью" data-testid="max-mobile-preview-open"><PanelRightOpen className="size-4" /></Button>
          <Button data-testid="max-launch-open" title={launchStatus} className="max-editor-publish" onClick={event => open("publish", event.currentTarget)}>Опубликовать <ArrowUpRight className="size-4" /></Button>
        </div>
      </header>
      <div className="max-editor-workspace">
        <section className="max-editor-conversation" aria-label="Редактор приложения">
          <div className="max-editor-section-heading"><h1>Редактор</h1><p>Создавайте и меняйте приложение в диалоге</p></div>
          <div className="max-studio-chat min-h-0 flex-1 overflow-hidden">{children}</div>
        </section>
        {desktop && <div className="max-editor-desktop-preview">{preview}</div>}
      </div>
      <Dialog.Root open={!!modal} onOpenChange={value => { if (!value) select(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="max-editor-overlay" />
          <Dialog.Content data-product-shell data-max-editor data-max-studio className={`max-editor-modal max-editor-modal-${modal}`}
            data-testid={modal === "preview" ? "max-mobile-preview" : "max-editor-modal"} aria-describedby={undefined}
            onEscapeKeyDown={event => { if (modalBusy) event.preventDefault(); }}
            onPointerDownOutside={event => { if (modalBusy) event.preventDefault(); }}
            onCloseAutoFocus={event => {
              event.preventDefault();
              // Moving between data and another modal must not pull focus out of the new dialog.
              queueMicrotask(() => { if (!document.querySelector('[role="dialog"]')) {
                if (trigger.current?.isConnected) trigger.current.focus();
                else document.querySelector<HTMLButtonElement>('[data-testid="max-launch-open"]')?.focus();
              } });
            }}>
            <div className="max-editor-modal-heading">
              <div><span className="max-editor-modal-project">{project.name}</span><Dialog.Title>{titles[modal ?? ""]}</Dialog.Title></div>
              <Dialog.Close asChild><Button variant="outline" size="icon" disabled={modalBusy} aria-label="Закрыть окно"><X className="size-5" /></Button></Dialog.Close>
            </div>
            {modal && ["max", "services", "hosting"].includes(modal) && <div className="max-editor-modal-back"><Button variant="outline" size="sm" disabled={modalBusy} onClick={() => select("publish")}><ArrowLeft className="size-4" />К публикации</Button></div>}
            <div className="max-editor-modal-scroll" key={modal}>
              <Suspense fallback={<p role="status">Открываем настройки…</p>}>
              {modal === "navigation" && <div data-testid="max-navigation-scroll" className="max-editor-drawer-scroll">{navigation}</div>}
              {modal === "tools" && <div className="max-editor-tools"><p>Файлы, расход на генерацию и дополнительные сервисы.</p>{tools}</div>}
              {modal === "preview" && <div className="max-editor-modal-preview-content">{preview}</div>}
              {modal === "publish" && launch}
              {modal === "max" && <MaxConnectionWizard projectId={project.id} onNavigate={select} onBusyChange={setModalBusy} />}
              {modal === "hosting" && <><p className="max-editor-modal-lead">Это необязательно. По умолчанию приложение размещается на хостинге Omnia. Свой сервер нужен, только если вы хотите управлять размещением самостоятельно.</p><ExternalDeployWizard projectId={project.id} maxStudio /></>}
              {modal === "services" && <FigmaIntegrationHub projectId={project.id} projectName={project.name} embedded onBusyChange={setModalBusy} onExit={() => selectEntry(null)} />}
              </Suspense>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
