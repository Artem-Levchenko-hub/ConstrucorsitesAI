"use client";

import { useEffect, useMemo } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { listProjectVersions, listSnapshots } from "@/lib/api/snapshots";
import type { Project, Snapshot } from "@/lib/api/types";
import { MAX_CUSTOMER_VERSIONING } from "@/lib/max-product-policy";

/** Shared owner preview history; every page retains the HEAD it was requested for. */
export function useMaxPreviewHistory(project: Project) {
  const queryClient = useQueryClient();
  const snapshots = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });
  const currentSnapshotId = snapshots.data?.[0]?.id ?? project.current_snapshot_id;
  const history = useInfiniteQuery({
    queryKey: MAX_CUSTOMER_VERSIONING ? ["project-versions", project.id] : ["project-versions", project.id, "current-build"],
    maxPages: MAX_CUSTOMER_VERSIONING ? undefined : 1,
    queryFn: async ({ pageParam, signal }) => {
      // Capture before the request: a late response for an earlier HEAD must
      // not authorize live preview of that HEAD's selected user version.
      const snapshotIdAtRequest = queryClient.getQueryData<Snapshot[]>(["snapshots", project.id])?.[0]?.id
        ?? project.current_snapshot_id;
      const page = await listProjectVersions(project.id, pageParam, signal);
      return { ...page, snapshotIdAtRequest };
    },
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5_000,
  });
  const versions = useMemo(() => {
    const seen = new Set<string>();
    return (history.data?.pages.flatMap((page) => page.versions) ?? []).filter((version) => {
      if (seen.has(version.id)) return false;
      seen.add(version.id); return true;
    });
  }, [history.data]);
  const historyCurrent = !!history.data?.pages.length
    && history.data.pages.every((page) => page.snapshotIdAtRequest === currentSnapshotId);
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["project-versions", project.id] });
  }, [currentSnapshotId, project.id, queryClient]);
  return { currentSnapshotId, versions, historyCurrent, history, snapshots };
}
