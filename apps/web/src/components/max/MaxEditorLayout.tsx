"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import Link from "next/link";
import { ArrowUpRight, PanelRightOpen, Plug, SlidersHorizontal, X } from "lucide-react";
import { MaxMobileNav } from "./MaxMobileNav";
import { MaxProjectHeader } from "./MaxProjectHeader";
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
  preview: "Превью",
};

export function MaxEditorLayout({ project, email, children, navigation, preview, initialPreview = false }: {
  project: Project;
  initialPreview?: boolean;
  email?: string;
  children: ReactNode;
  navigation?: ReactNode;
  preview: ReactNode | ((onClose?: () => void) => ReactNode);
}) {
  const [entry, setEntry] = useState<keyof typeof titles | null>(initialPreview ? "preview" : null);
  const desktop = useSyncExternalStore(subscribeViewport, isDesktop, serverViewport);
  const editor = useRef<HTMLDivElement>(null);
  const syncViewportAfterFocus = useRef<(() => void) | null>(null);
  const previewDialog = useRef<HTMLDivElement>(null);
  const closePreview = useCallback(() => {
    setEntry(null);
    const location = new URL(window.location.href);
    if (location.searchParams.get("preview") === "1") {
      location.searchParams.delete("preview");
      window.history.replaceState(window.history.state, "", location);
    }
  }, []);
  const trigger = useRef<HTMLElement | null>(null);
  const projectHref = `/max/${project.id}`;
  const modal = entry === "preview" && desktop ? null : entry;
  const previewContent = typeof preview === "function"
    ? preview(modal === "preview" ? closePreview : undefined)
    : preview;
  useEffect(() => {
    const viewport = window.visualViewport;
    const element = editor.current;
    if (!element || desktop) return;
    let frame = 0;
    let settled = 0;

    const resetViewport = () => {
      document.body.style.removeProperty("--max-editor-visual-height");
      document.body.style.removeProperty("--max-editor-visual-top");
      delete element.dataset.keyboardOpen;
    };
    const updateViewport = () => {
      frame = 0;
      if (viewport && viewport.scale !== 1) {
        resetViewport();
        return;
      }
      // Only the chat history scrolls. WebKit can also pan the outer document
      // to reveal the caret and leave that offset behind when the keyboard closes.
      if (window.scrollY !== 0) window.scrollTo(0, 0);
      const height = viewport?.height ?? window.innerHeight;
      const top = viewport?.offsetTop ?? 0;
      // Preview is portaled to the body. Its tab bar and the chat use the same visible area.
      document.body.style.setProperty("--max-editor-visual-height", height + "px");
      document.body.style.setProperty("--max-editor-visual-top", top + "px");
      element.toggleAttribute("data-keyboard-open", window.innerHeight - height - top > 96);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(updateViewport);
    };
    const onResize = () => {
      schedule();
      // Safari can publish its final keyboard geometry after the last resize.
      window.clearTimeout(settled);
      settled = window.setTimeout(schedule, 480);
    };

    updateViewport();
    viewport?.addEventListener("resize", onResize);
    viewport?.addEventListener("scroll", schedule);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("pageshow", onResize);
    // React focus events reach the editor even from the textarea's iOS iframe portal.
    syncViewportAfterFocus.current = onResize;
    return () => {
      viewport?.removeEventListener("resize", onResize);
      viewport?.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("pageshow", onResize);
      syncViewportAfterFocus.current = null;
      cancelAnimationFrame(frame);
      window.clearTimeout(settled);
      resetViewport();
    };
  }, [desktop]);

  function openPreview(target: HTMLElement) {
    trigger.current = target;
    setEntry("preview");
  }
  return (
    <div ref={editor} data-product-shell data-max-editor className="max-editor"
      onFocusCapture={() => syncViewportAfterFocus.current?.()} onBlurCapture={() => syncViewportAfterFocus.current?.()}>
      <header className="max-editor-header max-shared-project-header">
        <MaxProjectHeader projectId={project.id} projectName={project.name} active="editor" email={email} navigation={navigation} />
        <div className="max-editor-header-actions">
          <LearningButton />
          <Button asChild variant="outline" className="max-editor-section-link">
            <Link data-learning="editor-manage" href={`${projectHref}/manage`}><SlidersHorizontal aria-hidden="true" />Управление</Link>
          </Button>
          <Button asChild variant="outline" className="max-editor-section-link">
            <Link data-learning="editor-services" href={`${projectHref}/integrations?tab=services`} data-testid="max-integrations-open"><Plug aria-hidden="true" />Интеграции</Link>
          </Button>
          <Button data-learning="editor-preview" variant="outline" onClick={event => openPreview(event.currentTarget)}
            className="max-editor-mobile-preview-trigger" aria-label="Открыть живое превью" aria-haspopup="dialog"
            aria-expanded={modal === "preview"} data-testid="max-mobile-preview-open">
            <PanelRightOpen aria-hidden="true" /> <span>Превью</span>
          </Button>
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
      <MaxMobileNav projectId={project.id} active="chat" onPreview={openPreview} previewDialog={previewDialog} onClosePreview={closePreview} />
      <Dialog.Root open={!!modal} onOpenChange={value => { if (!value) closePreview(); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="max-editor-overlay" />
          <Dialog.Content ref={previewDialog} data-product-shell data-max-editor data-max-studio data-learning-context={modal ?? undefined} className={`max-editor-modal max-editor-modal-${modal}`}
            data-testid={modal === "preview" ? "max-mobile-preview" : "max-editor-modal"} aria-describedby={undefined}
            onCloseAutoFocus={event => {
              event.preventDefault();
              const opener = trigger.current;
              const hiddenPreview = desktop && (opener?.matches(".max-editor-mobile-preview-trigger") || !!opener?.closest(".max-mobile-nav"));
              if (opener?.isConnected && !hiddenPreview) opener.focus();
              else editor.current?.querySelector<HTMLElement>(desktop ? '[data-testid="max-launch-open"]' : ".max-mobile-nav button")?.focus();
            }}>
            <div className="max-editor-modal-heading">
              <div><span className="max-editor-modal-project">{project.name}</span><Dialog.Title>{modal ? titles[modal] : undefined}</Dialog.Title></div>
              <div className="max-editor-modal-actions flex items-center gap-2"><LearningButton /><Dialog.Close asChild><Button variant="outline" size="icon" aria-label="Закрыть окно"><X aria-hidden="true" className="size-5" /></Button></Dialog.Close></div>
            </div>
            <div data-learning="editor-panel" className="max-editor-modal-scroll" key={modal}>
              {modal === "preview" && <div className="max-editor-modal-preview-content">{previewContent}</div>}
            </div>
            {modal === "preview" && <MaxMobileNav projectId={project.id} active="preview" onClosePreview={closePreview} />}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
