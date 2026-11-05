"use client";

import { useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import Link from "next/link";
import { ArrowUpRight, ChevronDown, PanelRightOpen, Plug, SlidersHorizontal, X } from "lucide-react";
import { BrandMark } from "@/components/marketing/BrandMark";
import { Button } from "@/components/ui/button";
import { LearningButton } from "@/components/learning/ProductLearning";
import type { Project } from "@/lib/api/types";
import "./max-editor.css";
import "./max-editor-modals.css";

const desktopQuery = "(min-width: 1024px)";
function subscribeViewport(onChange: () => void) {
  const media = window.matchMedia?.(desktopQuery);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}
const isDesktop = () => window.matchMedia?.(desktopQuery).matches ?? true;
const serverViewport = () => false;
const titles = {
  navigation: "Проекты и аккаунт",
  preview: "Предпросмотр приложения",
};

export function MaxEditorLayout({ project, children, navigation, preview }: {
  project: Project;
  children: ReactNode;
  navigation: ReactNode;
  preview: ReactNode | ((onClose?: () => void) => ReactNode);
}) {
  const [entry, setEntry] = useState<keyof typeof titles | null>(null);
  const desktop = useSyncExternalStore(subscribeViewport, isDesktop, serverViewport);
  const trigger = useRef<HTMLElement | null>(null);
  const projectHref = `/max/${project.id}`;
  const modal = entry === "preview" && desktop ? null : entry;
  const previewContent = typeof preview === "function"
    ? preview(modal === "preview" ? () => setEntry(null) : undefined)
    : preview;
  function open(next: keyof typeof titles, target: HTMLElement) {
    trigger.current = target;
    setEntry(next);
  }
  return (
    <div data-product-shell data-max-editor className="max-editor">
      <header className="max-editor-header">
        <div className="max-editor-project">
          <div className="max-editor-brand"><BrandMark href="/max" /></div>
          <button className="max-editor-project-trigger" data-learning="editor-projects" data-testid="max-navigation-open" aria-label="Проекты и аккаунт"
            onClick={event => open("navigation", event.currentTarget)}>
            <span><strong>{project.name}</strong><small>Yleum</small></span><ChevronDown className="size-4 shrink-0" />
          </button>
        </div>
        <div className="max-editor-header-actions">
          <LearningButton />
          <Button asChild variant="outline" className="max-editor-section-link">
            <Link data-learning="editor-manage" href={`${projectHref}/manage`}><SlidersHorizontal aria-hidden="true" />Управление</Link>
          </Button>
          <Button asChild variant="outline" className="max-editor-section-link">
            <Link data-learning="editor-services" href={`${projectHref}/integrations?tab=services`} data-testid="max-integrations-open"><Plug aria-hidden="true" />Интеграции</Link>
          </Button>
          <Button data-learning="editor-preview" variant="outline" size="icon" onClick={event => open("preview", event.currentTarget)} className="max-editor-mobile-preview-trigger" aria-label="Открыть живое превью" data-testid="max-mobile-preview-open"><PanelRightOpen className="size-4" /></Button>
          <Button asChild className="max-editor-publish">
            <Link data-learning="editor-publish" data-testid="max-launch-open" href={`${projectHref}/publish`}>Публикация <ArrowUpRight aria-hidden="true" /></Link>
          </Button>
        </div>
      </header>
      <div className="max-editor-workspace">
        <section data-learning="editor-conversation" className="max-editor-conversation" aria-label="Редактор приложения">
          <h1 className="sr-only">Редактор приложения</h1>
          <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
        </section>
        {desktop && <div data-learning="editor-preview" className="max-editor-desktop-preview">{previewContent}</div>}
      </div>
      <Dialog.Root open={!!modal} onOpenChange={value => { if (!value) setEntry(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="max-editor-overlay" />
          <Dialog.Content data-product-shell data-max-editor data-max-studio data-learning-context={modal ?? undefined} className={`max-editor-modal max-editor-modal-${modal}`}
            data-testid={modal === "preview" ? "max-mobile-preview" : "max-editor-modal"} aria-describedby={undefined}
            onCloseAutoFocus={event => {
              event.preventDefault();
              const opener = trigger.current;
              const hiddenPreview = desktop && opener?.matches(".max-editor-mobile-preview-trigger");
              if (opener?.isConnected && !hiddenPreview) opener.focus();
              else document.querySelector<HTMLElement>('[data-testid="max-launch-open"]')?.focus();
            }}>
            <div className="max-editor-modal-heading">
              <div><span className="max-editor-modal-project">{project.name}</span><Dialog.Title>{modal ? titles[modal] : undefined}</Dialog.Title></div>
              <div className="flex items-center gap-2"><LearningButton /><Dialog.Close asChild><Button variant="outline" size="icon" aria-label="Закрыть окно"><X className="size-5" /></Button></Dialog.Close></div>
            </div>
            <div data-learning="editor-panel" className="max-editor-modal-scroll" key={modal}>
              {modal === "navigation" && <div data-testid="max-navigation-scroll" className="max-editor-drawer-scroll">{navigation}</div>}
              {modal === "preview" && <div className="max-editor-modal-preview-content">{previewContent}</div>}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
