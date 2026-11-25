"use client";

import { useMemo, useState, type CSSProperties } from "react";

import type { MetricDefinition, MetricPoint } from "@/lib/api/monitoring";
import {
  chartRows,
  chartSeries,
  MIXED_UNIT_METRICS,
  pointUnit,
  splitSamples,
  type ChartStatistic,
} from "@/lib/monitoring-chart";

export const formatNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format;
const compactNumber = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
const timeZone = "Asia/Tomsk";
const dateTime = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone,
});
const hours = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone });
const seconds = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone });
const days = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone });
export const formatDate = (input: string | number) => dateTime.format(new Date(input));

const CHART_WIDTH = 600;
const CHART_HEIGHT = 180;
const TICKS = [0, 0.25, 0.5, 0.75, 1];
const PALETTE = ["var(--series-blue)", "var(--series-teal)", "var(--series-purple)", "var(--series-rose)"];
const STAT_LABELS: Record<ChartStatistic, string> = { value: "Значение", p95: "p95", mean: "Среднее" };

function Trend({ points, statistic, metric, compact, expectedStep }: {
  points: MetricPoint[];
  statistic: ChartStatistic;
  metric: MetricDefinition;
  compact: boolean;
  expectedStep?: number;
}) {
  const series = useMemo(() => chartSeries(points, statistic), [points, statistic]);
  const [selected, setSelected] = useState("");
  const [cursor, setCursor] = useState<number | null>(null);
  const focused = series.find((entry) => entry.label === selected);
  const mixedUnits = MIXED_UNIT_METRICS.has(metric.id);
  const drawn = focused ? [focused] : series.slice(0, mixedUnits ? 1 : compact ? 3 : 4);
  const samples = drawn.flatMap((entry) => entry.samples);
  if (!samples.length) return null;
  const times = [...new Set(samples.map((point) => point.time))].sort((a, b) => a - b);
  const [start, end] = [times[0], times[times.length - 1]];
  const interval = end - start;
  const showSeconds = interval <= 5 * 60_000;
  const tickFormat = showSeconds ? seconds : interval <= 30 * 60 * 60_000 ? hours : days;
  const tickLabels = interval > 0 && interval < 4_000
    ? ["0 с", (interval / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 3 }) + " с"]
    : (interval ? TICKS : [0]).map((tick) => tickFormat.format(start + interval * tick));
  const cursorLabel = (time: number) => showSeconds ? seconds.format(time) : formatDate(time);
  const values = samples.map((sample) => sample.value);
  const lower = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  const upper = maximum === lower ? lower + 1 : maximum;
  const scale = upper - lower;
  const chartUnit = pointUnit(metric.id, metric.unit, drawn[0].label);
  const x = (time: number) => interval ? ((time - start) / interval) * CHART_WIDTH : CHART_WIDTH / 2;
  const y = (value: number) => ((upper - value) / scale) * CHART_HEIGHT;
  const at = cursor === null ? null : times[Math.min(cursor, times.length - 1)];
  const readings = at === null ? [] : drawn.flatMap((entry, index) => {
    const sample = entry.samples.find((point) => point.time === at);
    return sample ? [{ ...sample, label: entry.label, color: PALETTE[index] }] : [];
  });

  function moveCursor(position: number) {
    const relative = start + interval * Math.min(1, Math.max(0, position));
    const index = times.reduce((nearest, time, atIndex) => (
      Math.abs(time - relative) < Math.abs(times[nearest] - relative) ? atIndex : nearest
    ), 0);
    setCursor(index);
  }

  return (
    <figure className="monitor-trend" aria-label={"График · " + metric.title}>
      <div className="monitor-chart-head">
        <span>{chartUnit}</span>
        {series.length > 1 && (
          <select aria-label="Ряд графика" value={focused?.label ?? ""}
            onChange={(event) => { setSelected(event.target.value); setCursor(null); }}>
            <option value="">{mixedUnits ? "Первый ряд" : "Основные ряды"}</option>
            {series.map((entry) => <option key={entry.label}>{entry.label}</option>)}
          </select>
        )}
      </div>
      <div className="monitor-chart-area">
        <div className="monitor-chart-axis" aria-hidden="true">
          {TICKS.map((tick) => compactNumber.format(upper - scale * tick))
            .map((label, index) => <span key={index}>{label}</span>)}
        </div>
        <div className="monitor-plot" tabIndex={0}
          aria-label="График. Стрелки влево и вправо перемещаются по измерениям"
          onPointerMove={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            moveCursor((event.clientX - bounds.left) / bounds.width);
          }}
          onPointerLeave={() => setCursor(null)}
          onBlur={() => setCursor(null)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) return;
            event.preventDefault();
            if (event.key === "Escape") { setCursor(null); return; }
            if (event.key === "Home") { setCursor(0); return; }
            if (event.key === "End") { setCursor(times.length - 1); return; }
            const step = event.key === "ArrowLeft" ? -1 : 1;
            setCursor(Math.min(times.length - 1, Math.max(0, (cursor ?? times.length - 1) + step)));
          }}>
          <svg viewBox={"0 0 " + CHART_WIDTH + " " + CHART_HEIGHT} preserveAspectRatio="none" aria-hidden="true">
            {TICKS.map((tick) => <line className="monitor-grid-line" key={tick} x1="0" x2={CHART_WIDTH} y1={CHART_HEIGHT * tick} y2={CHART_HEIGHT * tick} />)}
            {TICKS.map((tick) => <line className="monitor-grid-line" key={"x" + tick} x1={CHART_WIDTH * tick} x2={CHART_WIDTH * tick} y1="0" y2={CHART_HEIGHT} />)}
            {drawn.map((entry, index) => splitSamples(entry.samples, expectedStep).map((segment) => {
              const locations = segment.map((sample) => x(sample.time) + "," + y(sample.value));
              const color = PALETTE[index];
              if (segment.length === 1) {
                return <circle key={entry.label + segment[0].time} cx={x(segment[0].time)} cy={y(segment[0].value)} r="3" fill={color} />;
              }
              return <g key={entry.label + segment[0].time}>
                {index === 0 && <polygon points={x(segment[0].time) + "," + y(0) + " " + locations.join(" ") + " " + x(segment[segment.length - 1].time) + "," + y(0)} fill={color} opacity=".12" />}
                <polyline points={locations.join(" ")} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
              </g>;
            }))}
            {at !== null && <line className="monitor-crosshair" x1={x(at)} x2={x(at)} y1="0" y2={CHART_HEIGHT} />}
            {readings.map((reading) => <circle key={reading.label} cx={x(reading.time)} cy={y(reading.value)} r="4" fill={reading.color} />)}
          </svg>
          {at !== null && (
            <div className="monitor-tooltip" aria-hidden="true" style={{ left: 100 * x(at) / CHART_WIDTH + "%" }} data-right={x(at) > CHART_WIDTH / 2 || undefined}>
              <strong>{cursorLabel(at)} · Томск</strong>
              {readings.map((reading) => <span key={reading.label}>{reading.label}<b>{formatNumber(reading.value)}</b></span>)}
            </div>
          )}
        </div>
      </div>
      <div className="monitor-ticks" aria-hidden="true">
        {tickLabels.map((label, index) => <span key={index}>{label}</span>)}
      </div>
      <div className="monitor-chart-legend">
        {drawn.map((entry, index) => <span key={entry.label}><i style={{ backgroundColor: PALETTE[index] }} />{entry.label}</span>)}
      </div>
      <span className="monitor-sr" aria-live="polite">
        {at === null ? "" : cursorLabel(at) + " · " + readings.map((reading) => reading.label + " " + formatNumber(reading.value) + " " + chartUnit).join(", ")}
      </span>
    </figure>
  );
}

export function MonitoringChart({ points, metric, compact = false, expectedStep }: {
  points: MetricPoint[];
  metric: MetricDefinition;
  compact?: boolean;
  expectedStep?: number;
}) {
  const [statistic, setStatistic] = useState<ChartStatistic>("value");
  const stats = (["value", "p95", "mean"] as const).filter((field) => field === "value" || points.some((point) => point[field] != null));
  const selected = stats.includes(statistic) ? statistic : "value";
  const timed = points.some((point) => point.at !== null);
  const categories = timed ? [] : chartRows(points, metric.id, metric.unit, selected);
  const rows = categories.slice(0, compact ? 6 : 12);
  const domains = new Map<string, number>();
  for (const row of rows) domains.set(row.unit, Math.max(domains.get(row.unit) ?? 0, Math.abs(row.value)));

  return (
    <div className="monitor-chart-content">
      {stats.length > 1 && (
        <label className="monitor-stat">
          <span>Показатель</span>
          <select value={selected} onChange={(event) => setStatistic(event.target.value as ChartStatistic)}>
            {stats.map((field) => <option key={field} value={field}>{field === "value" ? "Медиана" : STAT_LABELS[field]}</option>)}
          </select>
        </label>
      )}
      {timed ? <Trend points={points} statistic={selected} metric={metric} compact={compact} expectedStep={expectedStep} />
        : <figure className="monitor-bars" aria-label={"Разбивка · " + metric.title}>
          {categories.length > rows.length && <figcaption>Показано {rows.length} из {categories.length} рядов</figcaption>}
          {rows.map((row, index) => <div className="monitor-bar-item" key={row.label + index}>
            <span title={row.label}>{row.label}</span>
            <strong>{formatNumber(row.value)} <small>{row.unit}</small></strong>
            <div aria-hidden="true"><i style={{ "--bar-width": 100 * Math.abs(row.value) / (domains.get(row.unit) || 1) + "%" } as CSSProperties} /></div>
          </div>)}
        </figure>}
    </div>
  );
}
