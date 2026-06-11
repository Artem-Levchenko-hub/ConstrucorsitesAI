"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, Search, Sparkles } from "lucide-react";

import { cn, initials } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CommandPalette,
  useCommandPalette,
  type CommandItem,
} from "./command-palette";

export interface NavItem {
  label: string;
  href: string;
  /** A lucide icon element, e.g. `<LayoutDashboard />`. */
  icon?: React.ReactNode;
}

export interface AppShellUser {
  name?: string | null;
  email?: string | null;
}

/** Plan / trial status capsule for the sidebar footer (Mobbin: Time2book,
 *  7shifts, Vanta). The MVP has no billing, so the Upgrade button renders ONLY
 *  when a working handler is given — pass `onUpgrade` (open a «тарифы скоро»
 *  toast/dialog) or `upgradeHref`; with neither the capsule is status-only and
 *  never a dead button. */
export interface PlanInfo {
  /** Plan name, e.g. "Free", "Старт", "Pro". */
  plan: string;
  /** Days left in trial — renders «осталось N дн.» (urgent tint when ≤3). */
  trialDaysLeft?: number;
  /** Usage meter; `label` overrides the default «used из limit» caption. */
  usage?: { used: number; limit: number; label?: string };
  upgradeHref?: string;
  onUpgrade?: () => void;
  /** CTA label, default «Перейти на Pro». */
  upgradeLabel?: string;
}

export interface AppShellProps {
  /** App name or a logo node, shown at the top of the sidebar. */
  brand: React.ReactNode;
  nav: NavItem[];
  user?: AppShellUser | null;
  onSignOut?: () => void;
  /** Topbar title for the current page (optional — usually a <PageHeader> in
   *  the content does this). */
  title?: React.ReactNode;
  /** Topbar right-side controls. */
  actions?: React.ReactNode;
  /** Extra ⌘K commands beyond navigation (e.g. quick-create actions). The nav
   *  itself is added automatically, so apps get the palette for free. */
  commands?: CommandItem[];
  /** Optional plan/trial status capsule pinned to the sidebar footer. Omit it
   *  and the sidebar stays exactly as before — no footer is rendered. */
  plan?: PlanInfo;
  children: React.ReactNode;
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

function NavLinks({
  nav,
  pathname,
  onNavigate,
}: {
  nav: NavItem[];
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
      {nav.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all [&_svg]:size-4 [&_svg]:shrink-0",
              active
                ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground shadow-sm [&_svg]:text-primary"
                : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            {item.icon}
            <span className="truncate">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function Brand({ brand }: { brand: React.ReactNode }) {
  return (
    <div className="flex h-16 items-center gap-2 border-b border-sidebar-border px-5 text-base font-semibold tracking-tight">
      {brand}
    </div>
  );
}

function UserMenu({
  user,
  onSignOut,
}: {
  user?: AppShellUser | null;
  onSignOut?: () => void;
}) {
  if (!user) return null;
  const name = user.name || user.email || "Аккаунт";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="h-10 gap-2 px-2">
          <Avatar className="size-7">
            <AvatarFallback>{initials(user.name || user.email)}</AvatarFallback>
          </Avatar>
          <span className="hidden max-w-32 truncate text-sm font-medium sm:inline">
            {name}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>
          <div className="flex flex-col">
            <span className="truncate text-sm font-medium">{name}</span>
            {user.email ? (
              <span className="truncate text-xs font-normal text-muted-foreground">
                {user.email}
              </span>
            ) : null}
          </div>
        </DropdownMenuLabel>
        {onSignOut ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onSignOut}>
              <LogOut />
              Выйти
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Topbar button that opens the ⌘K palette and advertises the shortcut — the
 *  discoverable, mouse- and touch-friendly entry point (Mobbin: Replit «Search &
 *  run commands ⌘K»). */
function CommandTrigger({ onOpen }: { onOpen: () => void }) {
  return (
    <Button
      variant="outline"
      onClick={onOpen}
      aria-label="Открыть командную палитру (Ctrl+K)"
      aria-keyshortcuts="Control+K Meta+K"
      className="h-9 gap-2 px-2.5 text-muted-foreground sm:px-3"
    >
      <Search className="size-4" />
      <span className="hidden text-sm font-normal sm:inline">Поиск…</span>
      <kbd className="hidden rounded border border-border bg-muted px-1.5 py-0.5 font-sans text-[10px] font-medium leading-none sm:inline">
        ⌘K
      </kbd>
    </Button>
  );
}

/** Sidebar-footer capsule advertising the current plan / trial status, with an
 *  optional usage meter and Upgrade CTA. Renders the CTA only when it has
 *  somewhere to go, so the MVP (no billing) never ships a dead button. */
function PlanCapsule({
  plan,
  trialDaysLeft,
  usage,
  upgradeHref,
  onUpgrade,
  upgradeLabel,
}: PlanInfo) {
  const pct =
    usage && usage.limit > 0
      ? Math.min(100, Math.max(0, Math.round((usage.used / usage.limit) * 100)))
      : null;
  const urgent = typeof trialDaysLeft === "number" && trialDaysLeft <= 3;
  const hasCta = Boolean(upgradeHref || onUpgrade);
  const label = upgradeLabel ?? "Перейти на Pro";

  return (
    <div className="rounded-xl border border-sidebar-border bg-sidebar-accent/50 p-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold uppercase tracking-wide text-sidebar-foreground/80">
          {plan}
        </span>
        {typeof trialDaysLeft === "number" ? (
          <span
            className={cn(
              "shrink-0 text-[11px] font-medium",
              urgent ? "text-destructive" : "text-sidebar-foreground/60",
            )}
          >
            осталось {trialDaysLeft} дн.
          </span>
        ) : null}
      </div>

      {pct !== null ? (
        <div className="mt-2">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-sidebar-foreground/15"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-500 motion-reduce:transition-none"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="mt-1 text-[11px] text-sidebar-foreground/60">
            {usage!.label ?? `${usage!.used} из ${usage!.limit}`}
          </p>
        </div>
      ) : null}

      {hasCta ? (
        upgradeHref ? (
          <Button asChild size="sm" className="mt-3 h-9 w-full gap-1.5">
            <Link href={upgradeHref}>
              <Sparkles className="size-4" />
              {label}
            </Link>
          </Button>
        ) : (
          <Button size="sm" onClick={onUpgrade} className="mt-3 h-9 w-full gap-1.5">
            <Sparkles className="size-4" />
            {label}
          </Button>
        )
      ) : null}
    </div>
  );
}

/**
 * Responsive application shell — persistent sidebar on desktop (lg+), a slide-in
 * Sheet drawer on mobile, plus a sticky topbar with a ⌘K command palette. Wrap
 * every page's content with it so the app reads like a real product, not a
 * single scrolling page.
 *
 *   <AppShell brand="Моя CRM" nav={NAV} user={me} onSignOut={signOut}>
 *     <PageHeader title="Клиенты" actions={...} />
 *     ...
 *   </AppShell>
 */
export function AppShell({
  brand,
  nav,
  user,
  onSignOut,
  title,
  actions,
  commands,
  plan,
  children,
}: AppShellProps) {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = React.useState(false);
  const [cmdOpen, setCmdOpen] = useCommandPalette();

  // Every nav item becomes a "go to" command for free; the app passes extra
  // `commands` for quick actions (e.g. «Создать клиента»).
  const paletteCommands: CommandItem[] = React.useMemo(
    () => [
      ...nav.map((item) => ({
        label: item.label,
        href: item.href,
        icon: item.icon,
        group: "Страницы",
      })),
      ...(commands ?? []).map((c) => ({ ...c, group: c.group ?? "Действия" })),
    ],
    [nav, commands],
  );

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-sidebar text-sidebar-foreground lg:flex">
        <Brand brand={brand} />
        <NavLinks nav={nav} pathname={pathname} />
        {plan ? (
          <div className="border-t border-sidebar-border p-3">
            <PlanCapsule {...plan} />
          </div>
        ) : null}
      </aside>

      <div className="lg:pl-64">
        {/* Topbar */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6">
          {/* Mobile menu */}
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-11 lg:hidden"
                aria-label="Меню"
              >
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="left"
              className="flex w-72 flex-col bg-sidebar p-0 text-sidebar-foreground"
            >
              <SheetTitle className="sr-only">Навигация</SheetTitle>
              <Brand brand={brand} />
              <NavLinks nav={nav} pathname={pathname} onNavigate={() => setOpen(false)} />
              {plan ? (
                <div className="border-t border-sidebar-border p-3">
                  <PlanCapsule {...plan} />
                </div>
              ) : null}
            </SheetContent>
          </Sheet>

          {title ? (
            <div className="truncate text-sm font-medium text-muted-foreground">{title}</div>
          ) : null}

          <div className="ml-auto flex items-center gap-2">
            <CommandTrigger onOpen={() => setCmdOpen(true)} />
            {actions}
            <UserMenu user={user} onSignOut={onSignOut} />
          </div>
        </header>

        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>

      <CommandPalette commands={paletteCommands} open={cmdOpen} onOpenChange={setCmdOpen} />
    </div>
  );
}
