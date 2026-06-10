"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Animated count-up number for KPI values — the "living dashboard" touch.
 *
 * Drop it into a `<StatCard value={…} />` (or anywhere a number shows up) and
 * the figure rolls from 0 up to its target on mount:
 *
 *   <StatCard accent label="Выручка" value={<CountUp value={total} suffix=" ₽" />} />
 *   <CountUp value={1280} />
 *
 * SSR renders the FINAL value (no hydration mismatch, correct with JS off),
 * then the client animates 0 → value once it mounts. Honours
 * `prefers-reduced-motion`: motion-averse users see the final number instantly,
 * no roll. Numbers are formatted with `Intl.NumberFormat` (ru-RU thousands
 * separators by default) and rendered `tabular-nums` so the width never jitters
 * mid-count.
 *
 * Use sparingly — one or two count-ups per dashboard (the hero KPIs), never on
 * every tile (Refactoring UI: emphasis you spend everywhere is emphasis spent
 * nowhere).
 */
export interface CountUpProps {
  /** Target value to count up to. */
  value: number;
  /** Text before the number, e.g. `"$"`. */
  prefix?: string;
  /** Text after the number, e.g. `" ₽"` or `"%"`. */
  suffix?: string;
  /** Fraction digits to show (default 0). */
  decimals?: number;
  /** Intl locale for grouping/decimals (default `"ru-RU"`). */
  locale?: string;
  /** Roll duration in ms (default 1100). */
  duration?: number;
  className?: string;
}

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

export function CountUp({
  value,
  prefix,
  suffix,
  decimals = 0,
  locale = "ru-RU",
  duration = 1100,
  className,
}: CountUpProps) {
  // First render (SSR + hydration) shows the final value, so the markup matches
  // and the number is correct without JS. The roll starts after mount.
  const [display, setDisplay] = React.useState(value);
  const fromRef = React.useRef(0);

  const format = React.useCallback(
    (n: number) =>
      new Intl.NumberFormat(locale, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(n),
    [locale, decimals],
  );

  React.useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced || duration <= 0 || fromRef.current === value) {
      fromRef.current = value;
      setDisplay(value);
      return;
    }

    const from = fromRef.current;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setDisplay(from + (value - from) * easeOutCubic(t));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        fromRef.current = value;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  return (
    <span className={cn("tabular-nums", className)}>
      {prefix}
      {format(decimals > 0 ? display : Math.round(display))}
      {suffix}
    </span>
  );
}
