import {
  ArrowLeft,
  BarChart3,
  Bot,
  CircleHelp,
  LayoutGrid,
  Plug,
  Rocket,
  Settings2,
  Smartphone,
} from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";

const navigation = [
  ["Редактор", "", Smartphone],
  ["Интеграции", "/integrations", Plug],
  ["MAX и приложение", "/settings", Bot],
  ["Публикация", "/publish", Rocket],
  ["После запуска", "/dashboard", BarChart3],
] as const;

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
  active: "integrations" | "settings" | "publish" | "dashboard";
  eyebrow: string;
  title: string;
  lead: string;
  children: React.ReactNode;
}) {
  return (
    <div data-light-shell className="flex h-dvh min-h-0 bg-[#f5f3ee] text-[#171716]">
      <aside className="hidden w-[220px] shrink-0 flex-col border-r border-[#d8d4cb] bg-[#fcfbf7] md:flex">
        <div className="flex h-16 items-center border-b border-[#d8d4cb] px-5"><BrandMark href="/max" /></div>
        <div className="p-3">
          <Link href="/max" className="flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]"><LayoutGrid className="size-4" />Все проекты</Link>
          <p className="omnia-kicker mt-6 px-3 text-[#aaa59b]">Текущий проект</p>
          <p className="mt-2 truncate px-3 text-xs font-semibold">{projectName}</p>
          <nav className="mt-4 space-y-1">
            {navigation.map(([label, suffix, Icon]) => {
              const key = suffix.replace("/", "") || "editor";
              const selected = key === active;
              return (
                <Link key={label} href={`/max/${projectId}${suffix}`} className={`flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs ${selected ? "bg-[#ece8df] font-medium" : "text-[#6d6962] hover:bg-[#f5f3ee]"}`}>
                  <Icon className={`size-4 ${selected ? "text-[#f15a38]" : ""}`} />{label}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="mt-auto border-t border-[#d8d4cb] p-3">
          <Link href="/account" className="flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]"><Settings2 className="size-4" />Аккаунт</Link>
          <Link href="/max/product" className="mt-1 flex h-9 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]"><CircleHelp className="size-4" />Справка</Link>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#d8d4cb] bg-[#fcfbf7] px-5 sm:px-7">
          <Link href={`/max/${projectId}`} className="inline-flex items-center gap-2 text-xs text-[#6d6962] hover:text-[#171716]"><ArrowLeft className="size-4" />В редактор</Link>
          <span className="truncate text-xs font-medium text-[#8d887f]">{projectName}</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto px-5 py-8 sm:px-8 sm:py-10 lg:px-12">
          <div className="mx-auto max-w-[1120px]">
            <header className="border-b border-[#d8d4cb] pb-8">
              <p className="omnia-kicker text-[#f15a38]">{eyebrow}</p>
              <h1 className="mt-3 text-[36px] font-semibold tracking-[-.045em] sm:text-[46px]">{title}</h1>
              <p className="mt-3 max-w-[700px] text-sm leading-6 text-[#6d6962]">{lead}</p>
            </header>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
