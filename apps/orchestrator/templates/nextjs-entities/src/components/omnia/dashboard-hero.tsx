import * as React from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";

export interface HeroStat {
  label: string;
  value: React.ReactNode;
  /** Optional lucide icon shown muted before the value. */
  icon?: React.ReactNode;
}

export interface DashboardHeroProps {
  /** Small uppercase context label above the title, e.g. «Обзор» or a greeting. */
  eyebrow?: React.ReactNode;
  /** The screen title. Rendered as the page's single <h1>, so use <DashboardHero>
   *  INSTEAD of <PageHeader> on the dashboard — never both (one h1 per screen). */
  title: React.ReactNode;
  /** Caption under the title (one line of context). */
  description?: React.ReactNode;
  /** Label for the dominant metric, e.g. «Выручка за месяц». */
  metricLabel: string;
  /** The dominant metric itself — the screen's one focal number. Wrap it in
   *  <CountUp> for the living-dashboard roll, e.g. `<CountUp value={total} suffix=" ₽" />`. */
  metric: React.ReactNode;
  /** Delta pill next to the metric, e.g. `{ value: "+12%", positive: true }`. */
  trend?: { value: string; positive?: boolean };
  /** A wide chart for the dominant metric, e.g. `<TrendArea data={…} />`. Sits on
   *  the right on desktop, under the metric on mobile. */
  chart?: React.ReactNode;
  /** Up to ~3 supporting stats shown as a compact strip beneath the band. Keep the
   *  emphasis on the single dominant metric — these stay quiet. */
  stats?: HeroStat[];
  /** Right-aligned controls in the header row (e.g. a period switch or «Создать»). */
  actions?: React.ReactNode;
  className?: string;
}

function TrendPill({ value, positive }: { value: string; positive?: boolean }) {
  const down = positive === false;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        down ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success",
      )}
    >
      {down ? <TrendingDown className="size-3.5" /> : <TrendingUp className="size-3.5" />}
      {value}
    </span>
  );
}

/**
 * The dashboard's hero band — the confident anchor an enterprise overview opens
 * with (Linear, Vercel, Stripe Dashboard), not a flat row of equal cards. It
 * gives the screen a clear dominant: one focal metric set large, a single accent
 * glow for depth, optional inline trend, and a quiet strip of supporting stats.
 *
 * Use it at the TOP of the dashboard in place of <PageHeader> (it renders the
 * <h1>), then follow with the secondary <StatCard> grid and the recent-records
 * table:
 *
 *   <DashboardHero
 *     eyebrow="Обзор" title="Дашборд"
 *     metricLabel="Выручка за месяц"
 *     metric={<CountUp value={revenue} suffix=" ₽" />}
 *     trend={{ value: "+12%", positive: true }}
 *     chart={<TrendArea data={byDay} className="text-chart-1" />}
 *     stats={[
 *       { label: "Клиентов", value: <CountUp value={clients.length} /> },
 *       { label: "Открытых сделок", value: open.length },
 *     ]}
 *   />
 */
export function DashboardHero({
  eyebrow,
  title,
  description,
  metricLabel,
  metric,
  trend,
  chart,
  stats,
  actions,
  className,
}: DashboardHeroProps) {
  const supporting = (stats ?? []).slice(0, 3);
  return (
    <section
      className={cn(
        "fade-up elev-1 relative mb-6 overflow-hidden rounded-2xl border border-border bg-card",
        className,
      )}
    >
      {/* Single accent: a soft primary glow for depth — restraint, not chrome. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-20 -top-28 size-72 rounded-full bg-primary/10 blur-3xl"
      />
      <div className="relative p-6 sm:p-8">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            {eyebrow ? (
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {eyebrow}
              </p>
            ) : null}
            <h1 className="text-2xl font-semibold leading-tight tracking-tight text-balance sm:text-3xl">
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

        <div className="mt-6 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          {/* Dominant metric — the screen's one focal number, set large. */}
          <div className="min-w-0">
            <p className="text-sm font-medium text-muted-foreground">{metricLabel}</p>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-4xl font-semibold tracking-tight tabular-nums text-foreground sm:text-5xl">
                {metric}
              </span>
              {trend ? <TrendPill {...trend} /> : null}
            </div>
          </div>

          {/* Wide trend for the dominant metric. */}
          {chart ? (
            <div className="w-full min-w-0 lg:max-w-md lg:flex-1">{chart}</div>
          ) : null}
        </div>

        {supporting.length ? (
          <div className="mt-6 flex flex-wrap gap-x-8 gap-y-3 border-t border-border pt-5">
            {supporting.map((s, i) => (
              <div key={i} className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {s.label}
                </p>
                <p className="mt-0.5 flex items-center gap-1.5 text-lg font-semibold tracking-tight tabular-nums [&_svg]:size-4 [&_svg]:text-muted-foreground">
                  {s.icon}
                  {s.value}
                </p>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
