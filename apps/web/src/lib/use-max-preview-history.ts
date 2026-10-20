"use client";

import { useEffect, useMemo, useRef } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { listProjectVersions, listSnapshots } from "@/lib/api/snapshots";
import { getLatestGeneration } from "@/lib/api/messages";
import type { Project, Snapshot } from "@/lib/api/types";
import { MAX_CUSTOMER_VERSIONING } from "@/lib/max-product-policy";
import { isGenerationResultConfirmed } from "@/lib/generation-recovery";

/** Shared owner preview history; every page retains the HEAD it was requested for. */
export function useMaxPreviewHistory(project: Project) {
  const queryClient = useQueryClient();
  const snapshots = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });
  const currentSnapshotId = snapshots.data?.[0]?.id ?? project.current_snapshot_id;
  // The chat/preview already observes this canonical query. Watch its receipt
  // without creating another poller: a lost snapshot.created event must not
  // leave HEAD pinned to the starter after the owner API confirms a new version.
  const generation = useQuery({
    queryKey: ["generation", project.id], queryFn: () => getLatestGeneration(project.id), enabled: false,
  });
  const confirmedSnapshotId = generation.data?.project_id === project.id && isGenerationResultConfirmed(generation.data)
    ? generation.data.acceptance?.snapshot_id : null;
  const confirmedRunId = confirmedSnapshotId ? generation.data?.id : null;
  const refreshedReceipt = useRef<string | null>(null);
  useEffect(() => {
    if (!confirmedSnapshotId || confirmedSnapshotId === currentSnapshotId || snapshots.isFetching) return;
    // Invalidating a pending first fetch joins its existing retryer: a starter
    // response can otherwise swallow this refresh. Wait for that fetch to settle
    // and perform at most one fresh read for this exact accepted receipt.
    const receipt = JSON.stringify([project.id, confirmedRunId, confirmedSnapshotId]);
    if (refreshedReceipt.current === receipt) return;
    refreshedReceipt.current = receipt;
    void queryClient.invalidateQueries({ queryKey: ["snapshots", project.id], exact: true });
  }, [confirmedRunId, confirmedSnapshotId, currentSnapshotId, snapshots.isFetching, project.id, queryClient]);
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
