"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";

/* Settings / Account scaffold — the enterprise pattern every app with a user
 * profile needs (Mobbin refs: Grammarly, 7shifts, Oyster, Time2book). Three
 * deep, narrow-interface primitives (R-01) built on the existing ui kit (R-04):
 *
 *   <SettingsShell nav={SETTINGS_NAV} title="Настройки">
 *     <SettingsSection title="Профиль" description="..."
 *       footer={<Button>Сохранить</Button>}>
 *       <FieldGrid>
 *         <FieldRow label="Имя" htmlFor="first"><Input id="first" /></FieldRow>
 *         <FieldRow label="Фамилия" htmlFor="last"><Input id="last" /></FieldRow>
 *       </FieldGrid>
 *     </SettingsSection>
 *     <DangerZone>
 *       <FieldRow label="Удалить аккаунт" description="...">
 *         <Button variant="destructive">Удалить</Button>
 *       </FieldRow>
 *     </DangerZone>
 *   </SettingsShell>
 */

export interface SettingsNavItem {
  label: string;
  href: string;
  /** A lucide icon element, e.g. `<User />`. Optional. */
  icon?: React.ReactNode;
}

export interface SettingsShellProps {
  /** Sub-navigation between settings pages (Профиль / Безопасность / Биллинг /
   *  Команда). Omit for a single-page settings screen — then only `children`
   *  render. */
  nav?: SettingsNavItem[];
  /** Heading shown above the sub-nav + content (e.g. “Настройки”). Optional —
   *  a <PageHeader> in `children` can do this instead. */
  title?: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

function SettingsNav({ nav, pathname }: { nav: SettingsNavItem[]; pathname: string }) {
  return (
    /* Vertical rail on desktop; horizontal scroll strip on mobile (no h-scroll
     * of the page itself — the strip scrolls, the page doesn't). */
    <nav
      aria-label="Разделы настроек"
      className="flex gap-1 overflow-x-auto pb-1 lg:w-56 lg:shrink-0 lg:flex-col lg:overflow-visible lg:pb-0"
    >
      {nav.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-all [&_svg]:size-4 [&_svg]:shrink-0",
              active
                ? "bg-secondary text-secondary-foreground shadow-sm [&_svg]:text-primary"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
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

/** Layout for a settings area: optional heading, a sub-nav rail (left on lg+,
 *  scrollable strip on mobile) and a stacked content column for the section
 *  cards. */
export function SettingsShell({
  nav,
  title,
  description,
  children,
  className,
}: SettingsShellProps) {
  const pathname = usePathname() ?? "/";
  return (
    <div className={cn("space-y-6", className)}>
      {title || description ? (
        <div className="space-y-1">
          {title ? (
            <h1 className="text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
              {title}
            </h1>
          ) : null}
          {description ? (
            <p className="text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-col gap-6 lg:flex-row">
        {nav && nav.length > 0 ? <SettingsNav nav={nav} pathname={pathname} /> : null}
        <div className="min-w-0 flex-1 space-y-6">{children}</div>
      </div>
    </div>
  );
}

export interface SettingsSectionProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Sticky bottom row inside the card — put the explicit “Сохранить” button
   *  here (Mobbin: no auto-save ambiguity, one clear submit). */
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

/** A grouped settings card: title + description header, body of `FieldRow`s,
 *  and an optional footer holding the explicit save action. Groups related
 *  settings the way Grammarly / Oyster cards do. */
export function SettingsSection({
  title,
  description,
  footer,
  children,
  className,
}: SettingsSectionProps) {
  return (
    <Card className={cn("gap-0 overflow-hidden py-0", className)}>
      <div className="space-y-1 border-b border-border px-5 py-4 sm:px-6">
        <h2 className="text-base font-semibold tracking-tight">{title}</h2>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <div className="divide-y divide-border px-5 sm:px-6">{children}</div>
      {footer ? (
        <div className="flex items-center justify-end gap-2 border-t border-border bg-muted/30 px-5 py-3 sm:px-6">
          {footer}
        </div>
      ) : null}
    </Card>
  );
}

export interface FieldRowProps {
  label: React.ReactNode;
  /** Helper text under the label (left column). */
  description?: React.ReactNode;
  /** Ties the label to the control for a11y (Inclusive Components) — pass the
   *  same id as the input. */
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
}

/** One settings field: label + helper on the left, control on the right;
 *  stacks on mobile. The atomic row inside a `SettingsSection`. */
export function FieldRow({ label, description, htmlFor, children, className }: FieldRowProps) {
  return (
    <div
      className={cn(
        "grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,28rem)] sm:items-start sm:gap-6",
        className,
      )}
    >
      <div className="space-y-0.5">
        <Label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
        </Label>
        {description ? (
          <p className="text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <div className="min-w-0 sm:justify-self-stretch">{children}</div>
    </div>
  );
}

export interface FieldGridProps {
  children: React.ReactNode;
  className?: string;
}

/** Two-column field group for paired inputs (Имя / Фамилия) — one column on
 *  mobile, two on sm+ (Mobbin: 7shifts/Oyster profile forms). Drop a couple of
 *  bare label+input pairs inside. */
export function FieldGrid({ children, className }: FieldGridProps) {
  return (
    <div className={cn("grid gap-4 py-4 sm:grid-cols-2", className)}>{children}</div>
  );
}

export interface DangerZoneProps {
  /** Defaults to “Опасная зона”. */
  title?: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

/** Destructive actions (delete account / data), visually isolated at the bottom
 *  with a destructive-tinted border so they never sit next to routine saves
 *  (Mobbin: settings keep “Delete account” apart). */
export function DangerZone({ title, description, children, className }: DangerZoneProps) {
  return (
    <Card
      className={cn("gap-0 overflow-hidden border-destructive/30 py-0", className)}
    >
      <div className="space-y-1 border-b border-destructive/20 bg-destructive/5 px-5 py-4 sm:px-6">
        <h2 className="text-base font-semibold tracking-tight text-destructive">
          {title ?? "Опасная зона"}
        </h2>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <div className="divide-y divide-border px-5 sm:px-6">{children}</div>
    </Card>
  );
}
