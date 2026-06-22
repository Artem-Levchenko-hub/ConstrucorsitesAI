import * as React from "react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";

/** A call-to-action. JSX (`<Button>`/`<Link>`) is canonical, but the generator
 *  frequently reaches for a `{ label, href }` shorthand — we accept it too and
 *  render it as a styled link, so a dashboard never crashes on "Objects are not
 *  valid as a React child" (tolerant reader, R-10). */
export type EmptyStateAction = React.ReactNode | { label: string; href: string };

/** Built-in themed line-art illustration. An empty list should read as a
 *  purposeful state (the app analog of the landings' thematic vector drawings —
 *  memory `omnia_graphic_arsenal_v5`), not a gray dashed box. Pass one by name
 *  via `illustration`, or supply a custom node. */
export type EmptyIllustration = "list" | "search" | "records" | "error";

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

const ILLUSTRATION_NAMES: readonly EmptyIllustration[] = ["list", "search", "records", "error"];

function isIllustrationName(v: unknown): v is EmptyIllustration {
  return typeof v === "string" && (ILLUSTRATION_NAMES as readonly string[]).includes(v);
}

/* Each illustration is muted line-art (structure in `--muted-foreground`) with
 * exactly ONE accent element in `--primary` — restraint, not chrome. */
const ART: Record<EmptyIllustration, React.ReactNode> = {
  list: (
    <>
      <rect x="22" y="18" width="76" height="13" rx="4" />
      <rect x="22" y="37" width="76" height="13" rx="4" />
      <rect x="22" y="56" width="46" height="13" rx="4" />
      <g className="text-primary">
        <path d="M84 60l4.5 4.5L98 56" />
      </g>
    </>
  ),
  search: (
    <>
      <rect x="20" y="22" width="58" height="12" rx="4" />
      <rect x="20" y="42" width="40" height="12" rx="4" />
      <g className="text-primary">
        <circle cx="82" cy="50" r="14" />
        <path d="M93 61l10 10" />
      </g>
    </>
  ),
  records: (
    <>
      <rect x="32" y="12" width="52" height="62" rx="7" />
      <path d="M43 30h30M43 42h30M43 54h18" />
      <g className="text-primary">
        <circle cx="84" cy="64" r="13" fill="var(--card)" />
        <path d="M84 58v12M78 64h12" />
      </g>
    </>
  ),
  error: (
    <>
      <circle cx="60" cy="44" r="26" />
      <g className="text-primary">
        <path d="M60 33v17" />
        <circle cx="60" cy="58" r="2" fill="currentColor" stroke="none" />
      </g>
    </>
  ),
};

function EmptyArt({ name }: { name: EmptyIllustration }) {
  return (
    <svg
      viewBox="0 0 120 84"
      width="120"
      height="84"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="text-muted-foreground/55"
    >
      {ART[name]}
    </svg>
  );
}

export interface EmptyStateProps {
  /** A themed built-in (`"list" | "search" | "records" | "error"`) or a custom
   *  illustration node. Takes precedence over `icon`. */
  illustration?: EmptyIllustration | React.ReactNode;
  /** Lucide icon, rendered in a tinted badge. Kept for back-compat; when neither
   *  `illustration` nor `icon` is given, a `list` illustration is shown. */
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: EmptyStateAction;
  className?: string;
}

/** Friendly placeholder for empty lists / no-results. Every list should show
 *  one instead of a blank area. */
export function EmptyState({
  illustration,
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  const actionNode = isActionLink(action) ? (
    <Link href={action.href} className={buttonVariants()}>
      {action.label}
    </Link>
  ) : (
    action
  );

  let visual: React.ReactNode;
  if (isIllustrationName(illustration)) {
    visual = <EmptyArt name={illustration} />;
  } else if (illustration) {
    visual = illustration;
  } else if (icon) {
    visual = (
      <span className="flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground [&_svg]:size-5">
        {icon}
      </span>
    );
  } else {
    visual = <EmptyArt name="list" />;
  }

  return (
    <div
      data-omnia-empty=""
      className={cn(
        "flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-border px-6 py-14 text-center",
        className,
      )}
    >
      {visual}
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
