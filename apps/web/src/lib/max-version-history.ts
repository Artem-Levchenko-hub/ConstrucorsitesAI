import type { Snapshot } from "@/lib/api/types";

const MAX_VERSION_LABEL_LENGTH = 28;
export const MAX_VERSION_HISTORY_LIMIT = 30;

/**
 * Turn low-level repository snapshots into user-visible versions.
 *
 * MAX writes a few technical snapshots around one build (template init,
 * release-kit sync, proof refresh). They are implementation details, not new
 * user versions. A snapshot with prompt_text starts a version; later technical
 * snapshots are folded into it so the representative still points at the real
 * final HEAD/preview. Rollback snapshots carry a semantic prompt from the API
 * and therefore correctly start a new version too.
 */
export function visibleMaxSnapshots(snapshots: Snapshot[]): Snapshot[] {
  const versions: Snapshot[] = [];

  for (const snapshot of [...snapshots].reverse()) {
    const prompt = snapshot.prompt_text?.trim();
    if (prompt) {
      versions.push(snapshot);
      continue;
    }

    if (versions.length === 0) continue;

    const previous = versions[versions.length - 1];
    versions[versions.length - 1] = {
      ...snapshot,
      // Keep the user intent as the label while using the newest technical
      // snapshot's id, commit and preview as the actual version state.
      prompt_text: previous.prompt_text,
      model_id: snapshot.model_id ?? previous.model_id,
    };
  }

  return versions.reverse().slice(0, MAX_VERSION_HISTORY_LIMIT);
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
