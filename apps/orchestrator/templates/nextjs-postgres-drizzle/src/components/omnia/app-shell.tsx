"use client";

/**
 * `<AppShell>` — the premium signed-in cabinet frame for a fullstack project.
 *
 * Self-contained on purpose: the drizzle template ships NO `@/components/ui/*`
 * shadcn primitives, so this shell is built from Tailwind + lucide alone and
 * wears the project's colour through `share.accent` (pinned as `--brand` /
 * `--brand-fg` by `brandTokens`) — exactly like the default landing and the
 * split-screen auth chrome it sits beside. One accent, one recipe.
 *
 * Theme-adaptive: the whole shell is token-driven (`bg-background`, `bg-sidebar`,
 * `text-foreground`, …), so a light niche (bakery, clinic, school) gets a clean
 * light cabinet and a luxe / fintech niche the near-black one — instead of every
 * app being locked dark. The theme follows the OS (no-flash init in layout.tsx)
 * and the topbar carries a Sun/Moon toggle that persists the user's choice. A
 * branded sidebar (workspace glyph + grouped nav with an active accent bar) wraps
 * a topbar (page title + theme toggle + user menu) over the work canvas — the
 * Linear / Notion enterprise cabinet pattern. Mobile collapses the sidebar into a
 * slide-over drawer.
 *
 * `"use client"` for the active-route highlight (`usePathname`), the mobile
 * drawer toggle and the theme switch; it takes its data via props, so a server
 * page can fetch the user with `getCurrentUser()` and hand it straight down.
 */
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu, Moon, Sparkles, Sun, X } from "lucide-react";

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

/** Light/dark toggle — flips `<html class="dark">` and persists the choice, so
 *  the cabinet honours both the OS default and an explicit per-user override
 *  (the no-flash init in layout.tsx reads `localStorage.theme`). */
function ThemeToggle() {
  const [dark, setDark] = React.useState<boolean | null>(null);
  React.useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* private mode — the in-page toggle still works for the session */
    }
    setDark(next);
  }
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Светлая тема" : "Тёмная тема"}
      title={dark ? "Светлая тема" : "Тёмная тема"}
      className="grid size-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground"
    >
      {/* Pre-hydration: render nothing decisive to avoid a mismatch flash. */}
      {dark === null ? (
        <Sun className="size-[1.05rem] opacity-0" />
      ) : dark ? (
        <Sun className="size-[1.05rem]" />
      ) : (
        <Moon className="size-[1.05rem]" />
      )}
    </button>
  );
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
              <p className="px-3 pb-1 pt-5 text-[0.65rem] font-semibold uppercase tracking-widest text-muted-foreground first:pt-1">
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
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
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
                    active ? "text-[var(--brand)]" : "text-muted-foreground group-hover:text-foreground",
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
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_78%)] text-[var(--brand)] ring-1 ring-inset ring-border">
          <Sparkles className="size-5" />
        </span>
        <span className="truncate text-[0.95rem] font-semibold tracking-tight text-foreground">
          {brand}
        </span>
      </div>

      {/* Nav */}
      <div className="flex-1 overflow-y-auto px-3 pb-4">
        <NavList nav={nav} pathname={pathname} onNavigate={onNavigate} />
      </div>

      {/* User footer */}
      {user ? (
        <div className="border-t border-sidebar-border p-3">
          <div className="flex items-center gap-3 rounded-lg px-2 py-2">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-[color-mix(in_oklab,var(--brand),transparent_82%)] text-xs font-semibold text-[var(--brand)] ring-1 ring-inset ring-border">
              {initials(user.name || user.email || "?")}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">
                {user.name || user.email}
              </p>
              {user.name && user.email ? (
                <p className="truncate text-xs text-muted-foreground">{user.email}</p>
              ) : null}
            </div>
            <Link
              href="/signout"
              aria-label="Выйти"
              className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition hover:bg-sidebar-accent hover:text-foreground"
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
      className="omnia-app-canvas min-h-screen bg-background text-foreground"
      style={brandTokens(accent)}
    >
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-sidebar-border bg-sidebar lg:block">
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
          <div className="absolute inset-y-0 left-0 w-72 max-w-[82vw] border-r border-sidebar-border bg-sidebar shadow-2xl">
            <button
              type="button"
              aria-label="Закрыть меню"
              onClick={() => setDrawerOpen(false)}
              className="absolute right-3 top-4 grid size-8 place-items-center rounded-lg text-muted-foreground transition hover:bg-sidebar-accent hover:text-foreground"
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
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6">
          <button
            type="button"
            aria-label="Открыть меню"
            onClick={() => setDrawerOpen(true)}
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground lg:hidden"
          >
            <Menu className="size-5" />
          </button>
          {title ? (
            <div className="min-w-0 truncate text-sm font-medium text-muted-foreground">
              {title}
            </div>
          ) : null}
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            {actions ? <>{actions}</> : null}
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
