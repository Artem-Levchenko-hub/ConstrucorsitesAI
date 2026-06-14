import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Dependency-free chart primitives for dashboards.
 *
 * Pure SVG / CSS — no recharts, chart.js or any runtime package (the app-gen
 * brief forbids chart libraries). Each chart is a presentational server
 * component: it renders `role="img"` with an `aria-label`, and a
 * visually-hidden data list so screen readers get the underlying numbers.
 *
 * Colors come from the kit's `--chart-1..5` tokens via `currentColor`, so they
 * track light/dark mode automatically. Set the hue with a `text-chart-*`
 * class on the chart (default: `text-chart-1`).
 *
 * On first paint each chart draws itself in — the line strokes left to right,
 * the area fills up behind it, bars grow from their baseline, the ring sweeps to
 * its value (the "living dashboard" touch). It's pure CSS (globals.css), so the
 * charts stay server components and motion-averse users get the final frame
 * instantly. Nothing to wire — it just happens wherever a chart renders.
 *
 *   <Sparkline data={[3, 5, 4, 8, 6, 11]} />
 *   <TrendArea data={revenueByDay} className="text-chart-2" />
 *   <BarMini data={[{ label: "Север", value: 42 }, …]} />
 *   <DonutStat value="68%" pct={68} label="Конверсия" />
 */

const SR_ONLY = "sr-only";

function numberList(values: number[]): React.ReactNode {
  return (
    <ul className={SR_ONLY}>
      {values.map((v, i) => (
        <li key={i}>{v}</li>
      ))}
    </ul>
  );
}

/** Map data points into SVG-space coordinates within `[0,width] × [0,height]`. */
function points(data: number[], width: number, height: number, pad = 2) {
  if (data.length === 0) return [] as Array<{ x: number; y: number }>;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const innerH = height - pad * 2;
  const step = data.length > 1 ? width / (data.length - 1) : 0;
  return data.map((v, i) => ({
    x: data.length > 1 ? i * step : width / 2,
    y: pad + innerH - ((v - min) / span) * innerH,
  }));
}

export interface SparklineProps {
  data: number[];
  /** Accessible description, e.g. "Продажи за 7 дней". */
  label?: string;
  width?: number;
  height?: number;
  className?: string;
}

/** Inline trend line — sized to sit next to a metric inside a StatCard. */
export function Sparkline({
  data,
  label,
  width = 96,
  height = 28,
  className,
}: SparklineProps) {
  const pts = points(data, width, height);
  const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  return (
    <span
      role="img"
      aria-label={label ?? "Тренд"}
      className={cn("inline-block align-middle text-chart-1", className)}
    >
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        fill="none"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          className="omnia-chart-draw"
          pathLength={1}
          d={d}
          stroke="currentColor"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {numberList(data)}
    </span>
  );
}

export interface TrendAreaProps {
  data: number[];
  label?: string;
  height?: number;
  className?: string;
}

/** Filled area for a primary dashboard metric. Fills available width. */
export function TrendArea({ data, label, height = 64, className }: TrendAreaProps) {
  const width = 300; // viewBox unit width; scaled to container via CSS
  const pts = points(data, width, height, 4);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  const area = pts.length
    ? `${line} L${width} ${height} L0 ${height} Z`
    : "";
  return (
    <div
      role="img"
      aria-label={label ?? "Динамика"}
      className={cn("w-full text-chart-1", className)}
    >
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        fill="none"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {area ? (
          <path className="omnia-chart-fill" d={area} fill="currentColor" fillOpacity={0.12} />
        ) : null}
        <path
          className="omnia-chart-draw"
          pathLength={1}
          d={line}
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {numberList(data)}
    </div>
  );
}

export interface BarMiniDatum {
  label: string;
  value: number;
}

export interface BarMiniProps {
  data: BarMiniDatum[];
  label?: string;
  className?: string;
}

/** Horizontal bars — a compact breakdown by category. Cycles `--chart-1..5`. */
export function BarMini({ data, label, className }: BarMiniProps) {
  const max = data.length ? Math.max(...data.map((d) => d.value)) || 1 : 1;
  return (
    <div
      role="img"
      aria-label={label ?? "Распределение"}
      className={cn("flex flex-col gap-2.5", className)}
    >
      {data.map((d, i) => (
        <div key={`${d.label}-${i}`} aria-hidden="true" className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate text-muted-foreground">{d.label}</span>
            <span className="shrink-0 font-medium tabular-nums">{d.value}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="omnia-chart-grow h-full rounded-full"
              style={{
                width: `${Math.max(2, (d.value / max) * 100)}%`,
                backgroundColor: `var(--chart-${(i % 5) + 1})`,
                animationDelay: `${i * 0.08}s`,
              }}
            />
          </div>
        </div>
      ))}
      <ul className={SR_ONLY}>
        {data.map((d, i) => (
          <li key={i}>
            {d.label}: {d.value}
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface DonutStatProps {
  /** Big center label, e.g. "68%" or "1 240". */
  value: React.ReactNode;
  /** Filled fraction of the ring, 0–100. */
  pct: number;
  /** Small caption under the value. */
  label?: string;
  size?: number;
  className?: string;
}

/** Single-metric ring built from a CSS conic-gradient (no SVG arcs). */
export function DonutStat({ value, pct, label, size = 112, className }: DonutStatProps) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div
      role="img"
      aria-label={label ? `${label}: ${clamped}%` : `${clamped}%`}
      className={cn("inline-flex flex-col items-center gap-2 text-primary", className)}
    >
      <div
        className="omnia-donut-sweep relative grid place-items-center rounded-full"
        style={
          {
            width: size,
            height: size,
            "--omnia-pct": `${clamped}%`,
            background:
              "conic-gradient(currentColor var(--omnia-pct), var(--muted) var(--omnia-pct) 100%)",
          } as React.CSSProperties
        }
      >
        <div
          className="grid place-items-center rounded-full bg-card text-center"
          style={{ width: size * 0.72, height: size * 0.72 }}
        >
          <span className="text-xl font-semibold tracking-tight tabular-nums text-foreground">
            {value}
          </span>
        </div>
      </div>
      {label ? <span className="text-xs text-muted-foreground">{label}</span> : null}
    </div>
  );
}
