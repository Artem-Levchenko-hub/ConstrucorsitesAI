"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiUrl } from "@/lib/api/client";
import { createMaxPreviewSession } from "@/lib/api/max-studio";
import { getRuntime } from "@/lib/api/runtime";
import { isGenerationActive } from "@/lib/generation-lifecycle";
import { isChatMessageStreaming } from "@/lib/chat-message-status";
import type { GenerationRun, GenerationRunStatus, Message } from "@/lib/api/types";

export type DraftTarget = {
  projectId: string;
  runId: string | null;
  messageId: string | null;
  phase: GenerationRunStatus;
  canOpenSavedDraft: boolean;
};

type DraftSource = { kind: "capture"; image: Blob; partial: boolean } | { kind: "live"; url: string; isolated: boolean };
type DraftResult = DraftSource | "waiting" | "preparing" | null;

export function draftPreviewLabel(source: DraftSource | null | undefined, loading: boolean, paused: boolean) {
  if (source?.kind === "capture") {
    return source.partial ? "Снимок экрана. Часть данных могла не загрузиться." : "Снимок экрана. Кнопки доступны в рабочем превью.";
  }
  if (source?.kind === "live") return source.isolated
    ? "Тестовая копия с отдельными данными." : "Рабочее превью черновика.";
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

async function openDraft(target: DraftTarget, signal: AbortSignal, stillCurrent: () => boolean): Promise<DraftResult> {
  signal.throwIfAborted();
  if (!stillCurrent()) return null;
  if (isGenerationActive({ status: target.phase })) {
    // During generation, only observe the server the builder already started.
    const runtime = await getRuntime(target.projectId);
    signal.throwIfAborted();
    if (!stillCurrent()) return null;
    if (runtime.state !== "running") return "waiting";
    const session = await createMaxPreviewSession(target.projectId, undefined, signal);
    signal.throwIfAborted();
    return stillCurrent() ? { kind: "live", url: session.url, isolated: false } : null;
  }
  // The generic environment may already have reverted to the starter or a
  // previous release. Only a run-bound session can serve this saved attempt.
  if (!target.canOpenSavedDraft || !target.runId) return null;
  try {
    const session = await createMaxPreviewSession(target.projectId, target.runId, signal);
    signal.throwIfAborted();
    return stillCurrent() ? { kind: "live", url: session.url, isolated: true } : null;
  } catch (error) {
    signal.throwIfAborted();
    if (accessDenied(error)) throw error;
    if (!stillCurrent()) return null;
    if (error instanceof ApiError && error.status === 409 && error.details?.reason === "draft_preview_preparing") return "preparing";
    if (target.phase !== "failed" && target.phase !== "cancelled") return null;
    return getCapture(target, signal);
  }
}

export function useMaxPreviewDraft(target: DraftTarget | null) {
  const queryClient = useQueryClient();
  const [brokenCapture, setBrokenCapture] = useState<string | null>(null);
  const [requestedCapture, setRequestedCapture] = useState<string | null>(null);
  const captureKey = target ? `${target.projectId}:${target.runId}` : null;
  const result = useQuery({
    queryKey: ["max-working-preview", target?.projectId, target?.runId, target?.messageId, target?.phase, target?.canOpenSavedDraft],
    enabled: target !== null,
    queryFn: async ({ signal }): Promise<DraftResult> => {
      if (!target) return null;
      return openDraft(target, signal, () => {
        const run = queryClient.getQueryData<GenerationRun | null>(["generation", target.projectId]);
        const message = queryClient.getQueryData<Message[]>(["messages", target.projectId])?.at(-1);
        if (!target.runId) return run === null && (message?.id ?? null) === target.messageId;
        return run?.project_id === target.projectId && run.id === target.runId && run.status === target.phase
          && !(isChatMessageStreaming(message) && message?.id !== run.assistant_message_id);
      });
    },
    retry: false, gcTime: 0, staleTime: Infinity,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
    // Poll only a workspace that is starting or a run-bound preview build the
    // server has already acknowledged. A refusal never triggers a retry loop.
    refetchInterval: query => !query.state.error && (query.state.data === "waiting" || query.state.data === "preparing") ? 10_000 : false,
  });
  const data = target && !result.isError && typeof result.data === "object" ? result.data : null;
  const captureAvailable = data?.kind === "capture" && captureKey !== brokenCapture;
  return {
    ...result,
    preparing: result.data === "preparing",
    source: data?.kind === "live" || (captureAvailable && requestedCapture === captureKey) ? data : null,
    captureAvailable,
    showCapture: () => setRequestedCapture(captureKey),
    onBrokenImage: () => setBrokenCapture(captureKey),
    retryWorkspace: () => {
      setRequestedCapture(null);
      setBrokenCapture(null);
      void result.refetch();
    },
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
