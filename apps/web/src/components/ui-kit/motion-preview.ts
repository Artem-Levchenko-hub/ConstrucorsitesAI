import { EASE_OUT } from "@/lib/motion";

/** Proposals for the lab only. Do not import into product screens before approval. */
export const previewMotion = {
  control: 0.14,
  content: 0.18,
  surface: 0.24,
  exit: 0.12,
  reduced: 0.08,
  pressScale: 0.985,
  surfaceScale: 0.99,
  distance: 4,
  ease: EASE_OUT,
} as const;
