import type { DeployPhase } from "@/lib/api/types";

const ACTIVE_DEPLOY_PHASES = new Set<DeployPhase>([
  "queued",
  "building",
  "pushing",
  "swapping",
  "cancelling",
]);

export function isMaxDeployActive(phase: DeployPhase): boolean {
  return ACTIVE_DEPLOY_PHASES.has(phase);
}

export function shouldStartMaxDeploy(
  savedPhase: string,
  deploymentPhase: DeployPhase,
): boolean {
  return (
    savedPhase === "new" &&
    !isMaxDeployActive(deploymentPhase)
  );
}
