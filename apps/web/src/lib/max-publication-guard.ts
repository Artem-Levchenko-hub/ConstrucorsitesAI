import type { DeployStatus, MaxReadiness, Project, PublicationMigration } from "@/lib/api/types";

export type MaxPublicationGuard = "none" | "checking" | "migration_required";

/** A failed attempt blocks only the exact current snapshot it diagnosed. */
export function getMaxPublicationGuard(
  deployment: DeployStatus | undefined,
  project: Pick<Project, "id" | "current_snapshot_id"> | undefined,
  projectId: string,
  migration?: PublicationMigration | null,
): MaxPublicationGuard {
  if (deployment?.phase !== "failed" || deployment.reason_code !== "migration_required") return "none";
  if (!deployment.snapshot_id || !project?.current_snapshot_id || project.id !== projectId) return "checking";
  if (migration?.project_id === projectId && migration.snapshot_id === project.current_snapshot_id &&
    migration.state === "applied" && migration.backup_verified && !migration.effect_possible &&
    /^[0-9a-f]{64}$/.test(migration.plan_sha256 ?? "")) return "none";
  return deployment.snapshot_id === project.current_snapshot_id ? "migration_required" : "none";
}

export function guardedMaxReadiness(readiness: MaxReadiness | undefined, guard: MaxPublicationGuard): MaxReadiness | undefined {
  if (!readiness || guard === "none") return readiness;
  return { ...readiness, ready_to_launch: false, items: readiness.items.map(item => item.id === "build" ? { ...item, done: false } : item) };
}
