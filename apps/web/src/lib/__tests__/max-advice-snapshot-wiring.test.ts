import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("passes the workspace's live HEAD to chat instead of a separate stale project query", () => {
  const shell = readFileSync("src/components/max/MaxWorkspaceShell.tsx", "utf8");
  const chat = readFileSync("src/components/workspace/ChatPanel.tsx", "utf8");
  expect(shell).toMatch(/<ChatPanel[\s\S]*?currentSnapshotId=\{currentSnapshotId\}/);
  expect(chat).toContain("getProductAdviceSnapshotId(messages ?? [], currentSnapshotId)");
  expect(chat).not.toContain("currentProject?.current_snapshot_id");
});
