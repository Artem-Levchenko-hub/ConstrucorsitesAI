"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRuntime, startRuntime } from "@/lib/api/runtime";

/** Runtime observer and one-shot provisioning for the current preview mount. */
export function usePreviewRuntime(
  projectId: string,
  isFullstack: boolean,
  viewingOld: boolean,
) {
  const qc = useQueryClient();
  const {
    data: runtime,
    isError: runtimeError,
    isLoading: runtimeLoading,
  } = useQuery({
    queryKey: ["runtime", projectId],
    queryFn: () => getRuntime(projectId),
    enabled: isFullstack && !viewingOld,
    // Keep polling until the container is actually serving (or hard-failed).
    // The orchestrator can bring the dev container up via auto-provision
    // (e.g. right after a build) without a guaranteed `runtime.started` WS
    // event, so a one-shot read can miss the transition and leave the preview
    // stuck on the startup panel. Poll through any non-terminal state so the
    // live iframe appears on its own. (R-10: converge to the real state.)
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === "running" || s === "failed" ? false : 2_000;
    },
    retry: false,
  });

  // V2: provision the dev container on open so the live Next.js app appears
  // by itself — no need to hunt for the TopBar "Запустить". `provision` is
  // idempotent; we fire it once. If the orchestrator is down the mutation
  // errors and the in-frame panel below shows a "Запустить" retry instead of
  // a blank iframe.
  const startMut = useMutation({
    mutationFn: () => startRuntime(projectId),
    onSuccess: (s) => qc.setQueryData(["runtime", projectId], s),
  });
  const autoStarted = useRef(false);
  const runtimeState = runtime?.state;
  useEffect(() => {
    if (viewingOld || !isFullstack || autoStarted.current || runtimeLoading) return;
    const idle =
      runtimeError ||
      runtimeState === "stopped" ||
      runtimeState === "failed" ||
      runtimeState === undefined;
    if (idle) {
      autoStarted.current = true;
      startMut.mutate();
    }
  }, [viewingOld, isFullstack, runtimeLoading, runtimeError, runtimeState, startMut]);

  function start() {
    autoStarted.current = true;
    startMut.mutate();
  }

  return { runtime, runtimeState, starting: startMut.isPending, start };
}