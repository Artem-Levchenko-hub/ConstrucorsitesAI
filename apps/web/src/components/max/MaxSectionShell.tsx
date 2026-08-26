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
    <div data-product-shell className="flex h-dvh min-h-0 bg-[#121519] text-white">
      <aside className="hidden w-[220px] shrink-0 flex-col border-r border-[#2b2d32] bg-[#191b20] lg:flex">
        <div className="flex h-16 items-center border-b border-[#2b2d32] px-5"><BrandMark href="/max" /></div>
        <div className="p-3">
          <Link href="/max" className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#9fa1b1] hover:bg-[#121519]"><LayoutGrid className="size-4" />Все проекты</Link>
          <p className="omnia-kicker mt-6 px-3 text-[#828491]">Текущий проект</p>
          <p className="mt-2 truncate px-3 text-xs font-semibold">{projectName}</p>
          <div className="mt-4">
            <MaxProjectNav projectId={projectId} active={active} />
          </div>
        </div>
        <div className="mt-auto border-t border-[#2b2d32] p-3">
          <Link href="/account" className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#9fa1b1] hover:bg-[#121519]"><Settings2 className="size-4" />Аккаунт</Link>
          <Link href="/max/start" className="mt-1 flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#9fa1b1] hover:bg-[#121519]"><CircleHelp className="size-4" />Быстрый старт</Link>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#2b2d32] bg-[#191b20] px-3 sm:px-7">
          <Link href={`/max/${projectId}`} className="inline-flex min-h-11 items-center gap-2 text-xs text-[#9fa1b1] hover:text-white"><ArrowLeft className="size-4" />В редактор</Link>
          <span className="truncate text-xs font-medium text-[#828491]">{projectName}</span>
        </header>
        <div className="shrink-0 overflow-x-auto border-b border-[#2b2d32] bg-[#191b20] lg:hidden">
          <MaxProjectNav
            projectId={projectId}
            active={active}
            showProgress={false}
            variant="mobile"
          />
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 sm:py-10 lg:px-12">
          <div className="mx-auto max-w-[1120px]">
            <header className="border-b border-[#2b2d32] pb-8">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="omnia-kicker text-[#4f81f7]">{eyebrow}</p>
                <Link href={helpHref[active]} className="inline-flex min-h-11 items-center gap-2 text-xs font-medium text-[#9fa1b1] hover:text-[#6a95fa]">
                  <CircleHelp className="size-3.5" />
                  Помощь по этому шагу
                </Link>
              </div>
              <h1 className="mt-3 text-[32px] font-semibold tracking-[-.045em] sm:text-[46px]">{title}</h1>
              <p className="mt-3 max-w-[700px] text-sm leading-6 text-[#9fa1b1]">{lead}</p>
            </header>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
