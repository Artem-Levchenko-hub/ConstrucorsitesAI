"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu } from "lucide-react";

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
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors [&_svg]:size-4 [&_svg]:shrink-0",
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
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

/**
 * Responsive application shell — persistent sidebar on desktop (lg+), a slide-in
 * Sheet drawer on mobile, plus a sticky topbar. Wrap every page's content with
 * it so the app reads like a real product, not a single scrolling page.
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
  children,
}: AppShellProps) {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-sidebar text-sidebar-foreground lg:flex">
        <Brand brand={brand} />
        <NavLinks nav={nav} pathname={pathname} />
      </aside>

      <div className="lg:pl-64">
        {/* Topbar */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6">
          {/* Mobile menu */}
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Меню">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 bg-sidebar p-0 text-sidebar-foreground">
              <SheetTitle className="sr-only">Навигация</SheetTitle>
              <Brand brand={brand} />
              <NavLinks nav={nav} pathname={pathname} onNavigate={() => setOpen(false)} />
            </SheetContent>
          </Sheet>

          {title ? (
            <div className="truncate text-sm font-medium text-muted-foreground">{title}</div>
          ) : null}

          <div className="ml-auto flex items-center gap-2">
            {actions}
            <UserMenu user={user} onSignOut={onSignOut} />
          </div>
        </header>

        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
