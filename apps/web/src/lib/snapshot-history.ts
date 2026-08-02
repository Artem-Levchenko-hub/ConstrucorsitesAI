import type { Snapshot } from "@/lib/api/types";

/**
 * HTTP mutations and the project WebSocket can report the same snapshot in
 * either order. Keep the cache newest-first and idempotent for both paths.
 */
export function upsertSnapshotNewest(
  snapshots: Snapshot[] | undefined,
  snapshot: Snapshot,
): Snapshot[] {
  const existing = snapshots?.find((item) => item.id === snapshot.id);
  const nextVersion =
    Math.max(
      snapshots?.length ?? 0,
      ...(snapshots ?? []).map((item) => item.version_number ?? 0),
    ) + 1;
  const merged = existing
    ? {
        ...existing,
        ...snapshot,
        // preview.ready can beat the HTTP mutation response. Never downgrade
        // an already enriched cache entry back to the loading placeholder.
        preview_url: snapshot.preview_url ?? existing.preview_url,
        version_number:
          snapshot.version_number ?? existing.version_number,
      }
    : {
        ...snapshot,
        version_number: snapshot.version_number ?? nextVersion,
      };
  return [
    merged,
    ...(snapshots ?? []).filter((item) => item.id !== snapshot.id),
  ];
}
