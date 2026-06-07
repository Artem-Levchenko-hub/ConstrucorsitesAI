"use client";

import { motion } from "framer-motion";

import { EASE_OUT } from "@/lib/motion";

interface WordRevealProps {
  /** The line to reveal. Split on spaces; each word staggers in. */
  text: string;
  className?: string;
  /** Seconds before this line's first word starts — cascade stacked lines. */
  baseDelay?: number;
  /** Per-word stagger step (seconds). */
  step?: number;
}

/**
 * Hero headline reveal — splits a string into words, each rising + fading in
 * on a stagger. Built on framer-motion (the app's one motion system, see
 * lib/motion.ts), so the end state is held reliably — no WAAPI fill/persist
 * quirks. Fires on mount (intended for above-the-fold headings).
 *
 * Reduced motion is honoured globally by `<MotionConfig reducedMotion="user">`
 * in providers.tsx: it drops the rise and keeps a plain fade.
 */
export function WordReveal({
  text,
  className,
  baseDelay = 0,
  step = 0.06,
}: WordRevealProps) {
  const words = text.split(" ");
  return (
    <span className={className} style={{ display: "block" }}>
      {words.map((word, i) => (
        <motion.span
          key={`${word}-${i}`}
          style={{ display: "inline-block", willChange: "transform, opacity" }}
          initial={{ opacity: 0, y: "0.45em" }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: EASE_OUT, delay: baseDelay + i * step }}
        >
          {word}
          {i < words.length - 1 ? " " : ""}
        </motion.span>
      ))}
    </span>
  );
}
