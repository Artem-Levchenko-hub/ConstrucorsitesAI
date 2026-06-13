/**
 * V4.2b-finish leg (B) — fork ("Remix this") lineage, surfaced in the
 * workspace.
 *
 * A remixed project carries `forked_from` (the source it was forked from); the
 * backend already inherits the source's design preset + onboarding spec at fork
 * time (apps/api routers/projects.py `perform_fork`), so the only thing missing
 * client-side was knowing a project IS a remix. These pure helpers keep that
 * decision out of the component (so it stays unit-testable) and out of JSX
 * (so the label can't drift between callers).
 */
import type { Project } from "@/lib/api/types";

/** Label rendered on the workspace remix badge. */
export const REMIX_BADGE_LABEL = "Ремикс";

/**
 * True when the project was forked from another project. Treats `null`,
 * `undefined`, and empty string identically (organic project → no badge); a
 * non-empty `forked_from` is the only thing that flips it on.
 */
export function isRemix(project: Pick<Project, "forked_from">): boolean {
  return typeof project.forked_from === "string" && project.forked_from.length > 0;
}
