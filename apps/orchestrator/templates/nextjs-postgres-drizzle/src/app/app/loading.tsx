/**
 * Route-level loading UI for the signed-in cabinet (`/app`).
 *
 * Next's App Router shows this instantly while the async cabinet page resolves
 * (session lookup + every Drizzle query the AI adds) and on each navigation into
 * the segment — so the cabinet is ALIVE from the first paint instead of a blank
 * flash. The silhouette mirrors <AppShell> (sidebar + topbar) wrapping a
 * <DashboardSkeleton>, so the layout doesn't jump when the real screen arrives.
 *
 * Server-component-safe and self-contained: it pins the project's brand tokens
 * itself (the real <AppShell> does this on its own root, which isn't mounted
 * yet) so the whole shimmer wears the project colour, and follows the OS theme
 * via the no-flash class set in layout.tsx — zero per-app model cost.
 */
import { DashboardSkeleton, Skeleton } from "@/components/omnia";
import { share } from "@/app/omnia-share";
import { brandTokens } from "@/lib/brand";

export default function CabinetLoading() {
  const accent = share.accent || "#6366f1";
  return (
    <div
      className="omnia-app-canvas min-h-screen bg-background text-foreground"
      style={brandTokens(accent)}
    >
      {/* Sidebar silhouette (desktop) — brand glyph + grouped nav lines. */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-sidebar-border bg-sidebar lg:block">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <Skeleton className="size-9 rounded-xl" />
          <Skeleton className="h-4 w-28 rounded-md" />
        </div>
        <div className="space-y-1.5 px-3 pt-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-9 w-full rounded-lg" />
          ))}
        </div>
      </aside>

      {/* Main column. */}
      <div className="lg:pl-64">
        {/* Topbar silhouette. */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6">
          <Skeleton className="h-4 w-28 rounded-md" />
          <div className="ml-auto flex items-center gap-2">
            <Skeleton className="size-9 rounded-lg" />
            <Skeleton className="size-9 rounded-full" />
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
          <DashboardSkeleton />
        </main>
      </div>
    </div>
  );
}
