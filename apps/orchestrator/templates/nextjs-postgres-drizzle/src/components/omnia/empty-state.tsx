/**
 * `<EmptyState>` — the premium zero-state for a cabinet region with no data
 * yet: a brand-tinted icon halo, a title, a short body, and an optional CTA.
 * Pure and server-component-safe. The canonical "your data will appear here"
 * panel — honest for a freshly provisioned project, never a sad blank box.
 * Self-contained on Tailwind + the `--brand` token.
 */
import * as React from "react";

export interface EmptyStateProps {
  /** A lucide icon element, e.g. `<Inbox />`. */
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** A CTA node (a <Link>/<a>/<button>). Rendered below the body. */
  action?: React.ReactNode;
  /** Drop the dashed border + padding when nested inside another card. */
  bare?: boolean;
}

export function EmptyState({ icon, title, description, action, bare }: EmptyStateProps) {
  return (
    <div
      className={[
        "flex flex-col items-center justify-center px-6 py-14 text-center",
        bare ? "" : "rounded-2xl border border-dashed border-white/12 bg-white/[0.02]",
      ].join(" ")}
    >
      {icon ? (
        <span className="relative mb-5 grid size-14 place-items-center rounded-2xl bg-[color-mix(in_oklab,var(--brand),transparent_84%)] text-[var(--brand)] ring-1 ring-inset ring-white/10 [&_svg]:size-6">
          <span
            aria-hidden
            className="pointer-events-none absolute inset-0 -z-10 rounded-2xl bg-[var(--brand)] opacity-20 blur-2xl"
          />
          {icon}
        </span>
      ) : null}
      <h3 className="text-base font-semibold tracking-tight text-white">{title}</h3>
      {description ? (
        <p className="mt-2 max-w-sm text-pretty text-sm leading-relaxed text-zinc-400">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}
