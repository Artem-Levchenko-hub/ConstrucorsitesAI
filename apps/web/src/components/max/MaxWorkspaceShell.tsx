"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  BarChart3,
  Bot,
  ChevronDown,
  CircleHelp,
  CreditCard,
  LayoutGrid,
  LogOut,
  Menu,
  Plug,
  Rocket,
  Settings,
  Smartphone,
  X,
} from "lucide-react";
import Link from "next/link";

import { logoutAction } from "@/app/(auth)/actions";
import { BrandMark } from "@/components/marketing/BrandMark";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { listProjects } from "@/lib/api/projects";
import type { Project } from "@/lib/api/types";
import { MaxLaunchPanel } from "./MaxLaunchPanel";
import { MaxLivePreview } from "./MaxLivePreview";

export function MaxWorkspaceShell({
  project,
  email,
}: {
  project: Project;
  email: string;
}) {
  const [launchOpen, setLaunchOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const maxProjects = useMemo(
    () => (projects.data ?? []).filter((item) => item.template === "max_miniapp"),
    [projects.data],
  );

  return (
    <div data-light-shell className="grid h-dvh min-h-0 grid-cols-1 overflow-hidden bg-[#f5f3ee] text-[#171716] lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(420px,1fr)_380px] 2xl:grid-cols-[220px_minmax(480px,1fr)_420px]">
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[220px] flex-col border-r border-[#d8d4cb] bg-[#fcfbf7] transition-transform lg:static lg:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 items-center justify-between border-b border-[#d8d4cb] px-5">
          <BrandMark href="/max" />
          <button type="button" onClick={() => setMobileNavOpen(false)} className="grid size-11 place-items-center rounded-[8px] text-[#8d887f] lg:hidden" aria-label="Закрыть меню">
            <X className="size-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <Link href="/max" className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
            <LayoutGrid className="size-4" /> Все проекты
          </Link>
          <p className="omnia-kicker mt-6 px-3 text-[#aaa59b]">Ваши Mini Apps</p>
          <nav className="mt-2 space-y-1">
            {maxProjects.map((item) => {
              const active = item.id === project.id;
              return (
                <Link key={item.id} href={`/max/${item.id}`} className={`flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition ${active ? "bg-[#ece8df] font-medium" : "text-[#6d6962] hover:bg-[#f5f3ee]"}`}>
                  <Smartphone className={`size-4 ${active ? "text-[#f15a38]" : ""}`} />
                  <span className="min-w-0 flex-1 truncate">{item.name}</span>
                  {active && <span className="size-1.5 rounded-full bg-[#248a4b]" />}
                </Link>
              );
            })}
          </nav>

          <p className="omnia-kicker mt-7 px-3 text-[#aaa59b]">Проект</p>
          <nav className="mt-2 space-y-1">
            <Link href={`/max/${project.id}/integrations`} className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
              <Plug className="size-4" /> Интеграции
            </Link>
            <Link href={`/max/${project.id}/settings`} className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
              <Bot className="size-4" /> MAX и приложение
            </Link>
            <Link href={`/max/${project.id}/publish`} className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
              <Rocket className="size-4" /> Публикация
            </Link>
            <Link href={`/max/${project.id}/dashboard`} className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
              <BarChart3 className="size-4" /> После запуска
            </Link>
          </nav>
        </div>

        <div className="border-t border-[#d8d4cb] p-3">
          <Link href="/account" className="flex min-h-11 min-w-0 items-center gap-2.5 rounded-[8px] p-2 hover:bg-[#f5f3ee]">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[#171716] text-[11px] font-semibold text-white">{email.slice(0, 1).toUpperCase()}</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium">{email.split("@")[0]}</span><span className="block truncate text-[9px] text-[#8d887f]">{email}</span></span>
            <Settings className="size-3.5 text-[#8d887f]" />
          </Link>
          <form action={logoutAction} className="mt-1">
            <button type="submit" className="flex min-h-11 w-full items-center gap-2 rounded-[8px] px-2 text-[10px] text-[#8d887f] hover:bg-[#f5f3ee]"><LogOut className="size-3.5" />Выйти</button>
          </form>
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col border-r border-[#d8d4cb] bg-[#fcfbf7]">
        <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-[#d8d4cb] px-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-1 sm:gap-3">
            <button type="button" onClick={() => setMobileNavOpen(true)} className="grid size-11 shrink-0 place-items-center rounded-[8px] text-[#6d6962] lg:hidden" aria-label="Открыть меню"><Menu className="size-4" /></button>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold">{project.name}</h1>
              <p className="mt-0.5 flex items-center gap-1.5 text-[9px] text-[#8d887f]"><span className="size-1.5 rounded-full bg-[#248a4b]" /> Состояние сохраняется на сервере</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
            <Link href={`/max/${project.id}/integrations`} className="hidden h-11 items-center rounded-[8px] border border-[#d8d4cb] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee] md:inline-flex">Интеграции</Link>
            <button
              type="button"
              onClick={() => setPreviewOpen(true)}
              className="grid size-11 place-items-center rounded-[8px] border border-[#d8d4cb] text-[#6d6962] hover:bg-[#f5f3ee] xl:hidden"
              aria-label="Открыть живое превью"
              data-testid="max-mobile-preview-open"
            >
              <Smartphone className="size-4" />
            </button>
            <button type="button" onClick={() => setLaunchOpen(true)} className="inline-flex h-11 items-center gap-1.5 rounded-[8px] bg-[#f15a38] px-3 text-xs font-semibold text-white hover:bg-[#d94929] sm:gap-2 sm:px-4">
              <span className="sm:hidden">Пуск</span>
              <span className="hidden sm:inline">Опубликовать</span>
              <ChevronDown className="size-3.5" />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 max-studio-chat">
          <ChatPanel
            projectId={project.id}
            projectSlug={project.slug}
            mode="max"
            basePath={`/max/${project.id}`}
            embedded
          />
        </div>
      </section>

      <div className="hidden min-h-0 bg-[#f5f3ee] xl:block">
        <MaxLivePreview project={project} />
      </div>

      {mobileNavOpen && <button type="button" className="fixed inset-0 z-40 bg-[#171716]/55 lg:hidden" onClick={() => setMobileNavOpen(false)} aria-label="Закрыть меню" />}

      {previewOpen && (
        <div className="fixed inset-0 z-[60] flex justify-end bg-[#171716]/55 backdrop-blur-[2px] xl:hidden">
          <button
            type="button"
            className="absolute inset-0 cursor-default"
            onClick={() => setPreviewOpen(false)}
            aria-label="Закрыть живое превью"
          />
          <section
            className="relative flex h-full w-full max-w-[460px] flex-col bg-[#f5f3ee] shadow-[-30px_0_80px_rgba(0,0,0,.16)]"
            aria-label="Живое превью приложения"
            data-testid="max-mobile-preview"
          >
            <div className="flex h-14 shrink-0 items-center justify-between border-b border-[#d8d4cb] px-3 sm:px-5">
              <p className="text-sm font-semibold">Превью приложения</p>
              <button
                type="button"
                onClick={() => setPreviewOpen(false)}
                className="grid size-11 place-items-center rounded-[8px] text-[#6d6962] hover:bg-[#ece8df]"
                aria-label="Закрыть превью"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1">
              <MaxLivePreview project={project} />
            </div>
          </section>
        </div>
      )}

      {launchOpen && (
        <div className="fixed inset-0 z-[70] flex justify-end bg-[#171716]/45 backdrop-blur-[2px]">
          <button type="button" className="absolute inset-0 cursor-default" onClick={() => setLaunchOpen(false)} aria-label="Закрыть публикацию" />
          <div className="relative h-full w-full max-w-[420px] shadow-[-30px_0_80px_rgba(0,0,0,.16)]">
            <MaxLaunchPanel project={project} onClose={() => setLaunchOpen(false)} />
          </div>
        </div>
      )}
    </div>
  );
}
