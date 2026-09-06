"use client";

import { useState, useSyncExternalStore, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowUpRight, ChevronDown, Ellipsis, PanelRightOpen, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { BrandMark } from "@/components/marketing/BrandMark";
import { Button } from "@/components/ui/button";
import type { Project } from "@/lib/api/types";
import "./max-editor.css";

const desktopQuery = "(min-width: 1024px)";
function subscribeViewport(onChange: () => void) {
  const media = window.matchMedia?.(desktopQuery);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}
const isDesktop = () => window.matchMedia?.(desktopQuery).matches ?? true;
const serverViewport = () => false;

/** Editor-only chrome. Generation, version selection and launch remain owned by the workspace. */
export function MaxEditorLayout({ project, children, navigation, tools, preview, launch, launchStatus, launchOpen, onLaunchChange }: {
  project: Project;
  children: ReactNode;
  navigation: ReactNode;
  tools: ReactNode;
  preview: ReactNode;
  launch: ReactNode;
  launchStatus: string;
  launchOpen: boolean;
  onLaunchChange: (open: boolean) => void;
}) {
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const desktop = useSyncExternalStore(subscribeViewport, isDesktop, serverViewport);
  return (
    <div data-product-shell data-max-editor className="max-editor">
      <header className="max-editor-header">
        <div className="max-editor-project">
          <div className="max-editor-brand"><BrandMark href="/max" /></div>
          <Dialog.Root open={navigationOpen} onOpenChange={setNavigationOpen}>
            <Dialog.Trigger asChild>
              <button className="max-editor-project-trigger" data-testid="max-navigation-open" aria-label="Проекты и аккаунт">
                <span><strong>{project.name}</strong><small>MAX Studio</small></span>
                <ChevronDown className="size-4 shrink-0" />
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="max-editor-overlay" />
              <Dialog.Content data-product-shell data-max-editor className="max-editor-drawer max-editor-navigation-drawer">
                <div className="max-editor-drawer-heading">
                  <Dialog.Title>Проекты и аккаунт</Dialog.Title>
                  <Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="Закрыть меню"><X className="size-5" /></Button></Dialog.Close>
                </div>
                <Dialog.Description className="sr-only">Переключение приложений, разделы проекта и настройки аккаунта.</Dialog.Description>
                <div className="max-editor-drawer-scroll" onClick={(event) => {
                  if ((event.target as HTMLElement).closest("a[href]")) setNavigationOpen(false);
                }}>{navigation}</div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
        <div className="max-editor-header-actions">
          <Button asChild variant="outline" className="max-editor-data-button">
            <Link href={`/max/${project.id}/settings?tab=app`} aria-label="Данные приложения"><SlidersHorizontal className="size-4" /><span>Данные</span></Link>
          </Button>
          <Dialog.Root>
            <Dialog.Trigger asChild>
              <Button variant="outline" size="icon" aria-label="Инструменты редактора"><Ellipsis className="size-5" /></Button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="max-editor-overlay" />
              <Dialog.Content data-product-shell data-max-editor className="max-editor-tools-dialog">
                <div className="max-editor-drawer-heading">
                  <Dialog.Title>Инструменты редактора</Dialog.Title>
                  <Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="Закрыть инструменты"><X className="size-5" /></Button></Dialog.Close>
                </div>
                <Dialog.Description>Расход на генерацию, файлы и подключённые сервисы.</Dialog.Description>
                <div className="max-editor-tools">{tools}</div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
          <Button variant="outline" size="icon" onClick={() => setPreviewOpen(true)} className="max-editor-mobile-preview-trigger" aria-label="Открыть живое превью" data-testid="max-mobile-preview-open"><PanelRightOpen className="size-4" /></Button>
          <Dialog.Root open={launchOpen} onOpenChange={onLaunchChange}>
            <Dialog.Trigger asChild>
              <Button data-testid="max-launch-open" title={launchStatus} className="max-editor-publish">Опубликовать <ArrowUpRight className="size-4" /></Button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="max-editor-overlay" />
              <Dialog.Content className="max-editor-launch-drawer" aria-describedby={undefined}>
                <Dialog.Title className="sr-only">Публикация приложения</Dialog.Title>
                {launch}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
      </header>

      <div className="max-editor-workspace">
        <section className="max-editor-conversation" aria-label="Редактор приложения">
          <div className="max-editor-section-heading"><h1>Редактор</h1><p>Создавайте и меняйте приложение в диалоге</p></div>
          <div className="max-studio-chat min-h-0 flex-1 overflow-hidden">{children}</div>
        </section>
        {desktop && <div className="max-editor-desktop-preview">{preview}</div>}
      </div>
      <Dialog.Root open={!desktop && previewOpen} onOpenChange={setPreviewOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="max-editor-overlay" />
          <Dialog.Content data-product-shell data-max-editor className="max-editor-drawer max-editor-preview-drawer" data-testid="max-mobile-preview" aria-describedby={undefined}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              document.querySelector<HTMLButtonElement>("[data-testid='max-mobile-preview-open']")?.focus();
            }}>
            <div className="max-editor-drawer-heading">
              <Dialog.Title>Предпросмотр приложения</Dialog.Title>
              <Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="Закрыть превью"><X className="size-5" /></Button></Dialog.Close>
            </div>
            <div className="min-h-0 flex-1">{preview}</div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
