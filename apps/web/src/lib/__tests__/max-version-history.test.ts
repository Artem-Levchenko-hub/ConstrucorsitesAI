import { describe, expect, it } from "vitest";
import type { Snapshot } from "@/lib/api/types";
import { upsertSnapshotNewest } from "@/lib/snapshot-history";

function snapshot(
  id: string,
  promptText: string | null,
  parentId: string | null,
): Snapshot {
  return {
    id,
    project_id: "project-1",
    commit_sha: `${id}abcdef0123456789`,
    prompt_text: promptText,
    model_id: "test-model",
    parent_id: parentId,
    preview_url: null,
    is_rollback_target: false,
    created_at: "2026-08-02T10:00:00Z",
  };
}

describe("Snapshot event cache", () => {
  it("deduplicates HTTP and WebSocket reports of the same snapshot", () => {
    const previous = [snapshot("old", "Старая", "starter")];
    const created = snapshot("new", "Новая", "old");
    const merged = upsertSnapshotNewest(
      upsertSnapshotNewest(previous, created),
      { ...created, preview_url: "/ready.png" },
    );

    expect(merged.map((item) => item.id)).toEqual(["new", "old"]);
    expect(merged[0].preview_url).toBe("/ready.png");
  });

});
