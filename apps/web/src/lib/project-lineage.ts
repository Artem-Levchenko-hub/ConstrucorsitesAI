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

/** Resolved source of a remix: a display name and (when resolvable) the public
 *  slug to link to. */
export type RemixSource = { name: string; slug: string | null };

/** Display name shown when the source's real name couldn't be resolved (e.g. it
 *  was deleted) — keeps the attribution sentence grammatical without a link. */
export const REMIX_SOURCE_FALLBACK_NAME = "другого проекта";

/**
 * Source attribution for the remix badge/modal, or `null` when the project is
 * not a remix. A non-empty `forked_from_name` becomes the display name (else the
 * grammatical fallback); a non-empty `forked_from_slug` becomes the link target
 * (else `null` → link-less attribution, e.g. when the source was deleted). Pure
 * + total so the modal stays a dumb renderer and the rules stay unit-tested.
 */
export function remixSource(
  project: Pick<Project, "forked_from" | "forked_from_name" | "forked_from_slug">,
): RemixSource | null {
  if (!isRemix(project)) return null;
  const name = (project.forked_from_name ?? "").trim() || REMIX_SOURCE_FALLBACK_NAME;
  const slug = (project.forked_from_slug ?? "").trim() || null;
  return { name, slug };
}
