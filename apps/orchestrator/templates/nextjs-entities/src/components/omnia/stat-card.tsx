import * as React from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

export interface StatCardProps {
  label: string;
  value: React.ReactNode;
  /** A lucide icon element, e.g. `<Users />`. Rendered in a tinted square. */
  icon?: React.ReactNode;
  hint?: string;
  /** Small delta pill, e.g. `{ value: "+12%", positive: true }`. */
  trend?: { value: string; positive?: boolean };
  /** Hairline top accent. Use on the SINGLE primary KPI only (one accent per
   *  screen — Refactoring UI). Off by default. */
  accent?: boolean;
  /** Optional inline chart under the value, e.g. `<Sparkline data={…} />`. */
  chart?: React.ReactNode;
  className?: string;
}

/** KPI tile for dashboards. A row of these is the canonical dashboard top. */
export function StatCard({ label, value, icon, hint, trend, accent, chart, className }: StatCardProps) {
  return (
    <Card
      className={cn(
        "hover-lift gap-0 p-5",
        accent && "border-t-2 border-t-primary/30",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {icon ? (
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary [&_svg]:size-5">
            {icon}
          </span>
        ) : null}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-3xl font-semibold tracking-tight tabular-nums">{value}</span>
        {trend ? (
          <span
            className={cn(
              "inline-flex items-center gap-0.5 text-xs font-medium",
              trend.positive === false ? "text-destructive" : "text-success",
            )}
          >
            {trend.positive === false ? (
              <TrendingDown className="size-3.5" />
            ) : (
              <TrendingUp className="size-3.5" />
            )}
            {trend.value}
          </span>
        ) : null}
      </div>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      {chart ? <div className="mt-3">{chart}</div> : null}
    </Card>
  );
}
