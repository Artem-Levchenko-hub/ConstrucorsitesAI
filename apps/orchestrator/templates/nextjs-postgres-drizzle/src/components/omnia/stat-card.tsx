/**
 * `<StatCard>` — a single KPI tile for the cabinet dashboard: a label, a big
 * value, an optional trend delta (green up / red down), and a brand-accent icon
 * tile. Pure and server-component-safe — feed it a number you've already
 * queried with Drizzle. Self-contained on Tailwind + the `--brand` token.
 */
import * as React from "react";

export interface StatCardProps {
  label: React.ReactNode;
  value: React.ReactNode;
  /** A lucide icon element, e.g. `<Users />`. Sits in a brand-tinted tile. */
  icon?: React.ReactNode;
  /** Trend delta, e.g. `+12%` or `-3`. `direction` tints it; omit for neutral. */
  delta?: React.ReactNode;
  direction?: "up" | "down" | "flat";
  /** Small caption under the value, e.g. «за 30 дней». */
  hint?: React.ReactNode;
  /** Optional inline chart under the value, e.g. `<Sparkline data={…} />`. */
  chart?: React.ReactNode;
}

export function StatCard({ label, value, icon, delta, direction = "flat", hint, chart }: StatCardProps) {
  const deltaTone =
    direction === "up"
      ? "text-success"
      : direction === "down"
        ? "text-destructive"
        : "text-muted-foreground";
  return (
    <div className="hover-lift elev-1 rounded-2xl border border-border bg-card p-5 transition hover:border-foreground/20">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {icon ? (
          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_82%)] text-[var(--brand)] ring-1 ring-inset ring-border [&_svg]:size-[1.05rem]">
            {icon}
          </span>
        ) : null}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-3xl font-semibold tracking-tight text-foreground tabular-nums">
          {value}
        </span>
        {delta ? (
          <span className={`text-sm font-medium ${deltaTone}`}>{delta}</span>
        ) : null}
      </div>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      {chart ? <div className="mt-3">{chart}</div> : null}
    </div>
  );
}
