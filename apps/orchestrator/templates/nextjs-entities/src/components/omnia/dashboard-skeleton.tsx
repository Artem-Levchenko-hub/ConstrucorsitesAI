import { Skeleton } from "@/components/ui/skeleton";

/**
 * Branded loading silhouette for the dashboard. Render this while data loads
 * instead of `return null` — the screen is ALIVE from the first paint (the
 * brand-tinted shell shimmers) and the layout doesn't jump when real data
 * arrives. Mirrors the default dashboard rhythm: hero band → KPI row → a wide
 * panel beside a side panel. Pure presentation, no data — safe to render
 * before any fetch resolves.
 */
export function DashboardSkeleton() {
  return (
    <div
      data-omnia-skeleton=""
      className="space-y-8"
      aria-busy="true"
      aria-label="Загрузка дашборда"
    >
      {/* hero band */}
      <div className="overflow-hidden rounded-2xl border border-border/60 bg-card p-6 sm:p-8">
        <Skeleton className="h-3 w-24 rounded-full" />
        <Skeleton className="mt-4 h-10 w-3/5 max-w-md" />
        <Skeleton className="mt-3 h-14 w-44" />
        <Skeleton className="mt-6 h-24 w-full rounded-xl" />
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="rounded-xl border border-border/60 bg-card p-5"
          >
            <Skeleton className="h-9 w-9 rounded-lg" />
            <Skeleton className="mt-4 h-3 w-28 rounded-full" />
            <Skeleton className="mt-3 h-7 w-20" />
          </div>
        ))}
      </div>

      {/* panels: a wide list beside a chart card */}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <div className="space-y-3">
          <Skeleton className="h-5 w-40" />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12 w-full rounded-lg" />
          ))}
        </div>
        <div className="space-y-3">
          <Skeleton className="h-5 w-44" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      </div>
    </div>
  );
}
