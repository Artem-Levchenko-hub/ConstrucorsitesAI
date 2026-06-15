/**
 * `<ScreenFrame>` — the confident header plinth every non-dashboard cabinet
 * screen opens with, so the app stays coherent past the first click (Linear /
 * Stripe / Vercel keep a real header on sub-pages, not flat body text). It is the
 * calm sibling of `<DashboardHero>`: the same family (rounded `--brand`-tinted
 * band + `omnia-display` title + glowing eyebrow + staggered entrance) at a
 * lighter weight, with the depth tuned per archetype via `.omnia-screen-art`.
 *
 * Pure and server-component-safe (no hooks), self-contained on Tailwind + the
 * `--brand` token — matching the rest of the drizzle `omnia/` cabinet kit.
 */
import * as React from "react";

export type ScreenFrameVariant = "list" | "settings" | "detail";

export interface ScreenFrameProps {
  /** Small uppercase label above the title (e.g. «Каталог», a breadcrumb). */
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned controls (buttons, filters). Wraps below on narrow screens. */
  actions?: React.ReactNode;
  /** Archetype → depth treatment. `list` (default) = a quiet top wash; `settings`
   *  = no wash, a left brand rail; `detail` = a soft corner/bottom tint. */
  variant?: ScreenFrameVariant;
  /** Extra content under the header row, inside the plinth. Optional. */
  children?: React.ReactNode;
  className?: string;
}

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
      className={`fade-up elev-1 relative mb-8 overflow-hidden rounded-2xl border border-border bg-card p-5 sm:p-6 ${
        variant === "settings" ? "border-l-[3px] border-l-[var(--brand)] " : ""
      }${className ?? ""}`}
    >
      <div aria-hidden data-frame={variant} className="omnia-screen-art" />
      <div className="stagger relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          {eyebrow ? (
            <p className="mb-2 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--brand)]">
              <span className="size-1.5 rounded-full bg-[var(--brand)] shadow-[0_0_10px_var(--brand)]" />
              {eyebrow}
            </p>
          ) : null}
          <h1 className="omnia-display text-balance text-2xl font-semibold leading-tight text-foreground sm:text-3xl">
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
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
