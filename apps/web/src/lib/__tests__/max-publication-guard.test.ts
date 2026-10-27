import { expect, it } from "vitest";
import { getMaxPublicationGuard, guardedMaxReadiness } from "@/lib/max-publication-guard";
import type { DeployStatus, MaxReadiness, PublicationMigration } from "@/lib/api/types";

const failure = { phase: "failed", reason_code: "migration_required", snapshot_id: "current" } as DeployStatus;
it("releases only a confirmed migration for the current project and snapshot", () => {
  const project = { id: "p", current_snapshot_id: "current" };
  const receipt = { project_id: "p", snapshot_id: "current", state: "applied", backup_verified: true,
    effect_possible: false, plan_sha256: "a".repeat(64) } as PublicationMigration;
  expect(getMaxPublicationGuard(failure, project, "p", receipt)).toBe("none");
  for (const changes of [{ project_id: "other" }, { snapshot_id: "older" }, { effect_possible: true },
    { backup_verified: false }, { plan_sha256: null }, { state: "prepared" as const }]) {
    expect(getMaxPublicationGuard(failure, project, "p", { ...receipt, ...changes })).toBe("migration_required");
  }
});
it.each([
  { deployment: failure, project: { id: "p", current_snapshot_id: "current" }, expected: "migration_required" },
  { deployment: failure, project: { id: "p", current_snapshot_id: "newer" }, expected: "none" },
  { deployment: failure, project: undefined, expected: "checking" },
  { deployment: failure, project: { id: "foreign", current_snapshot_id: "current" }, expected: "checking" },
  { deployment: { ...failure, snapshot_id: undefined }, project: { id: "p", current_snapshot_id: "current" }, expected: "checking" },
  { deployment: { ...failure, reason_code: "service_readiness_failed" }, project: undefined, expected: "none" },
  { deployment: { ...failure, phase: "building" as const }, project: undefined, expected: "none" },
])("publication guard respects exact current snapshot identity: $expected", ({ deployment, project, expected }) => {
  expect(getMaxPublicationGuard(deployment, project, "p")).toBe(expected);
});
it("never mutates server readiness or hides independent prerequisite state", () => {
  const readiness: MaxReadiness = { ready_to_launch: true, progress: 100, items: ["build", "bot", "legal"].map(id => ({ id, done: true, label: id, blocking: true, action: null })) };
  const result = guardedMaxReadiness(readiness, "migration_required")!;
  expect(readiness.items.every(item => item.done)).toBe(true);
  expect(result.ready_to_launch).toBe(false);
  expect(result.items.filter(item => item.done).map(item => item.id)).toEqual(["bot", "legal"]);
  expect(guardedMaxReadiness(readiness, "none")).toBe(readiness);
});
