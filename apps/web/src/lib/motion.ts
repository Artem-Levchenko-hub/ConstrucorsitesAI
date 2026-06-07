import type { Transition, Variants } from "framer-motion";

/**
 * Shared motion language for the workspace ("agent screen"). One small set of
 * spring/ease tokens so every micro-interaction — chat, editing, panels,
 * toggles — feels like the same physical material (Apple-style: settle with a
 * gentle decelerate, depress on tap, no flashy bounce).
 *
 * Reduced-motion is honoured globally by `<MotionConfig reducedMotion="user">`
 * in providers.tsx — it strips transforms/layout for users who ask for it and
 * keeps only opacity, so individual components don't each re-implement the
 * guard.
 */

// Typed as a mutable 4-tuple (not `as const`) so it satisfies framer-motion's
// `BezierDefinition` ([number, number, number, number]) without a readonly clash.
/** Apple decelerate — fast out of the gate, long soft settle. The house ease. */
export const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];
/** Symmetric ease for things that move both ways (collapse, crossfade). */
export const EASE_IN_OUT: [number, number, number, number] = [0.4, 0, 0.2, 1];

/** Snappy spring for small UI controls (toggles, segmented indicators, taps). */
export const springSnappy: Transition = {
  type: "spring",
  stiffness: 420,
  damping: 34,
  mass: 0.9,
};

/** Softer spring for surfaces that grow in (panels, popovers, menus). */
export const springSoft: Transition = {
  type: "spring",
  stiffness: 260,
  damping: 26,
  mass: 1,
};

/** whileTap depress — the universal "I felt that" feedback. */
export const tap = { scale: 0.96 } as const;
/** Lighter depress for large/whole-card press targets. */
export const tapSubtle = { scale: 0.985 } as const;
/** Gentle hover lift for cards / interactive tiles. */
export const hoverLift = { y: -2 } as const;

/** Panels & popovers — scale + lift in from rest, settle on a soft spring. */
export const popIn: Variants = {
  hidden: { opacity: 0, scale: 0.96, y: 8 },
  visible: { opacity: 1, scale: 1, y: 0, transition: springSoft },
  exit: {
    opacity: 0,
    scale: 0.97,
    y: 6,
    transition: { duration: 0.14, ease: EASE_IN_OUT },
  },
};

/** Chips / tokens popping into a list (e.g. picked elements while editing). */
export const chipIn: Variants = {
  hidden: { opacity: 0, scale: 0.9, y: -2 },
  visible: { opacity: 1, scale: 1, y: 0, transition: springSnappy },
  exit: {
    opacity: 0,
    scale: 0.9,
    transition: { duration: 0.12, ease: EASE_IN_OUT },
  },
};

/** Chat rows / inline notices sliding up into place. */
export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 6 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.26, ease: EASE_OUT },
  },
};
