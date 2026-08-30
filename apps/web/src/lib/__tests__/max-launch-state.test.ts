import { describe, expect, it } from "vitest";

import { shouldStartMaxDeploy } from "@/lib/max-launch-state";

describe("MAX launch resume state", () => {
  it.each(["failed", "cancelled"] as const)(
    "does not restart a saved %s deployment after reload",
    (phase) => {
      expect(shouldStartMaxDeploy("deploying", phase)).toBe(false);
    },
  );

  it.each(["failed", "cancelled"] as const)(
    "allows an explicit new click to restart a %s deployment",
    (phase) => {
      expect(shouldStartMaxDeploy("new", phase)).toBe(true);
    },
  );

  it("does not duplicate an active deployment", () => {
    expect(shouldStartMaxDeploy("new", "building")).toBe(false);
    expect(shouldStartMaxDeploy("new", "queued")).toBe(false);
  });

  it("publishes fresh edits even when the previous deployment is done", () => {
    expect(shouldStartMaxDeploy("new", "done")).toBe(true);
  });
});
