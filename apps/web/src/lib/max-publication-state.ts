import type { DeployPhase, MaxReadiness } from "@/lib/api/types";

export type MaxPublicationState =
  | "checking"
  | "published"
  | "outdated"
  | "unpublished";

/**
 * A successful deployment is historical. The readiness endpoint is the
 * canonical answer for whether that deployment still matches the project's
 * current snapshot.
 */
export function getMaxPublicationState(
  readiness: MaxReadiness | undefined,
  deployPhase: DeployPhase | undefined,
): MaxPublicationState {
  if (!readiness) return "checking";

  const currentVersionPublished =
    readiness.items.find((item) => item.id === "publish")?.done === true;

  if (currentVersionPublished) return "published";
  if (deployPhase === "done") return "outdated";
  return "unpublished";
}
