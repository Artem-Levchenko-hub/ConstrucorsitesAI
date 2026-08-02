import {
  ArrowLeft,
  CircleHelp,
  LayoutGrid,
  Settings2,
} from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";
import {
  MaxProjectNav,
  type MaxProjectNavKey,
} from "@/components/max/MaxProjectNav";

const helpHref: Record<MaxProjectNavKey, string> = {
  editor: "/max/guide#builder",
  app: "/max/guide#settings",
  integrations: "/max/guide#integrations",
  bot: "/max/guide#max-bot",
  publish: "/max/guide#publish",
  dashboard: "/max/guide#operations",
};

export function MaxSectionShell({
  projectId,
  projectName,
  active,
  eyebrow,
  title,
  lead,
  children,
}: {
  projectId: string;
  projectName: string;
  active: MaxProjectNavKey;
  eyebrow: string;
  title: string;
  lead: string;
  children: React.ReactNode;
}) {
  return (
    <div data-light-shell className="flex h-dvh min-h-0 bg-[#f5f3ee] text-[#171716]">
      <aside className="hidden w-[220px] shrink-0 flex-col border-r border-[#d8d4cb] bg-[#fcfbf7] lg:flex">
        <div className="flex h-16 items-center border-b border-[#d8d4cb] px-5"><BrandMark href="/max" /></div>
        <div className="p-3">
          <Link href="/max" className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]"><LayoutGrid className="size-4" />Все проекты</Link>
          <p className="omnia-kicker mt-6 px-3 text-[#aaa59b]">Текущий проект</p>
          <p className="mt-2 truncate px-3 text-xs font-semibold">{projectName}</p>
          <div className="mt-4">
            <MaxProjectNav projectId={projectId} active={active} />
          </div>
        </div>
        <div className="mt-auto border-t border-[#d8d4cb] p-3">
          <Link href="/account" className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]"><Settings2 className="size-4" />Аккаунт</Link>
          <Link href="/max/start" className="mt-1 flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]"><CircleHelp className="size-4" />Быстрый старт</Link>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#d8d4cb] bg-[#fcfbf7] px-3 sm:px-7">
          <Link href={`/max/${projectId}`} className="inline-flex min-h-11 items-center gap-2 text-xs text-[#6d6962] hover:text-[#171716]"><ArrowLeft className="size-4" />В редактор</Link>
          <span className="truncate text-xs font-medium text-[#8d887f]">{projectName}</span>
        </header>
        <div className="shrink-0 overflow-x-auto border-b border-[#d8d4cb] bg-[#fcfbf7] lg:hidden">
          <MaxProjectNav
            projectId={projectId}
            active={active}
            showProgress={false}
            variant="mobile"
          />
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 sm:py-10 lg:px-12">
          <div className="mx-auto max-w-[1120px]">
            <header className="border-b border-[#d8d4cb] pb-8">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="omnia-kicker text-accent">{eyebrow}</p>
                <Link href={helpHref[active]} className="inline-flex min-h-11 items-center gap-2 text-xs font-medium text-[#6d6962] hover:text-accent">
                  <CircleHelp className="size-3.5" />
                  Помощь по этому шагу
                </Link>
              </div>
              <h1 className="mt-3 text-[32px] font-semibold tracking-[-.045em] sm:text-[46px]">{title}</h1>
              <p className="mt-3 max-w-[700px] text-sm leading-6 text-[#6d6962]">{lead}</p>
            </header>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
