import * as React from "react";

import { share } from "@/app/omnia-share";

/**
 * Shared brand-token helpers for the drizzle template.
 *
 * The drizzle template ships NO shadcn token system (`--primary` / `--background`
 * CSS vars) and no `omnia/` kit, so every branded surface (the default landing,
 * the split-screen auth chrome) derives its colour from the project's
 * `share.accent` instead. These helpers are pure (server-component-safe) and
 * shared so the landing and auth screens stay byte-for-byte consistent — one
 * accent, one gradient recipe, one legibility rule.
 */

/** Pick a legible foreground (near-white or near-black) for text/icons sitting
 *  on top of `hex`, via WCAG relative luminance — so the brand accent can drive
 *  a primary button without ever producing unreadable button text. */
export function readableOn(hex: string): string {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return "#ffffff";
  let h = m[1];
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return L > 0.45 ? "#0b0b0c" : "#ffffff";
}

/** A deep, brand-tinted gradient used by the auth showcase panel. Rides the
 *  accent from a light top to a near-black bottom so light/dark accents both
 *  stay legible under white text. */
export function panelGradient(accent: string): string {
  return [
    "linear-gradient(155deg,",
    `color-mix(in oklab, ${accent}, white 10%) 0%,`,
    `${accent} 34%,`,
    `color-mix(in oklab, ${accent}, #060810 86%) 100%)`,
  ].join(" ");
}

/** APP-DNA motion-half: each tempo is a ready entrance curve + duration that the
 *  kit's `.fade-up` / `.fade-in` / `.scale-in` / `.reveal` family reads through
 *  `--omnia-ease` / `--omnia-dur` (globals.css). One tempo drives the birth of
 *  every surface, so a luxe gallery (calm) and a kids' shop (snappy) visibly
 *  "come alive" apart — mirrors the entities kit's brief-driven motion-DNA. */
const MOTION_TEMPI: Record<"calm" | "snappy" | "precise", { ease: string; dur: string }> = {
  calm: { ease: "cubic-bezier(.22,1,.36,1)", dur: ".72s" }, // luxe / media / content
  snappy: { ease: "cubic-bezier(.34,1.56,.64,1)", dur: ".34s" }, // shop / lifestyle / e-com
  precise: { ease: "cubic-bezier(.4,0,.2,1)", dur: ".5s" }, // fintech / B2B / SaaS
};

/** Pin the brand accent + entrance tempo as local CSS vars on a root element so
 *  descendants can read them through Tailwind arbitrary values
 *  (`bg-[var(--brand)]`, `focus:ring-[var(--brand)]`) and the kit's entrance
 *  animations (`--omnia-ease` / `--omnia-dur`). No dependency on any
 *  template-wide token system — drizzle ships none. The motion pair rides
 *  `share.motion` (pinned per-build by services/share_meta.py) the same way the
 *  accent rides `share.accent`. */
export function brandTokens(accent: string): React.CSSProperties {
  const tempo = MOTION_TEMPI[share.motion as keyof typeof MOTION_TEMPI] ?? MOTION_TEMPI.precise;
  return {
    "--brand": accent,
    "--brand-fg": readableOn(accent),
    "--omnia-ease": tempo.ease,
    "--omnia-dur": tempo.dur,
  } as React.CSSProperties;
}

/** The project name, falling back to "Omnia" before a real build overwrites
 *  `omnia-share.ts` with the project's title. */
export function brandName(): string {
  return share.title && share.title !== "Omnia project" ? share.title : "Omnia";
}
