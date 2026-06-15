import * as React from "react";

import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  /** Small uppercase label above the title (section / context), e.g. “Обзор”.
   *  Gives the title hierarchy weight without extra size — type doing graphic
   *  work, restrained. Optional. */
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned controls (e.g. a “Создать” button). */
  actions?: React.ReactNode;
  className?: string;
}

/** Consistent page heading: optional eyebrow + display title + description on
 *  the left, actions on the right; stacks on mobile. The title carries real
 *  hierarchy weight (display size, tight tracking, balanced wrap) so each screen
 *  has a confident anchor, not flat body text. Put one at the top of every app
 *  screen. */
export function PageHeader({ eyebrow, title, description, actions, className }: PageHeaderProps) {
  return (
    <div
      className={cn(
        "fade-up mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
    >
      <div className="space-y-1">
        {eyebrow ? (
          <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <span aria-hidden className="size-1.5 rounded-full bg-primary" />
            {eyebrow}
          </p>
        ) : null}
        <h1 className="omnia-display text-2xl font-semibold leading-tight text-balance sm:text-3xl">
          {title}
        </h1>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
