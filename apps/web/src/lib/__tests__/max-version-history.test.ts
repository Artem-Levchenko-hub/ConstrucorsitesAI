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
const promptStreamSource = readFileSync(
  resolve(process.cwd(), "src/hooks/usePromptStream.ts"),
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
    model_id: "google-test",
    parent_id: parentId,
    preview_url: null,
    is_rollback_target: false,
    created_at: "2026-08-02T10:00:00Z",
  };
}

describe("MAX version history", () => {
  it("hides only the empty repository bootstrap and preserves newest-first order", () => {
    const snapshots = [
      snapshot("new", "Добавь экран профиля", "old"),
      snapshot("rollback", null, "old"),
      snapshot("starter", null, null),
    ];

    expect(visibleMaxSnapshots(snapshots).map((item) => item.id)).toEqual([
      "new",
      "rollback",
    ]);
  });

  it("keeps a bounded 30-version working set for instant local switching", () => {
    const snapshots = Array.from({ length: 36 }, (_, index) =>
      snapshot(
        `v-${index}`,
        `Версия ${index}`,
        index ? `v-${index - 1}` : "starter",
      ),
    );

    const visible = visibleMaxSnapshots(snapshots);
    expect(MAX_VERSION_HISTORY_LIMIT).toBe(30);
    expect(visible).toHaveLength(30);
    expect(visible[0].id).toBe("v-0");
    expect(visible[29].id).toBe("v-29");
  });

  it("creates compact human labels and chronological version numbers", () => {
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
    expect(maxSnapshotVersion(snapshots, "missing")).toBeNull();
  });

  it("keeps the server ordinal when the 30-version window is truncated", () => {
    const snapshots = [
      { ...snapshot("new", "Новая", "old"), version_number: 36 },
      { ...snapshot("old", "Старая", "starter"), version_number: 35 },
    ];

    expect(maxSnapshotVersion(snapshots, "new")).toBe(36);
    expect(maxSnapshotVersion(snapshots, "old")).toBe(35);
  });

  it("deduplicates the same snapshot for both HTTP→WS and WS→HTTP orders", () => {
    const previous = [snapshot("old", "Старая версия", "starter")];
    const created = snapshot("new", "Новая версия", "old");

    const httpThenWs = upsertSnapshotNewest(
      upsertSnapshotNewest(previous, created),
      { ...created, preview_url: "/ready.png" },
    );
    const wsThenHttp = upsertSnapshotNewest(
      upsertSnapshotNewest(previous, { ...created, preview_url: "/ready.png" }),
      created,
    );

    expect(httpThenWs.map((item) => item.id)).toEqual(["new", "old"]);
    expect(httpThenWs[0].preview_url).toBe("/ready.png");
    expect(httpThenWs[0].version_number).toBe(2);
    expect(wsThenHttp.map((item) => item.id)).toEqual(["new", "old"]);
    expect(wsThenHttp[0].preview_url).toBe("/ready.png");
  });

  it("keeps code selection isolated and puts rollback behind an explicit confirmation", () => {
    expect(railSource).toContain('aria-label="История версий"');
    expect(railSource).toContain("overflow-y-auto overscroll-contain");
    expect(railSource).toContain("min-h-11 w-full");
    expect(railSource).toContain("aria-pressed={isCurrent || isSelected}");
    expect(railSource).toContain(
      "onClick={() => onSelect(isCurrent ? null : snapshot.id)}",
    );
    expect(railSource).not.toContain("rollbackSnapshot");

    expect(previewSource).toContain('data-testid="max-historical-snapshot"');
    expect(previewSource).toContain(
      "Версия v{selectedVersion} · изолированная сессия",
    );
    expect(previewSource).toContain(
      "disabled={!displayPreviewUrl || viewingHistorical}",
    );
    expect(previewSource).toContain(
      "snapshotId: selectedSnapshot.id",
    );
    expect(previewSource).toContain("headId: currentSnapshotId");
    expect(previewSource).toContain("Вернуться к версии v{restoreTargetVersion}?");
    expect(previewSource).toContain(
      "await onRestoreSnapshot(restoreTargetSnapshot.id)",
    );
    expect(previewSource).toContain(
      "restoreTargetId === selectedSnapshotId",
    );
    expect(previewSource).toContain(
      "restoreTarget?.headId === currentSnapshotId",
    );
  });

  it("shares one selection and one rollback mutation between both mounted previews", () => {
    expect(shellSource.match(/selectedSnapshotId=\{selectedSnapshotId\}/g)).toHaveLength(
      2,
    );
    expect(shellSource.match(/onRestoreSnapshot=\{restoreSnapshot\}/g)).toHaveLength(
      2,
    );
    expect(shellSource.match(/onSelectSnapshot=\{selectSnapshot\}/g)).toHaveLength(
      2,
    );
    expect(shellSource).toContain("rollbackSnapshot(project.id, snapshotId)");
    expect(shellSource).toContain("upsertSnapshotNewest(previous, snapshot)");
    expect(promptStreamSource).toContain(
      "return upsertSnapshotNewest(prev, snapshot)",
    );
    expect(promptStreamSource).toContain(
      'invalidateQueries({ queryKey: ["snapshots", projectId] })',
    );
    expect(shellSource).toContain("setVersionSelection(null)");
    expect(shellSource).toContain("versionSelection?.headId === currentSnapshotId");
    expect(shellSource).toContain('["max-managed-kit-sync", project.id]');
    expect(shellSource).toContain('["max-preview-session", project.id]');
    expect(shellSource).toContain("MAX_VERSION_HISTORY_LIMIT + 1");
    expect(shellSource).toContain(
      "prepareSnapshotPreview(project.id, snapshotId)",
    );
    expect(shellSource).toContain("active < 2");
    expect(shellSource).toContain("startSnapshotSession(project.id, snapshotId)");
    expect(shellSource).toContain(
      "stopSnapshotSession(project.id, snapshotId, session.session_id)",
    );
    expect(previewSource).toContain('data-testid="max-historical-iframe"');
    expect(previewSource).toContain('data?.type === "omnia:inspect:ready"');
    expect(previewSource).toContain('{ type: "omnia:inspect:ping" }');
    expect(previewSource).toContain("historicalPreviewFrame.current?.contentWindow");
    expect(shellSource).toContain("historySessionTail.current");
    expect(shellSource).toContain("StaleHistorySessionRequest");
    expect(shellSource).toContain("inert={!previewPanelVisible}");
    expect(shellSource).toContain("inert={!navigationInteractive}");
  });
});
