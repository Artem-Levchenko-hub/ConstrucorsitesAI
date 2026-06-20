"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api/client";
import type { ExeBuildStage, ExeReadyData } from "@/lib/api/types";

/**
 * Drives the full lifecycle of a Windows .exe build for a project:
 *   1. Opens the project WebSocket so we don't miss early events.
 *   2. POSTs to /api/projects/:id/build-exe to kick the job.
 *   3. Routes `exe.*` frames to local state (stage + ready payload).
 *   4. Closes the socket on terminal events (ready / failed) or unmount.
 *
 * Design notes
 * - The WS is opened BEFORE the POST so we can't race the first `exe.stage`.
 * - `buildIdRef` guards multi-build races: frames from an older build are dropped.
 * - `start` is a no-op while a build is in progress (stage ≠ idle/ready/failed).
 * - The hook does NOT share the project's main streaming WS (usePromptStream) —
 *   the `exe.*` namespace is additive and those events won't interfere with
 *   llm.chunk / snapshot.created routing because the hook filters by type prefix.
 */
export function useExeBuild(projectId: string) {
  const [stage, setStage] = useState<ExeBuildStage>("idle");
  const [healAttempt, setHealAttempt] = useState(0);
  const [ready, setReady] = useState<ExeReadyData | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const buildIdRef = useRef<string | null>(null);

  /** Close and forget the socket without triggering a reconnect. */
  const closeWs = useCallback(() => {
    if (wsRef.current) {
      // Null onclose first so the browser's auto-close on tab-hide doesn't
      // inadvertently re-arm anything.
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  // Cleanup on unmount — no dangling WS connections.
  useEffect(() => closeWs, [closeWs]);

  const start = useCallback(async () => {
    // Re-entrant guard: allow restart from terminal states only.
    if (stage !== "idle" && stage !== "ready" && stage !== "failed") return;

    setReady(null);
    setHealAttempt(0);
    setStage("starting");

    // Open the WS BEFORE the POST so we don't miss the first exe.stage frame
    // that the orchestrator may emit within milliseconds of accepting the job.
    const wsBase =
      process.env.NEXT_PUBLIC_WS_URL ??
      (typeof window !== "undefined"
        ? `wss://${window.location.host}`
        : "wss://constructor.lead-generator.ru");
    const ws = new WebSocket(`${wsBase}/api/ws/projects/${projectId}`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      let msg: { type?: string; data?: Record<string, unknown> };
      try {
        msg = JSON.parse(ev.data as string) as typeof msg;
      } catch {
        return; // ignore malformed frames
      }

      const { type, data = {} } = msg;
      if (!type || !type.startsWith("exe.")) return;

      // Drop frames from a different build (e.g. a stale tab that restarted).
      if (buildIdRef.current && data.build_id !== buildIdRef.current) return;

      if (type === "exe.stage") {
        setStage("build");
      } else if (type === "exe.heal") {
        setStage("heal");
        setHealAttempt(Number(data.attempt ?? 0) + 1);
      } else if (type === "exe.ready") {
        setReady(data as unknown as ExeReadyData);
        setStage("ready");
        closeWs();
      } else if (type === "exe.failed") {
        setStage("failed");
        closeWs();
      }
    };

    try {
      const res = await apiFetch<{ build_id: string }>(
        `/api/projects/${projectId}/build-exe`,
        { method: "POST" },
      );
      buildIdRef.current = res.build_id;
    } catch {
      // POST failed (network error, 4xx, 5xx) — surface via "failed" state so
      // the button renders the retry variant. Caller can read toast from context
      // if needed; we keep the hook dependency-free from sonner.
      setStage("failed");
      closeWs();
    }
  }, [projectId, stage, closeWs]);

  return { stage, healAttempt, ready, start };
}
