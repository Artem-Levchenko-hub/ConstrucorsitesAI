import * as React from "react";

import { cn } from "@/lib/utils";

export type ScreenFrameVariant = "list" | "settings" | "detail";

export interface ScreenFrameProps {
  /** Small uppercase label above the title (section / context), e.g. «Каталог».
   *  Type doing graphic work, restrained — same eyebrow the dashboard hero uses
   *  so every screen shares one voice. Optional. */
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned controls (e.g. a «Создать» button). Wraps below on mobile. */
  actions?: React.ReactNode;
  /** Archetype → depth treatment. `list` (default) = a quiet top wash, the calm
   *  sibling of the dashboard aurora; `settings` = no wash, a left accent rail
   *  carries it (calm by design); `detail` = a soft corner/bottom tint behind a
   *  record header. */
  variant?: ScreenFrameVariant;
  /** Extra content under the header row, inside the plinth (e.g. a stat strip or
   *  filter bar that should read as part of the same surface). Optional. */
  children?: React.ReactNode;
  className?: string;
}

/**
 * `<ScreenFrame>` — the confident header plinth every non-dashboard screen opens
 * with, so the app stays coherent past the first click (Linear / Stripe / Vercel
 * keep a real header on sub-pages, not flat body text). It is the calm sibling of
 * `<DashboardHero>`: the same family (rounded brand-tinted band + `omnia-display`
 * title + eyebrow + staggered entrance) at a lighter weight, with the depth tuned
 * per archetype via `.omnia-screen-art` — surgical, never uniform wallpaper.
 *
 * Use it (directly or through `<PageHeader>` / `<SettingsShell>`) at the top of
 * list, settings and record screens:
 *
 *   <ScreenFrame eyebrow="Каталог" title="Товары" description="…"
 *     actions={<Button>Создать</Button>} />
 */
export function ScreenFrame({
  eyebrow,
  title,
  description,
  actions,
  variant = "list",
  children,
  className,
}: ScreenFrameProps) {
  return (
    <section
      data-frame={variant}
      className={cn(
        "fade-up relative mb-6 overflow-hidden rounded-2xl border border-border/70 bg-card/50 p-5 sm:p-6",
        // settings stays calm — a single brand rail instead of a wash.
        variant === "settings" && "border-l-[3px] border-l-primary",
        className,
      )}
    >
      <div aria-hidden data-frame={variant} className="omnia-screen-art" />
      <div className="stagger relative flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0 space-y-1">
          {eyebrow ? (
            <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <span aria-hidden className="size-1.5 rounded-full bg-primary" />
              {eyebrow}
            </p>
          ) : null}
          <h1 className="omnia-display text-2xl font-semibold leading-tight text-balance text-foreground sm:text-3xl">
            {title}
          </h1>
          {description ? (
            <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        ) : null}
      </div>
      {children ? <div className="relative mt-5">{children}</div> : null}
    </section>
  );
}
