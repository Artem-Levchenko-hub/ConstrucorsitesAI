"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";

import { EASE_OUT } from "@/lib/motion";

interface RevealProps {
  children: ReactNode;
  /** Stagger offset in seconds (e.g. `index * 0.06`). */
  delay?: number;
  /** Rise distance in px before settling (default 24). */
  y?: number;
  className?: string;
}

/**
 * Scroll-triggered fade + rise for marketing sections. Fires once when the
 * element scrolls into view (above-the-fold elements fire on mount, since
 * they're already visible — so it doubles as a staggered entrance).
 *
 * Reduced motion is honoured globally by `<MotionConfig reducedMotion="user">`
 * in providers.tsx: it drops the `y` transform and keeps a plain fade, so this
 * component never re-implements the guard.
 */
export function Reveal({ children, delay = 0, y = 24, className }: RevealProps) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.6, ease: EASE_OUT, delay }}
    >
      {children}
    </motion.div>
  );
}
