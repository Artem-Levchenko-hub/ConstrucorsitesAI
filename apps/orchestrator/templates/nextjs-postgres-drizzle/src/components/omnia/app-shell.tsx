"use client";

/**
 * `<AppShell>` — the premium signed-in cabinet frame for a fullstack project.
 *
 * Self-contained on purpose: the drizzle template ships NO shadcn token system
 * and NO `@/components/ui/*` primitives, so this shell is built from Tailwind +
 * lucide alone and wears the project's colour through `share.accent` (pinned as
 * `--brand` / `--brand-fg` by `brandTokens`) — exactly like the default landing
 * and the split-screen auth chrome it sits beside. One accent, one recipe.
 *
 * Dark by default to match the app `<body className="bg-zinc-950">`: a branded
 * sidebar (workspace glyph + grouped nav with an active accent bar) wraps a
 * topbar (page title + user menu) over a near-black work canvas — the Linear /
 * Notion enterprise cabinet pattern. Mobile collapses the sidebar into a
 * slide-over drawer.
 *
 * `"use client"` only for the active-route highlight (`usePathname`) and the
 * mobile drawer toggle; it takes its data via props, so a server page can fetch
 * the user with `getCurrentUser()` and hand it straight down.
 */
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, Sparkles, X } from "lucide-react";

import { brandTokens } from "@/lib/brand";

export interface NavItem {
  label: string;
  href: string;
  /** A lucide icon element, e.g. `<LayoutDashboard />`. */
  icon?: React.ReactNode;
  /** Optional quiet uppercase section heading. Consecutive items sharing a
   *  `section` render under one label — the grouped-nav pattern (Linear,
   *  Stripe). Omit it everywhere and the nav stays a flat list. */
  section?: string;
}

export interface AppShellUser {
  name?: string | null;
  email?: string | null;
}

export interface AppShellProps {
  /** Workspace / app name shown beside the glyph at the top of the sidebar. */
  brand: string;
  /** Project accent (hex). Drives the whole shell — glyph, active bar, focus. */
  accent: string;
  nav: NavItem[];
  user?: AppShellUser | null;
  /** Topbar title for the current page (a <PageHeader> in the content usually
   *  carries the real heading; this is the small breadcrumb-level label). */
  title?: React.ReactNode;
  /** Topbar right-side controls (e.g. a "Создать" button). */
  actions?: React.ReactNode;
  children: React.ReactNode;
}

function initials(value: string): string {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

/** Renders the grouped nav list. Consecutive items with the same `section`
 *  sit under one quiet uppercase label. */
function NavList({
  nav,
  pathname,
  onNavigate,
}: {
  nav: NavItem[];
  pathname: string;
  onNavigate?: () => void;
}) {
  let lastSection: string | undefined;
  return (
    <nav className="flex flex-col gap-0.5">
      {nav.map((item) => {
        const active = isActive(pathname, item.href);
        const showSection = item.section && item.section !== lastSection;
        lastSection = item.section;
        return (
          <React.Fragment key={item.href}>
            {showSection ? (
              <p className="px-3 pb-1 pt-5 text-[0.65rem] font-semibold uppercase tracking-widest text-zinc-500 first:pt-1">
                {item.section}
              </p>
            ) : null}
            <Link
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={[
                "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                active
                  ? "bg-white/[0.06] text-white"
                  : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100",
              ].join(" ")}
            >
              <span
                aria-hidden
                className={[
                  "absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-[var(--brand)] transition-opacity",
                  active ? "opacity-100" : "opacity-0",
                ].join(" ")}
              />
              {item.icon ? (
                <span
                  className={[
                    "[&_svg]:size-[1.05rem] transition-colors",
                    active ? "text-[var(--brand)]" : "text-zinc-500 group-hover:text-zinc-300",
                  ].join(" ")}
                >
                  {item.icon}
                </span>
              ) : null}
              <span className="truncate">{item.label}</span>
            </Link>
          </React.Fragment>
        );
      })}
    </nav>
  );
}

function SidebarInner({
  brand,
  nav,
  user,
  pathname,
  onNavigate,
}: {
  brand: string;
  nav: NavItem[];
  user?: AppShellUser | null;
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 py-5">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_78%)] text-[var(--brand)] ring-1 ring-inset ring-white/10">
          <Sparkles className="size-5" />
        </span>
        <span className="truncate text-[0.95rem] font-semibold tracking-tight text-white">
          {brand}
        </span>
      </div>

      {/* Nav */}
      <div className="flex-1 overflow-y-auto px-3 pb-4">
        <NavList nav={nav} pathname={pathname} onNavigate={onNavigate} />
      </div>

      {/* User footer */}
      {user ? (
        <div className="border-t border-white/5 p-3">
          <div className="flex items-center gap-3 rounded-lg px-2 py-2">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-[color-mix(in_oklab,var(--brand),transparent_82%)] text-xs font-semibold text-[var(--brand)] ring-1 ring-inset ring-white/10">
              {initials(user.name || user.email || "?")}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-zinc-100">
                {user.name || user.email}
              </p>
              {user.name && user.email ? (
                <p className="truncate text-xs text-zinc-500">{user.email}</p>
              ) : null}
            </div>
            <Link
              href="/signout"
              aria-label="Выйти"
              className="grid size-8 shrink-0 place-items-center rounded-lg text-zinc-500 transition hover:bg-white/5 hover:text-zinc-200"
            >
              <LogOut className="size-4" />
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function AppShell({
  brand,
  accent,
  nav,
  user,
  title,
  actions,
  children,
}: AppShellProps) {
  const pathname = usePathname() ?? "/";
  const [drawerOpen, setDrawerOpen] = React.useState(false);

  // Close the mobile drawer on route change.
  React.useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  return (
    <div
      className="min-h-screen bg-zinc-950 text-zinc-100"
      style={brandTokens(accent)}
    >
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-white/5 bg-zinc-900/40 backdrop-blur-sm lg:block">
        <SidebarInner brand={brand} nav={nav} user={user} pathname={pathname} />
      </aside>

      {/* Mobile drawer */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
          <button
            type="button"
            aria-label="Закрыть меню"
            onClick={() => setDrawerOpen(false)}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
          />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[82vw] border-r border-white/10 bg-zinc-900 shadow-2xl">
            <button
              type="button"
              aria-label="Закрыть меню"
              onClick={() => setDrawerOpen(false)}
              className="absolute right-3 top-4 grid size-8 place-items-center rounded-lg text-zinc-400 transition hover:bg-white/5 hover:text-white"
            >
              <X className="size-4" />
            </button>
            <SidebarInner
              brand={brand}
              nav={nav}
              user={user}
              pathname={pathname}
              onNavigate={() => setDrawerOpen(false)}
            />
          </div>
        </div>
      ) : null}

      {/* Main column */}
      <div className="lg:pl-64">
        {/* Topbar */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-white/5 bg-zinc-950/80 px-4 backdrop-blur-md sm:px-6">
          <button
            type="button"
            aria-label="Открыть меню"
            onClick={() => setDrawerOpen(true)}
            className="grid size-9 place-items-center rounded-lg text-zinc-400 transition hover:bg-white/5 hover:text-white lg:hidden"
          >
            <Menu className="size-5" />
          </button>
          {title ? (
            <div className="min-w-0 truncate text-sm font-medium text-zinc-300">
              {title}
            </div>
          ) : null}
          {actions ? (
            <div className="ml-auto flex items-center gap-2">{actions}</div>
          ) : null}
        </header>

        <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
