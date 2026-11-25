import type { MetricPoint } from "./api/monitoring";

export type ChartStatistic = "value" | "p95" | "mean";
export type ChartSample = { time: number; value: number };
export type ChartSeries = { label: string; samples: ChartSample[] };
export type ChartRow = { label: string; value: number; unit: string };

export const CURRENT_METRICS = new Set([1, 43, 45, 46, 96, 99]);
export const MIXED_UNIT_METRICS = new Set([11, 13]);

export function pointUnit(metricId: number, unit: string, series: string) {
  if (metricId === 37) return "шт.";
  if (!MIXED_UNIT_METRICS.has(metricId)) return unit;
  return series.endsWith(" / %") ? "%" : series.endsWith(" / ГБ") ? "ГБ" : unit;
}

export function chartSeries(points: MetricPoint[], statistic: ChartStatistic): ChartSeries[] {
  const readings = new Map<string, Map<number, number>>();
  for (const point of points) {
    const time = point.at ? Date.parse(point.at) : NaN;
    const value = point[statistic];
    if (value == null || !Number.isFinite(value) || !Number.isFinite(time)) continue;
    const series = readings.get(point.series) ?? new Map<number, number>();
    series.set(time, value);
    readings.set(point.series, series);
  }
  return Array.from(readings, ([label, samples]) => ({
    label,
    samples: Array.from(samples, ([time, value]) => ({ time, value })).sort((a, b) => a.time - b.time),
  })).sort((a, b) => a.label.localeCompare(b.label, "ru"));
}

export function chartRows(
  points: MetricPoint[], metricId: number, unit: string, statistic: ChartStatistic,
): ChartRow[] {
  const rows = points.flatMap((point) => {
    const value = point[statistic];
    return value != null && Number.isFinite(value)
      ? [{ label: point.series, value, unit: pointUnit(metricId, unit, point.series) }]
      : [];
  });
  return rows.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
}

export function appendSnapshots(history: MetricPoint[], points: MetricPoint[], time: string) {
  if (history[0]?.at === time || !Number.isFinite(Date.parse(time))) return history;
  const batch = points.filter((point) => !point.at).slice(0, 30).map((point) => ({ ...point, at: time }));
  if (!batch.length) return history;
  const oldest = Date.parse(time) - 60 * 60_000;
  return [...batch, ...history.filter((point) => point.at && Date.parse(point.at) > oldest)].slice(0, 3600);
}

export function splitSamples(samples: ChartSample[], expectedStep?: number): ChartSample[][] {
  const gaps = samples.slice(1).map((sample, index) => sample.time - samples[index].time)
    .sort((a, b) => a - b);
  const typical = expectedStep ?? gaps[Math.floor((gaps.length - 1) / 2)] ?? Infinity;
  const groups: ChartSample[][] = [];
  for (const sample of samples) {
    const group = groups.at(-1);
    if (!group || sample.time - group[group.length - 1].time > typical * 2.5) groups.push([sample]);
    else group.push(sample);
  }
  return groups;
}
