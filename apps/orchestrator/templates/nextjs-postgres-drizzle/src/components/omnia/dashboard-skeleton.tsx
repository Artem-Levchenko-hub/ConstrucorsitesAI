/**
 * `<Skeleton>` + `<DashboardSkeleton>` — the brand-tinted loading state for the
 * fullstack cabinet.
 *
 * Render these while a data region loads (a route `loading.tsx`, a Suspense
 * fallback, or a client `loading ? <DashboardSkeleton/> : …`) instead of a
 * `return null`, a blank flash, or a lone spinner. The screen is ALIVE from the
 * first paint — the brand pulse with a sheen sweeping across it — and because
 * the silhouette mirrors the real cabinet rhythm (hero band → KPI row → a wide
 * list panel) the layout never jumps when the rows arrive.
 *
 * Self-contained on purpose, exactly like the rest of the kit: built from
 * Tailwind + the `.omnia-skeleton` shimmer (globals.css) and the project's
 * `--brand` token, no shadcn, no client JS (server-component-safe). Pure
 * presentation — safe to render before any fetch resolves.
 */
import * as React from "react";

/** A single shimmering block. Pass size + rounding via `className`
 *  (`h-4 w-32 rounded-full`); the element's own radius wins and clips the sheen.
 *  Defaults to a small rounded line. */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`omnia-skeleton block h-4 w-full rounded-md ${className}`}
    />
  );
}

/** Full-cabinet loading silhouette: a hero band, a 3-up KPI row and a wide list
 *  panel beside a side panel — the default cabinet rhythm. */
export function DashboardSkeleton() {
  return (
    <div className="space-y-8" aria-busy="true" aria-label="Загрузка кабинета">
      {/* Hero band — mirrors <DashboardHero>: eyebrow, big title, lead, a metric. */}
      <div className="overflow-hidden rounded-2xl border border-border bg-card p-6 elev-1 sm:p-8">
        <Skeleton className="h-3 w-24 rounded-full" />
        <Skeleton className="mt-4 h-9 w-3/5 max-w-md rounded-lg" />
        <Skeleton className="mt-3 h-4 w-4/5 max-w-lg" />
        <Skeleton className="mt-6 h-12 w-40 rounded-xl" />
      </div>

      {/* KPI row — three StatCard silhouettes. */}
      <div className="grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-2xl border border-border bg-card p-5 elev-1">
            <Skeleton className="size-9 rounded-lg" />
            <Skeleton className="mt-4 h-3 w-24 rounded-full" />
            <Skeleton className="mt-3 h-7 w-20 rounded-md" />
          </div>
        ))}
      </div>

      {/* Data region — a wide list panel beside a chart card. */}
      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <div className="rounded-2xl border border-border bg-card p-5 elev-1">
          <Skeleton className="h-5 w-40 rounded-md" />
          <div className="mt-5 space-y-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-11 w-full rounded-lg" />
            ))}
          </div>
        </div>
        <div className="rounded-2xl border border-border bg-card p-5 elev-1">
          <Skeleton className="h-5 w-32 rounded-md" />
          <Skeleton className="mt-5 h-48 w-full rounded-xl" />
        </div>
      </div>
    </div>
  );
}
