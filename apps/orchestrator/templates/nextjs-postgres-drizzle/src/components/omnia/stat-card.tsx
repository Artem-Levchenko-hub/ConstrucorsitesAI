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
      ? "text-emerald-400"
      : direction === "down"
        ? "text-rose-400"
        : "text-zinc-400";
  return (
    <div className="hover-lift rounded-2xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-sm transition hover:border-white/20 hover:bg-white/[0.05]">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-zinc-400">{label}</p>
        {icon ? (
          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[color-mix(in_oklab,var(--brand),transparent_82%)] text-[var(--brand)] ring-1 ring-inset ring-white/10 [&_svg]:size-[1.05rem]">
            {icon}
          </span>
        ) : null}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-3xl font-semibold tracking-tight text-white tabular-nums">
          {value}
        </span>
        {delta ? (
          <span className={`text-sm font-medium ${deltaTone}`}>{delta}</span>
        ) : null}
      </div>
      {hint ? <p className="mt-1 text-xs text-zinc-500">{hint}</p> : null}
      {chart ? <div className="mt-3">{chart}</div> : null}
    </div>
  );
}
