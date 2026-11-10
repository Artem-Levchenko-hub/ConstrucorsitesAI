import { EASE_OUT } from "@/lib/motion";

/** Proposals for the lab only. Do not import into product screens before approval. */
export const previewMotion = {
  control: 0.18,
  content: 0.26,
  surface: 0.34,
  exit: 0.16,
  progress: 0.4,
  reduced: 0.08,
  pressScale: 0.97,
  surfaceScale: 0.96,
  distance: 10,
  pageDistance: 24,
  ease: EASE_OUT,
} as const;
