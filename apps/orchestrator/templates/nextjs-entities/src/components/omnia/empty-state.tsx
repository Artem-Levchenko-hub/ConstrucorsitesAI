import * as React from "react";
import { Inbox } from "lucide-react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

/** Friendly placeholder for empty lists / no-results. Every list should show
 *  one instead of a blank area. */
export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border px-6 py-14 text-center",
        className,
      )}
    >
      <span className="flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground [&_svg]:size-5">
        {icon ?? <Inbox />}
      </span>
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        {description ? (
          <p className="mx-auto max-w-sm text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
