"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiUrl } from "@/lib/api/client";
import { createMaxPreviewSession } from "@/lib/api/max-studio";
import { getRuntime, startRuntime } from "@/lib/api/runtime";
import { isGenerationActive } from "@/lib/generation-lifecycle";
import { isChatMessageStreaming } from "@/lib/chat-message-status";
import type { GenerationRun, GenerationRunStatus, Message } from "@/lib/api/types";

export type DraftTarget = {
  projectId: string;
  runId: string | null;
  messageId: string | null;
  phase: GenerationRunStatus;
  canWake: boolean;
};

type DraftSource = { kind: "capture"; image: Blob; partial: boolean } | { kind: "live"; url: string };

export function draftPreviewLabel(source: DraftSource | null | undefined, triedWorkspace: boolean, loading: boolean, paused: boolean) {
  if (source?.kind === "capture") {
    const label = ["Сохранённый вид черновика."];
    if (source.partial) label.push("Часть данных на экране могла не загрузиться.");
    if (triedWorkspace) label.push("Рабочая среда пока недоступна.");
    return label.join(" ");
  }
  if (source?.kind === "live") return "Рабочее превью. Среда может показывать ранее восстановленную версию.";
  if (loading) return "Открываем рабочее превью.";
  return paused ? "Подготовка на паузе." : "Итоговая версия ещё не подтверждена.";
}

function accessDenied(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

async function getCapture(target: DraftTarget, signal: AbortSignal): Promise<DraftSource | null> {
  if (!target.runId) return null;
  const path = `/api/projects/${encodeURIComponent(target.projectId)}/max/runs/${encodeURIComponent(target.runId)}/preview-image`;
  try {
    const response = await fetch(apiUrl(path), {
      credentials: "include", cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
    });
    if (response.status === 401 || response.status === 403) {
      throw new ApiError(response.status, { code: "forbidden", message: "Нет доступа к предпросмотру." });
    }
    if (!response.ok || !/^image\/(png|webp|jpeg)$/i.test(response.headers.get("content-type")?.split(";")[0].trim() ?? "")) return null;
    return {
      kind: "capture", image: await response.blob(),
      partial: response.headers.get("X-Yleum-Preview-Partial") === "1",
    };
  } catch (error) {
    signal.throwIfAborted();
    if (accessDenied(error)) throw error;
    return null;
  }
}

async function getWorkspace(target: DraftTarget, signal: AbortSignal, stillCurrent: () => boolean): Promise<DraftSource | "waiting" | null> {
  signal.throwIfAborted();
  if (!stillCurrent()) return null;
  let runtime = await getRuntime(target.projectId);
  signal.throwIfAborted();
  if (!stillCurrent()) return null;
  if (target.runId && target.canWake && ["paused", "stopped", "failed"].includes(runtime.state)) {
    runtime = await startRuntime(target.projectId, target.runId);
    signal.throwIfAborted();
  }
  // A wake can return before the app starts serving. Read status for up to a
  // minute, without starting another environment or changing project files.
  for (let check = 0; target.canWake && runtime.state === "provisioning" && check < 12; check += 1) {
    await new Promise(resolve => setTimeout(resolve, 5_000));
    signal.throwIfAborted();
    if (!stillCurrent()) return null;
    runtime = await getRuntime(target.projectId);
  }
  signal.throwIfAborted();
  if (!stillCurrent()) return null;
  if (runtime.state !== "running") return "waiting";
  const session = await createMaxPreviewSession(target.projectId);
  signal.throwIfAborted();
  return { kind: "live", url: session.url };
}

export function useMaxPreviewDraft(target: DraftTarget | null) {
  const queryClient = useQueryClient();
  const [brokenCapture, setBrokenCapture] = useState<string | null>(null);
  const [requestedWorkspace, setRequestedWorkspace] = useState<string | null>(null);
  const captureKey = target ? `${target.projectId}:${target.runId}` : null;
  const skipCapture = !!captureKey && brokenCapture === captureKey;
  const preferWorkspace = !!captureKey && requestedWorkspace === captureKey;
  const result = useQuery({
    queryKey: ["max-working-preview", target?.projectId, target?.runId, target?.messageId, target?.phase, target?.canWake, skipCapture, preferWorkspace],
    enabled: target !== null,
    queryFn: async ({ signal }): Promise<DraftSource | "waiting" | null> => {
      if (!target) return null;
      // An interrupted run may have rolled its live workspace back. Its own
      // captured screen is stronger evidence, and avoids waking the environment.
      const canShowCapture = Boolean(target.runId) && !skipCapture && (target.phase === "failed" || target.phase === "cancelled");
      const captureFirst = canShowCapture && !preferWorkspace;
      if (captureFirst) {
        const captured = await getCapture(target, signal);
        if (captured) return captured;
      }
      try {
        const workspace = await getWorkspace(target, signal, () => {
          const run = queryClient.getQueryData<GenerationRun | null>(["generation", target.projectId]);
          const message = queryClient.getQueryData<Message[]>(["messages", target.projectId])?.at(-1);
          if (!target.runId) return run === null && (message?.id ?? null) === target.messageId;
          return run?.project_id === target.projectId && run.id === target.runId && run.status === target.phase
            && !(isChatMessageStreaming(message) && message?.id !== run.assistant_message_id);
        });
        if (workspace !== "waiting" || !canShowCapture || captureFirst) return workspace;
        return (await getCapture(target, signal)) ?? "waiting";
      } catch (error) {
        signal.throwIfAborted();
        if (accessDenied(error) || !canShowCapture || captureFirst) throw error;
      }
      return getCapture(target, signal);
    },
    retry: false, gcTime: 0, staleTime: Infinity,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
    // Only retry a read of a still-starting active workspace. Mutations, errors
    // and session refusals are never placed on this timer.
    refetchInterval: query => target && isGenerationActive({ status: target.phase })
      && query.state.data === "waiting" && !query.state.error ? 10_000 : false,
  });
  return {
    ...result,
    source: target && !result.isError && result.data !== "waiting" ? result.data : null,
    onBrokenImage: () => setBrokenCapture(captureKey),
    triedWorkspace: preferWorkspace,
    tryWorkspace: () => { if (preferWorkspace) void result.refetch(); else setRequestedWorkspace(captureKey); },
  };
}

export function MaxPreviewDraft({ image, onError }: { image: Blob; onError: () => void }) {
  const element = useRef<HTMLImageElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const url = URL.createObjectURL(image);
    element.current.src = url;
    return () => URL.revokeObjectURL(url);
  }, [image]);
  // A protected blob URL is already the original mobile screen.
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={element} alt="Экран черновика, сохранённый для этой попытки" onError={onError}
    data-testid="max-draft-capture" className="absolute inset-0 size-full object-contain object-top" />;
}
