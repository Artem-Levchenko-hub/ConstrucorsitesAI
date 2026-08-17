import type { Snapshot } from "@/lib/api/types";

const MAX_VERSION_LABEL_LENGTH = 28;
export const MAX_VERSION_HISTORY_LIMIT = 30;

/** Hide the empty repository bootstrap: it is not a user-visible version. */
export function visibleMaxSnapshots(snapshots: Snapshot[]): Snapshot[] {
  return snapshots
    .filter(
      (snapshot) => snapshot.prompt_text !== null || snapshot.parent_id !== null,
    )
    .slice(0, MAX_VERSION_HISTORY_LIMIT);
}

export function maxSnapshotLabel(snapshot: Snapshot): string {
  const prompt = snapshot.prompt_text
    ?.split(/\r?\n/, 1)[0]
    ?.replace(/\s+/g, " ")
    .trim();
  const fallback = snapshot.parent_id ? "Восстановление" : "Первая версия";
  const label = prompt || fallback;

  if (label.length <= MAX_VERSION_LABEL_LENGTH) return label;
  return `${label.slice(0, MAX_VERSION_LABEL_LENGTH - 1).trimEnd()}…`;
}

/** Snapshots arrive newest-first; product version numbers grow oldest-first. */
export function maxSnapshotVersion(
  snapshots: Snapshot[],
  snapshotId: string,
): number | null {
  const index = snapshots.findIndex((snapshot) => snapshot.id === snapshotId);
  if (index === -1) return null;
  return snapshots.length - index;
}
