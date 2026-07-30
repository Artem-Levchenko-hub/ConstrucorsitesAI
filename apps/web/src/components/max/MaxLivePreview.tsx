"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, Play, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  createMaxPreviewSession,
  syncMaxManagedKit,
} from "@/lib/api/max-studio";
import { getRuntime, startRuntime } from "@/lib/api/runtime";
import type { Project } from "@/lib/api/types";

const DEVICE_WIDTH = 390;
const DEVICE_HEIGHT = 844;

export function MaxLivePreview({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const started = useRef(false);
  const deviceStage = useRef<HTMLDivElement>(null);
  const [deviceScale, setDeviceScale] = useState(0.75);
  const runtime = useQuery({
    queryKey: ["runtime", project.id],
    queryFn: () => getRuntime(project.id),
    retry: false,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "running" || state === "failed" ? false : 2_000;
    },
  });
  const start = useMutation({
    mutationFn: () => startRuntime(project.id),
    onSuccess: (value) => queryClient.setQueryData(["runtime", project.id], value),
  });
  const runtimeRunning = runtime.data?.state === "running";
  const managedKit = useQuery({
    queryKey: ["max-managed-kit-sync", project.id],
    queryFn: () => syncMaxManagedKit(project.id),
    enabled: runtimeRunning,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const previewSession = useQuery({
    queryKey: [
      "max-preview-session",
      project.id,
      runtime.data?.container_name ?? null,
      managedKit.data?.synced_snapshot_id ?? null,
    ],
    queryFn: () => createMaxPreviewSession(project.id),
    enabled: runtimeRunning && managedKit.isSuccess,
    retry: 1,
    staleTime: 60_000,
  });
  const separatePreview = useMutation({
    mutationFn: () => createMaxPreviewSession(project.id),
  });

  useEffect(() => {
    if (runtime.isLoading || started.current) return;
    if (runtime.isError || !runtime.data || ["stopped", "paused", "failed"].includes(runtime.data.state)) {
      started.current = true;
      start.mutate();
    }
  }, [runtime.isLoading, runtime.isError, runtime.data, start]);

  useEffect(() => {
    const stage = deviceStage.current;
    if (!stage) return;

    const updateScale = () => {
      const bounds = stage.getBoundingClientRect();
      const next = Math.min(
        1,
        bounds.width / DEVICE_WIDTH,
        bounds.height / DEVICE_HEIGHT,
      );
      if (Number.isFinite(next) && next > 0) {
        setDeviceScale(next);
      }
    };

    updateScale();
    const observer = new ResizeObserver(updateScale);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // A relative same-origin fallback is both correct behind the production
  // reverse proxy and stable across SSR/hydration. Reading window.location
  // during render produced different href values on server and client.
  const apiOrigin = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
  const publicUrl = apiOrigin
    ? `${apiOrigin}/p/${project.slug}`
    : `/p/${project.slug}`;
  const previewUrl = previewSession.data?.url ?? null;
  const connected = Boolean(previewUrl);
  const previewError =
    managedKit.error ?? previewSession.error ?? start.error ?? runtime.error;
  const preparing =
    runtime.isLoading ||
    start.isPending ||
    (runtimeRunning && (managedKit.isLoading || previewSession.isLoading));

  async function openSeparatePreview() {
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;

    try {
      const session = await separatePreview.mutateAsync();
      if (!popup) {
        toast.error("Браузер заблокировал новую вкладку", {
          description: "Разрешите всплывающие окна и повторите попытку.",
        });
        return;
      }
      popup.location.replace(session.url);
    } catch {
      popup?.close();
      toast.error("Не удалось открыть превью", {
        description: "Обновите безопасную сессию и повторите попытку.",
      });
    }
  }

  function retryPreview() {
    if (!runtimeRunning) {
      start.mutate();
      return;
    }
    if (managedKit.isError) {
      void managedKit.refetch();
      return;
    }
    void previewSession.refetch();
  }

  return (
    <aside
      className="flex h-full min-h-0 flex-col bg-[#f5f3ee] px-3 py-3 sm:px-5 sm:py-4"
      data-testid="max-live-preview"
    >
      <div className="flex shrink-0 items-center justify-between gap-3">
        <div>
          <p className="omnia-kicker text-[#8d887f]">Mobile WebView</p>
          <h2 className="mt-1 text-sm font-semibold">Живое превью</h2>
        </div>
        <span className="inline-flex items-center gap-2 text-[10px] text-[#6d6962]">
          <span className={`size-1.5 rounded-full ${connected ? "bg-[#248a4b]" : "bg-[#aaa59b]"}`} />
          {connected ? "Подключено" : "Запускается"}
        </span>
      </div>

      <div className="mt-3 flex min-h-0 flex-1 flex-col items-center">
        <div
          ref={deviceStage}
          className="flex min-h-[340px] w-full flex-1 items-center justify-center overflow-hidden"
          data-testid="max-live-device-stage"
        >
          <div
            className="relative shrink-0"
            style={{
              width: DEVICE_WIDTH * deviceScale,
              height: DEVICE_HEIGHT * deviceScale,
            }}
          >
            <div
              className="absolute left-0 top-0 overflow-hidden rounded-[52px] border-[9px] border-[#171716] bg-[#171716] shadow-[0_24px_70px_rgba(23,23,22,.18)]"
              data-testid="max-live-device"
              style={{
                width: DEVICE_WIDTH,
                height: DEVICE_HEIGHT,
                transform: `scale(${deviceScale})`,
                transformOrigin: "top left",
              }}
            >
              <div className="absolute inset-x-0 top-0 z-10 flex h-9 items-center justify-between bg-[#171716] px-4 text-[11px] font-semibold text-white">
                <span>09:41</span>
                <span aria-hidden="true">▮▮ ◉</span>
              </div>
              {previewUrl ? (
                <iframe
                  key={previewUrl}
                  src={previewUrl}
                  title={`Превью ${project.name}`}
                  className="absolute inset-x-0 bottom-0 top-9 h-[calc(100%-2.25rem)] w-full border-0 bg-white"
                  allow="clipboard-read; clipboard-write"
                  referrerPolicy="no-referrer"
                  data-testid="max-live-iframe"
                />
              ) : (
                <div className="absolute inset-x-0 bottom-0 top-9 flex flex-col items-center justify-center bg-[#fcfbf7] px-10 text-center">
                  {preparing ? (
                    <Loader2 className="size-7 animate-spin text-[#f15a38]" />
                  ) : (
                    <Play className="size-7 text-[#f15a38]" />
                  )}
                  <p className="mt-5 text-[15px] font-medium text-[#171716]">
                    {previewError
                      ? "Не удалось открыть безопасное превью"
                      : "Подготавливаем рабочую версию"}
                  </p>
                  <p className="mt-2 text-[12px] leading-5 text-[#8d887f]">
                    {previewError
                      ? "Данные приложения не открываются без защищённой preview-сессии."
                      : "Синхронизируем приложение и запускаем изолированную preview-сессию."}
                  </p>
                  {!preparing && (
                    <button
                      type="button"
                      onClick={retryPreview}
                      className="mt-6 inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-[#d8d4cb] px-4 text-[12px] font-medium text-[#171716]"
                    >
                      <RefreshCw className="size-4" />
                      Повторить
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="shrink-0 text-center">
          <p className="mt-2 max-w-[320px] text-[10px] leading-4 text-[#8d887f]">
            Реальный viewport 390 × 844. Превью обновляется после сохранённых
            изменений агента.
          </p>
          <button
            type="button"
            onClick={() => void openSeparatePreview()}
            disabled={!connected || separatePreview.isPending}
            className="mt-1 inline-flex min-h-11 items-center gap-1.5 text-[11px] font-medium text-[#c84528] hover:underline disabled:cursor-not-allowed disabled:opacity-45"
            data-testid="max-open-preview-separate"
            title={connected ? undefined : `Публичный адрес: ${publicUrl}`}
          >
            {separatePreview.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <ExternalLink className="size-3" />
            )}
            Открыть отдельно
          </button>
        </div>
      </div>
    </aside>
  );
}
