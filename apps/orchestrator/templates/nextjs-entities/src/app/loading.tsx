import { Skeleton } from "@/components/ui/skeleton";

/**
 * Route-level loading fallback. Next.js renders this instantly on every
 * navigation while the target route's server component streams — so a click
 * never lands on a frozen/blank screen, the app feels alive from the first
 * frame. Nested segments (e.g. a generated `(app)/loading.tsx`) override it;
 * this root fallback covers everything that doesn't ship its own.
 *
 * Generic on purpose — it stands in for any page (landing, auth, cabinet), so
 * it draws a neutral header bar + a responsive content silhouette rather than a
 * dashboard-specific shape. Pure presentation, no data. Mobile-first.
 */
export default function Loading() {
  return (
    <div
      data-omnia-skeleton=""
      aria-busy="true"
      aria-label="Загрузка"
      className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8"
    >
      {/* header bar */}
      <div className="flex items-center justify-between gap-4">
        <div className="space-y-2">
          <Skeleton className="h-3 w-24 rounded-full" />
          <Skeleton className="h-8 w-48 max-w-[60vw]" />
        </div>
        <Skeleton className="h-10 w-28 rounded-lg" />
      </div>

      {/* responsive card row — 1 col on phones, more as it widens */}
      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-xl border border-border/60 bg-card p-5">
            <Skeleton className="h-9 w-9 rounded-lg" />
            <Skeleton className="mt-4 h-3 w-28 rounded-full" />
            <Skeleton className="mt-3 h-7 w-20" />
          </div>
        ))}
      </div>

      {/* content rows */}
      <div className="mt-8 space-y-3">
        <Skeleton className="h-5 w-40" />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}
