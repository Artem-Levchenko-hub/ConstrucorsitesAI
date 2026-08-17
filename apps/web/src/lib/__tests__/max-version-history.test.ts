import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { Snapshot } from "@/lib/api/types";
import {
  MAX_VERSION_HISTORY_LIMIT,
  maxSnapshotLabel,
  maxSnapshotVersion,
  visibleMaxSnapshots,
} from "@/lib/max-version-history";
import { upsertSnapshotNewest } from "@/lib/snapshot-history";

const railSource = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxVersionRail.tsx"),
  "utf8",
);
const previewSource = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxLivePreview.tsx"),
  "utf8",
);
const shellSource = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxWorkspaceShell.tsx"),
  "utf8",
);

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

describe("MAX version history", () => {
  it("collapses bootstrap and technical snapshots into one user version", () => {
    const snapshots = [
      snapshot("release-sync", null, "generated"),
      snapshot("generated", "Создай приложение", "template-sync"),
      snapshot("template-sync", null, "starter"),
      snapshot("starter", null, null),
    ];

    const visible = visibleMaxSnapshots(snapshots);

    expect(visible).toHaveLength(1);
    expect(visible[0]).toMatchObject({
      id: "release-sync",
      prompt_text: "Создай приложение",
    });
    expect(maxSnapshotVersion(visible, "release-sync")).toBe(1);
    expect(MAX_VERSION_HISTORY_LIMIT).toBe(30);
  });

  it("keeps an explicit rollback as a separate version", () => {
    const snapshots = [
      snapshot("rollback-sync", null, "rollback"),
      snapshot("rollback", "Восстановление версии", "generated"),
      snapshot("generated", "Создай приложение", "starter"),
      snapshot("starter", null, null),
    ];

    const visible = visibleMaxSnapshots(snapshots);

    expect(visible.map((item) => item.id)).toEqual([
      "rollback-sync",
      "generated",
    ]);
    expect(visible.map(maxSnapshotLabel)).toEqual([
      "Восстановление версии",
      "Создай приложение",
    ]);
  });

  it("creates compact labels and chronological version numbers", () => {
    const snapshots = [
      snapshot(
        "new",
        "  Добавь    очень длинное название для истории версий приложения  \nостальное",
        "old",
      ),
      snapshot("old", "Первая полезная версия", "starter"),
    ];

    expect(maxSnapshotLabel(snapshots[0])).toBe(
      "Добавь очень длинное назван…",
    );
    expect(maxSnapshotVersion(snapshots, "new")).toBe(2);
    expect(maxSnapshotVersion(snapshots, "old")).toBe(1);
  });

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

  it("keeps history read-only until an explicit confirmed rollback", () => {
    expect(railSource).toContain('aria-label="История версий"');
    expect(railSource).not.toContain("rollbackSnapshot");
    expect(previewSource).toContain('data-testid="max-historical-snapshot"');
    expect(previewSource).toContain("только просмотр");
    expect(previewSource).toContain("Вернуться к версии v{restoreTargetVersion}?");
    expect(previewSource).toContain(
      "await onRestoreSnapshot(restoreTargetSnapshot.id)",
    );
    expect(shellSource).toContain("rollbackSnapshot(project.id, snapshotId)");
    expect(shellSource.match(/selectedSnapshotId=\{selectedSnapshotId\}/g)).toHaveLength(
      2,
    );
    expect(shellSource.match(/restoringSnapshot=\{rollbackMutation.isPending\}/g)).toHaveLength(
      2,
    );
  });
});
