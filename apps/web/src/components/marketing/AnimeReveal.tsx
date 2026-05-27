"use client";

import { useEffect, useRef } from "react";
import { waapi } from "animejs";

type Mode = "word" | "letter";

interface AnimeRevealProps {
  /** The text to reveal. Splits on whitespace or per-letter. */
  children: string;
  /**
   * Split granularity. `word` = each word eases in (default, fast and
   * readable). `letter` = every glyph staggers (Apple-style hero, slower
   * — usually paired with shorter strings).
   */
  mode?: Mode;
  /** Per-token delay step (ms). Defaults: 45 for words, 22 for letters. */
  stagger?: number;
  /** Total per-span animation duration. */
  duration?: number;
  /** Wrapper element tag — `h1` for hero, `span` for inline. */
  as?: "h1" | "h2" | "h3" | "p" | "span";
  /** className passes through to the wrapper. */
  className?: string;
}

/**
 * Awwwards-style staggered text reveal. Splits the string into spans
 * and animates each via `waapi.animate` from `animejs`. Respects
 * `prefers-reduced-motion` (downgrades to a single fade).
 *
 * The component is the React counterpart to the
 * `.anime-hero-reveal` selector wired into `omnia-kit.js` for
 * user-generated sites — same effect, same easing curve.
 */
export function AnimeReveal({
  children,
  mode = "word",
  stagger,
  duration = 700,
  as: Tag = "h1",
  className,
}: AnimeRevealProps) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const text = children;
    const parts =
      mode === "letter" ? Array.from(text) : text.split(/(\s+)/);

    // Build span tree once. If the children prop changes between renders
    // we wipe and rebuild — the parent should change `key` for that.
    el.textContent = "";
    const spans: HTMLSpanElement[] = [];
    parts.forEach((p) => {
      if (p === "") return;
      const span = document.createElement("span");
      span.textContent = p;
      span.style.display = "inline-block";
      span.style.whiteSpace = /^\s+$/.test(p) ? "pre" : "";
      span.style.opacity = "0";
      if (!reduceMotion) span.style.transform = "translateY(0.4em)";
      el.appendChild(span);
      if (!/^\s+$/.test(p)) spans.push(span);
    });

    const step = stagger ?? (mode === "letter" ? 22 : 45);
    spans.forEach((span, i) => {
      waapi.animate(span, {
        opacity: [0, 1],
        translateY: reduceMotion ? undefined : ["0.4em", "0em"],
        duration: reduceMotion ? 300 : duration,
        delay: i * (reduceMotion ? 20 : step),
        easing: "cubicBezier(0.22, 1, 0.36, 1)",
      });
    });
    // No cleanup needed: animations finish on their own and we don't
    // want to interrupt them on parent re-render. If you need to
    // re-trigger on prop change, give the parent a fresh `key`.
  }, [children, mode, stagger, duration]);

  // Render the raw string once for SSR + as fallback when JS is off.
  // The useEffect above replaces it with split spans on hydration.
  return (
    <Tag ref={ref as React.Ref<HTMLHeadingElement>} className={className}>
      {children}
    </Tag>
  );
}
