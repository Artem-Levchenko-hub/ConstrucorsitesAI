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
  className?: string;
}

/** KPI tile for dashboards. A row of these is the canonical dashboard top. */
export function StatCard({ label, value, icon, hint, trend, className }: StatCardProps) {
  return (
    <Card className={cn("gap-0 p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {icon ? (
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground [&_svg]:size-5">
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
    </Card>
  );
}
