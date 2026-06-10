import * as React from "react";
import Link from "next/link";
import { Inbox } from "lucide-react";

import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";

/** A call-to-action. JSX (`<Button>`/`<Link>`) is canonical, but the generator
 *  frequently reaches for a `{ label, href }` shorthand — we accept it too and
 *  render it as a styled link, so a dashboard never crashes on "Objects are not
 *  valid as a React child" (tolerant reader, R-10). */
export type EmptyStateAction = React.ReactNode | { label: string; href: string };

function isActionLink(a: unknown): a is { label: string; href: string } {
  return (
    typeof a === "object" &&
    a !== null &&
    "label" in a &&
    "href" in a &&
    typeof (a as { label: unknown }).label === "string" &&
    typeof (a as { href: unknown }).href === "string"
  );
}

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: EmptyStateAction;
  className?: string;
}

/** Friendly placeholder for empty lists / no-results. Every list should show
 *  one instead of a blank area. */
export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  const actionNode = isActionLink(action) ? (
    <Link href={action.href} className={buttonVariants()}>
      {action.label}
    </Link>
  ) : (
    action
  );
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
      {actionNode ? <div className="mt-1">{actionNode}</div> : null}
    </div>
  );
}
