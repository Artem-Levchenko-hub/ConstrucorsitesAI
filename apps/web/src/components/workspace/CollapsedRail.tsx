"use client";

/**
 * Vertical 44 px rail that replaces a side panel when collapsed.
 *
 * Click the chevron (or hit the panel's hotkey — `[` for chat, `]` for
 * timeline) to expand back. The label sits vertically along the rail so
 * the panel's identity stays visible even at the narrowest width.
 *
 * `activity` toggles a breathing accent dot above the label, signalling
 * unread chat / in-progress timeline render so the user knows there's
 * something to come back to without having to expand the panel.
 */

import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export function CollapsedRail({
  label,
  side,
  hotkey,
  activity = false,
  onExpand,
  accentColor = "violet",
}: {
  label: string;
  side: "left" | "right";
  hotkey: string;
  activity?: boolean;
  onExpand: () => void;
  /** Decorative tint applied to the rail background. Matches the panel
      it stands in for (violet for chat, cyan for timeline). */
  accentColor?: "violet" | "cyan";
}) {
  const ChevronIcon = side === "left" ? ChevronRight : ChevronLeft;
  const accentClass =
    accentColor === "violet"
      ? "from-accent via-accent/60"
      : "from-accent-secondary via-accent-secondary/60";
  const accentGradient =
    accentColor === "violet"
      ? "linear-gradient(180deg, rgb(124 92 255 / 0.18) 0%, transparent 100%)"
      : "linear-gradient(180deg, rgb(92 184 255 / 0.18) 0%, transparent 100%)";
  const bgDotColor = accentColor === "violet" ? "bg-accent" : "bg-accent-secondary";
  const borderClass =
    side === "left" ? "border-r border-border-subtle" : "border-l border-border-subtle";
  const stripeClass =
    side === "left"
      ? `bg-gradient-to-r ${accentClass} to-transparent`
      : `bg-gradient-to-l ${accentClass} to-transparent`;

  return (
    <div
      className={cn(
        "relative w-[44px] backdrop-blur-xl flex flex-col items-center py-3",
        borderClass,
      )}
      style={{ background: accentGradient }}
    >
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-[3px]",
          stripeClass,
        )}
      />

      <button
        type="button"
        onClick={onExpand}
        title={`Развернуть ${label} (${hotkey})`}
        aria-label={`Развернуть ${label}`}
        className="h-8 w-8 rounded-lg border border-accent/30 bg-accent/15 text-accent hover:bg-accent/25 transition flex items-center justify-center mb-2"
      >
        <ChevronIcon className="h-4 w-4" />
      </button>

      {/* Vertical label — flips depending on side so it reads naturally
          when the user tilts their head to whichever side the rail is on. */}
      <div
        className="mt-2 text-[11px] font-mono text-fg-tertiary uppercase tracking-widest flex items-center gap-2"
        style={{
          writingMode: "vertical-rl",
          transform: side === "right" ? "rotate(180deg)" : "none",
        }}
      >
        {label}
        {activity && (
          <span
            aria-hidden="true"
            className={cn(
              "inline-flex h-1.5 w-1.5 rounded-full animate-breathe-glow",
              bgDotColor,
            )}
          />
        )}
      </div>
    </div>
  );
}
