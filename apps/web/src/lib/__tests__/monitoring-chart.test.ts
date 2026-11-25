import { describe, expect, it } from "vitest";
import type { MetricPoint } from "../api/monitoring";
import { appendSnapshots, chartRows, chartSeries, splitSamples } from "../monitoring-chart";

function point(series: string, value: number, at: string | null = null): MetricPoint {
  return { series, value, at, p95: null, samples: null };
}

describe("monitoring chart data", () => {
  it("keeps dated series separate and removes duplicates without inserting zeroes", () => {
    const early = "2026-11-25T00:00:00Z";
    const late = "2026-11-25T00:05:00Z";
    expect(chartSeries([
      point("A", 8, late), point("B", 0, late), point("A", 9, early),
      point("A", 12, late), point("A", 3), point("B", 1, "bad time"),
    ], "value")).toEqual([
      { label: "A", samples: [{ time: Date.parse(early), value: 9 }, { time: Date.parse(late), value: 12 }] },
      { label: "B", samples: [{ time: Date.parse(late), value: 0 }] },
    ]);
  });

  it("breaks a line across missing live samples", () => {
    const samples = [{ time: 0, value: 3 }, { time: 15_000, value: 5 }, { time: 110_000, value: 9 }];
    expect(splitSamples(samples, 15_000)).toEqual([samples.slice(0, 2), samples.slice(2)]);
  });

  it("retains only recent snapshots and skips missing or duplicate readings", () => {
    const time = "2026-11-25T02:00:00Z";
    const history = [point("old", 2, "2026-11-25T00:58:00Z"), point("fresh", 8, "2026-11-25T01:55:00Z")];
    const next = appendSnapshots(history, [point("fresh", 9)], time);
    expect(next).toEqual([point("fresh", 9, time), history[1]]);
    expect(appendSnapshots(next, [point("fresh", 10)], time)).toBe(next);
    expect(appendSnapshots(next, [], "2026-11-25T02:30:00Z")).toBe(next);
  });

  it("preserves a real zero and carries separate units in categorical breakdowns", () => {
    expect(chartRows([point("VPS / %", 48), point("VPS / ГБ", 6), point("Backup / ГБ", 0)], 11, "ГБ, %", "value"))
      .toEqual([
        { label: "VPS / %", unit: "%", value: 48 },
        { label: "VPS / ГБ", unit: "ГБ", value: 6 },
        { label: "Backup / ГБ", unit: "ГБ", value: 0 },
      ]);
  });
});
