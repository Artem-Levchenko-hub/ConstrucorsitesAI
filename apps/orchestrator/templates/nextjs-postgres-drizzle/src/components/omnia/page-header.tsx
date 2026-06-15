/**
 * `<PageHeader>` — the standard cabinet screen heading: an optional quiet
 * eyebrow, a large title, a muted lead, and a right-aligned actions slot. Pure
 * and server-component-safe (no hooks). Self-contained on Tailwind + the
 * `--brand` token, matching the rest of the drizzle `omnia/` cabinet kit.
 */
import * as React from "react";

export interface PageHeaderProps {
  /** Small uppercase label above the title (e.g. «Обзор», a breadcrumb). */
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned controls (buttons, filters). Wraps below the title on
   *  narrow screens. */
  actions?: React.ReactNode;
}

export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-4 pb-8 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="mb-2 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[var(--brand)]">
            <span className="size-1.5 rounded-full bg-[var(--brand)] shadow-[0_0_10px_var(--brand)]" />
            {eyebrow}
          </p>
        ) : null}
        <h1 className="text-balance text-2xl font-semibold tracking-tight text-white sm:text-3xl">
          {title}
        </h1>
        {description ? (
          <p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-zinc-400">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
