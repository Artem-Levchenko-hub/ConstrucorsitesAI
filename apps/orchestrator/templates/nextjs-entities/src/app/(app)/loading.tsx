import { Skeleton } from "@/components/ui/skeleton";

/**
 * Cabinet loading fallback. Because it lives inside the `(app)` group it renders
 * INSIDE <AppShell> — so on every in-cabinet navigation the sidebar + topbar
 * stay put and only the content area shows a skeleton (no full-screen flash).
 * Generic page shape (header + card row + rows) covers dashboard and any entity
 * list page. Pure presentation, no data. Mobile-first.
 */
export default function CabinetLoading() {
  return (
    <div data-omnia-skeleton="" aria-busy="true" aria-label="Загрузка" className="space-y-8">
      {/* page header */}
      <div className="space-y-2">
        <Skeleton className="h-3 w-24 rounded-full" />
        <Skeleton className="h-8 w-56 max-w-[70vw]" />
      </div>

      {/* KPI / card row — mobile-first responsive */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-xl border border-border/60 bg-card p-5">
            <Skeleton className="h-9 w-9 rounded-lg" />
            <Skeleton className="mt-4 h-3 w-24 rounded-full" />
            <Skeleton className="mt-3 h-7 w-16" />
          </div>
        ))}
      </div>

      {/* table / list rows */}
      <div className="space-y-3">
        <Skeleton className="h-5 w-44" />
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-lg" />
        ))}
      </div>
    </div>
  );
}
