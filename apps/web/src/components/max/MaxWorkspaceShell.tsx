"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  CreditCard,
  Database,
  LogOut,
  Menu,
  Settings,
  Waypoints,
  X,
} from "lucide-react";
import Link from "next/link";

import { logoutAction } from "@/app/(auth)/actions";
import { listProjects } from "@/lib/api/projects";
import type { Project } from "@/lib/api/types";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { BrandMark } from "@/components/marketing/BrandMark";
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
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const maxProjects = useMemo(
    () =>
      (projects.data ?? []).filter(
        (item) => item.template === "max_miniapp",
      ),
    [projects.data],
  );

  return (
    <div className="grid h-dvh min-h-0 grid-cols-1 overflow-hidden bg-[#080a10] text-white lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[260px_minmax(0,1fr)_360px]">
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col border-r border-[#151a2c] bg-[#080a10] p-4 transition-transform lg:static lg:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between">
          <BrandMark inverse href="/max" label="MaxStudio" />
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            className="rounded-lg p-2 text-[#7b89a4] lg:hidden"
            aria-label="Закрыть меню"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-7 min-h-0 flex-1 overflow-y-auto">
          <p className="px-3 text-[10px] font-semibold uppercase tracking-[0.04em] text-[#60708d]">
            Ваши Mini Apps
          </p>
          <nav className="mt-3 space-y-1">
            {maxProjects.map((item) => {
              const active = item.id === project.id;
              return (
                <Link
                  key={item.id}
                  href={`/max/${item.id}`}
                  className={`flex h-[38px] items-center gap-3 rounded-lg border px-3 text-[13px] transition-colors ${
                    active
                      ? "border-[#202946] bg-[#13172a] text-white"
                      : "border-transparent text-[#94a3b8] hover:bg-[#0f121f] hover:text-white"
                  }`}
                >
                  <Waypoints className={`h-4 w-4 ${active ? "text-[#3b82f6]" : ""}`} />
                  <span className="min-w-0 flex-1 truncate">{item.name}</span>
                  {active && <span className="h-1.5 w-1.5 rounded-full bg-[#20c997]" />}
                </Link>
              );
            })}
          </nav>

          <p className="mt-8 px-3 text-[10px] font-semibold uppercase tracking-[0.04em] text-[#60708d]">
            Интеграции
          </p>
          <nav className="mt-3 space-y-1">
            <Link
              href={`/max/${project.id}/integrations`}
              className="flex h-[38px] items-center gap-3 rounded-lg px-3 text-[13px] text-[#94a3b8] hover:bg-[#0f121f] hover:text-white"
            >
              <CreditCard className="h-4 w-4" />
              Платежи и CRM
            </Link>
            <button
              type="button"
              onClick={() => setLaunchOpen(true)}
              className="flex h-[38px] w-full items-center gap-3 rounded-lg px-3 text-left text-[13px] text-[#94a3b8] hover:bg-[#0f121f] hover:text-white"
            >
              <Database className="h-4 w-4" />
              Публикация и MAX
            </button>
          </nav>
        </div>

        <div className="mt-4 rounded-lg bg-[#13172a] p-2">
          <Link href="/account" className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#27304a] text-[11px] font-semibold">
              {email.slice(0, 1).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] font-medium">{email.split("@")[0]}</span>
              <span className="block truncate text-[10px] text-[#60708d]">{email}</span>
            </span>
            <Settings className="h-4 w-4 text-[#60708d]" />
          </Link>
          <form action={logoutAction} className="mt-2 border-t border-[#202946] pt-2">
            <button type="submit" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11px] text-[#7b89a4] hover:bg-white/[0.04] hover:text-white">
              <LogOut className="h-3.5 w-3.5" />
              Выйти
            </button>
          </form>
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#151a2c] px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => setMobileNavOpen(true)}
              className="rounded-lg p-2 text-[#94a3b8] lg:hidden"
              aria-label="Открыть меню"
            >
              <Menu className="h-4 w-4" />
            </button>
            <h1 className="truncate text-[15px] font-semibold">{project.name}</h1>
            <span className="hidden rounded-md bg-[#082b2a] px-2.5 py-1 text-[10px] font-semibold uppercase text-[#20c997] sm:inline">
              Активный черновик
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href={`/max/${project.id}/integrations`}
              className="hidden h-10 items-center rounded-lg border border-[#202946] px-4 text-[12px] font-semibold text-[#94a3b8] hover:text-white sm:inline-flex"
            >
              Интеграции
            </Link>
            <button
              type="button"
              onClick={() => setLaunchOpen(true)}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-gradient-to-r from-[#3b82f6] to-[#8b5cf6] px-5 text-[13px] font-semibold text-white"
            >
              Опубликовать в MAX
              <ChevronDown className="h-3.5 w-3.5" />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1">
          <ChatPanel
            projectId={project.id}
            projectSlug={project.slug}
            mode="max"
            basePath={`/max/${project.id}`}
            embedded
          />
        </div>
      </section>

      <div className="hidden min-h-0 xl:block">
        <MaxLivePreview project={project} />
      </div>

      {mobileNavOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/70 lg:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-label="Закрыть меню"
        />
      )}

      {launchOpen && (
        <div className="fixed inset-0 z-[70] flex justify-end bg-[#03050a]/80 backdrop-blur-sm">
          <button
            type="button"
            className="absolute inset-0 cursor-default"
            onClick={() => setLaunchOpen(false)}
            aria-label="Закрыть публикацию"
          />
          <div className="relative h-full w-full max-w-[420px] shadow-[-30px_0_80px_rgba(0,0,0,0.35)]">
            <MaxLaunchPanel
              project={project}
              onClose={() => setLaunchOpen(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
