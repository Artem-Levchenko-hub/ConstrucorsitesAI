import type { Snapshot } from "@/lib/api/types";

/** Keep HTTP mutation and WebSocket snapshot events idempotent and newest-first. */
export function upsertSnapshotNewest(
  snapshots: Snapshot[] | undefined,
  snapshot: Snapshot,
): Snapshot[] {
  const existing = snapshots?.find((item) => item.id === snapshot.id);
  const merged = existing
    ? {
        ...existing,
        ...snapshot,
        preview_url: snapshot.preview_url ?? existing.preview_url,
      }
    : snapshot;
  return [
    merged,
    ...(snapshots ?? []).filter((item) => item.id !== snapshot.id),
  ];
}
