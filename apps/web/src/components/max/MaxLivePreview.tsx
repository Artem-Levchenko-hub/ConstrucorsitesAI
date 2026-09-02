"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BatteryFull,
  Check,
  CircleAlert,
  ExternalLink,
  GitCommitHorizontal,
  Loader2,
  PanelRightClose,
  Play,
  RefreshCw,
  Signal,
  Wifi,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import {
  createMaxPreviewSession,
  syncMaxManagedKit,
} from "@/lib/api/max-studio";
import { getRuntime, startRuntime } from "@/lib/api/runtime";
import type { Project, Snapshot } from "@/lib/api/types";
import {
  maxSnapshotLabel,
  maxSnapshotVersion,
} from "@/lib/max-version-history";
import { shortSha } from "@/lib/utils";
import { MaxVersionRail } from "./MaxVersionRail";

const SCREEN_WIDTH = 390;
const SCREEN_HEIGHT = 844;
const STATUS_BAR_HEIGHT = 38;
const DEVICE_BEZEL = 10;
const DEVICE_WIDTH = SCREEN_WIDTH + DEVICE_BEZEL * 2;
const DEVICE_HEIGHT = SCREEN_HEIGHT + STATUS_BAR_HEIGHT + DEVICE_BEZEL * 2;
const PREVIEW_RETRY_LIMIT = 2;
const PREVIEW_RETRY_DELAY_MS = 1_500;

function isTransientPreviewError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.status === 0 || error.status === 409 || error.status >= 500) {
    return true;
  }
  if (error.status >= 400 && error.status < 500) {
    return false;
  }
  return (
    error.code === "conflict" ||
    error.code === "internal_error" ||
    error.code === "orchestrator_unavailable" ||
    error.code === "orchestrator_rejected"
  );
}

export function MaxLivePreview({
  project,
  snapshots,
  snapshotsLoading,
  currentSnapshotId,
  selectedSnapshotId,
  onSelectSnapshot,
  onRestoreSnapshot,
  restoringSnapshot,
  onClose,
}: {
  project: Project;
  snapshots: Snapshot[];
  snapshotsLoading: boolean;
  currentSnapshotId: string | null;
  selectedSnapshotId: string | null;
  onSelectSnapshot: (snapshotId: string | null) => void;
  onRestoreSnapshot: (snapshotId: string) => Promise<void>;
  restoringSnapshot: boolean;
  onClose?: () => void;
}) {
  const queryClient = useQueryClient();
  const started = useRef(false);
  const deviceStage = useRef<HTMLDivElement>(null);
  const previewFrame = useRef<HTMLIFrameElement>(null);
  const [deviceScale, setDeviceScale] = useState(0.72);
  const [lastWorkingUrl, setLastWorkingUrl] = useState<string | null>(null);
  const [restoreTargetId, setRestoreTargetId] = useState<string | null>(null);
  const previewTargetSnapshotId = currentSnapshotId ?? "no-current-snapshot";
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
    queryKey: ["max-managed-kit-sync", project.id, previewTargetSnapshotId],
    queryFn: () => syncMaxManagedKit(project.id),
    enabled: runtimeRunning,
    retry: (failureCount, error) =>
      failureCount < PREVIEW_RETRY_LIMIT && isTransientPreviewError(error),
    retryDelay: PREVIEW_RETRY_DELAY_MS,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const previewSession = useQuery({
    queryKey: [
      "max-preview-session",
      project.id,
      previewTargetSnapshotId,
      runtime.data?.container_name ?? null,
      managedKit.data?.synced_snapshot_id ?? null,
    ],
    queryFn: () => createMaxPreviewSession(project.id),
    enabled: runtimeRunning && managedKit.isSuccess,
    retry: (failureCount, error) =>
      failureCount < PREVIEW_RETRY_LIMIT && isTransientPreviewError(error),
    retryDelay: PREVIEW_RETRY_DELAY_MS,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const separatePreview = useMutation({
    mutationFn: () => createMaxPreviewSession(project.id),
  });
  const recoverPreview = useMutation({
    mutationFn: async () => {
      const runtimeResult = await runtime.refetch();
      let activeRuntime = runtimeResult.data ?? runtime.data ?? null;
      if (
        !activeRuntime ||
        ["stopped", "paused", "failed"].includes(activeRuntime.state)
      ) {
        activeRuntime = await start.mutateAsync();
      }
      const managedKitResult = await managedKit.refetch();
      if (!managedKitResult.data) {
        throw (
          managedKitResult.error ??
          new Error("Не удалось синхронизировать последнюю версию.")
        );
      }
      return queryClient.fetchQuery({
        queryKey: [
          "max-preview-session",
          project.id,
          previewTargetSnapshotId,
          activeRuntime?.container_name ?? null,
          managedKitResult.data.synced_snapshot_id ?? null,
        ],
        queryFn: () => createMaxPreviewSession(project.id),
        retry: (failureCount, error) =>
          failureCount < PREVIEW_RETRY_LIMIT && isTransientPreviewError(error),
        retryDelay: PREVIEW_RETRY_DELAY_MS,
        staleTime: 0,
      });
    },
    onError: (error) => {
      toast.error("Не удалось обновить превью", {
        description:
          error instanceof Error
            ? error.message
            : "Попробуйте ещё раз через пару секунд.",
      });
    },
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

  useEffect(() => {
    const syncPreviewChrome = (event: MessageEvent) => {
      if (
        event.source !== previewFrame.current?.contentWindow ||
        event.data?.type !== "omnia:inspect:ready"
      ) {
        return;
      }
      previewFrame.current?.contentWindow?.postMessage(
        { type: "omnia:preview:chrome", hideScrollbar: true },
        "*",
      );
    };

    window.addEventListener("message", syncPreviewChrome);
    return () => window.removeEventListener("message", syncPreviewChrome);
  }, []);

  // A relative same-origin fallback is both correct behind the production
  // reverse proxy and stable across SSR/hydration. Reading window.location
  // during render produced different href values on server and client.
  const apiOrigin = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
  const publicUrl = apiOrigin
    ? `${apiOrigin}/p/${project.slug}`
    : `/p/${project.slug}`;
  const previewUrl = previewSession.data?.url ?? null;
  const connected = Boolean(previewUrl ?? lastWorkingUrl);
  // A cold provision can outlive an earlier start request. Once polling sees
  // the runtime running, that old mutation error is no longer relevant and
  // must not replace the active "preparing" state with a false failure.
  const previewSessionError =
    previewSession.isError && !previewSession.data ? previewSession.error : null;
  const previewError =
    (managedKit.isError ? managedKit.error : null) ??
    previewSessionError ??
    (!runtimeRunning && start.isError ? start.error : null) ??
    (runtime.isError ? runtime.error : null);
  const preparing =
    runtime.isLoading ||
    start.isPending ||
    (runtimeRunning && (managedKit.isLoading || previewSession.isLoading));
  const showPreviewError = Boolean(previewError) && !preparing;
  const displayPreviewUrl = previewUrl ?? lastWorkingUrl;
  const selectedSnapshot = selectedSnapshotId
    ? snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) ?? null
    : null;
  const viewingHistorical = Boolean(
    selectedSnapshot && selectedSnapshot.id !== currentSnapshotId,
  );
  const selectedVersion = selectedSnapshot
    ? maxSnapshotVersion(snapshots, selectedSnapshot.id)
    : null;
  const restoreTargetSnapshot = restoreTargetId
    ? snapshots.find((snapshot) => snapshot.id === restoreTargetId) ?? null
    : null;
  const restoreTargetVersion = restoreTargetSnapshot
    ? maxSnapshotVersion(snapshots, restoreTargetSnapshot.id)
    : null;
  const preparationLabel = !runtimeRunning
    ? "Запускаем сервер приложения"
    : !managedKit.isSuccess
      ? "Синхронизируем последнюю версию"
      : "Создаём безопасную preview-сессию";
  const preparationSteps = [
    { label: "Сервер приложения", done: runtimeRunning },
    { label: "Последняя версия", done: managedKit.isSuccess },
    { label: "Безопасная сессия", done: Boolean(previewUrl) },
  ];

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
    recoverPreview.mutate();
  }

  async function confirmRestoreSnapshot() {
    if (!restoreTargetSnapshot) return;
    try {
      await onRestoreSnapshot(restoreTargetSnapshot.id);
      setRestoreTargetId(null);
    } catch {
      // The owner mutation keeps the confirmation open and shows the error.
    }
  }

  return (
    <aside
      className="flex h-full min-h-0 flex-col bg-transparent py-3 sm:py-4"
      data-testid="max-live-preview"
    >
      <div className="flex shrink-0 items-start justify-between gap-3 px-3 sm:px-5">
        <div className="min-w-0">
          <p className="omnia-kicker text-[#828491]">
            {viewingHistorical ? "История версий" : "Mobile WebView"}
          </p>
          <h2 className="mt-1 text-sm font-semibold">
            {viewingHistorical && selectedSnapshot
              ? `v${selectedVersion} · ${maxSnapshotLabel(selectedSnapshot)}`
              : "Живое превью"}
          </h2>
        </div>
        <div className="flex max-w-[62%] shrink-0 flex-wrap items-center justify-end gap-1 sm:gap-1.5">
          <span className="inline-flex items-center gap-2 text-[10px] text-[#9fa1b1]">
            <span className={`size-1.5 rounded-full ${connected ? "bg-[#248a4b]" : "bg-[#828491]"}`} />
            {connected ? "Подключено" : "Запускается"}
          </span>
          {!viewingHistorical && (
            <button
              type="button"
              onClick={retryPreview}
              disabled={recoverPreview.isPending}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-[#2b2d32] px-2.5 py-1 text-[10px] font-medium text-[#9fa1b1] transition-colors hover:bg-[#2b2d32] hover:text-white disabled:cursor-not-allowed disabled:opacity-45 sm:px-3"
              title="Обновить превью"
              aria-label="Обновить превью"
              data-testid="max-refresh-preview"
            >
              {recoverPreview.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <RefreshCw className="size-3" />
              )}
              <span className="hidden sm:inline">Обновить превью</span>
            </button>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="grid size-8 place-items-center rounded-full text-[#828491] transition-colors hover:bg-[#2b2d32] hover:text-white"
              aria-label="Скрыть панель превью"
              title="Скрыть превью"
              data-testid="max-desktop-preview-close"
            >
              <PanelRightClose className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="mt-3 flex min-h-0 flex-1">
        <MaxVersionRail
          snapshots={snapshots}
          currentSnapshotId={currentSnapshotId}
          selectedSnapshotId={selectedSnapshotId}
          loading={snapshotsLoading}
          onSelect={onSelectSnapshot}
        />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center">
        <div
          ref={deviceStage}
          className="flex min-h-[340px] w-full flex-1 items-center justify-center overflow-hidden px-1.5 sm:px-2"
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
              className="absolute left-0 top-0 rounded-[58px] bg-[#0b0b0b] p-[10px] shadow-[0_12px_28px_rgba(23,23,22,.14),0_2px_7px_rgba(23,23,22,.12),inset_0_0_0_1px_rgba(255,255,255,.16)]"
              data-testid="max-live-device"
              style={{
                width: DEVICE_WIDTH,
                height: DEVICE_HEIGHT,
                transform: `scale(${deviceScale})`,
                transformOrigin: "top left",
              }}
            >
              <span className="absolute -left-[3px] top-[154px] h-[76px] w-[4px] rounded-l-full bg-[#30302f] shadow-[inset_1px_0_rgba(255,255,255,.16)]" aria-hidden="true" />
              <span className="absolute -left-[3px] top-[242px] h-[46px] w-[4px] rounded-l-full bg-[#30302f] shadow-[inset_1px_0_rgba(255,255,255,.16)]" aria-hidden="true" />
              <span className="absolute -right-[3px] top-[196px] h-[104px] w-[4px] rounded-r-full bg-[#30302f] shadow-[inset_-1px_0_rgba(255,255,255,.16)]" aria-hidden="true" />

              <div className="relative h-full overflow-hidden rounded-[48px] bg-[#111] shadow-[inset_0_0_0_1px_rgba(255,255,255,.08)]">
                <div className="relative z-10 flex items-center justify-between bg-[#111] px-[19px] text-[11px] font-semibold text-white" style={{ height: STATUS_BAR_HEIGHT }}>
                  <span className="min-w-[58px] tracking-[-0.02em]">09:41</span>
                  <span className="absolute left-1/2 top-[8px] h-[22px] w-[82px] -translate-x-1/2 rounded-full bg-black shadow-[inset_0_0_0_1px_rgba(255,255,255,.03)]" aria-hidden="true" />
                  <span className="flex min-w-[58px] items-center justify-end gap-1.5" aria-hidden="true">
                    <Signal className="size-3" strokeWidth={2.5} />
                    <Wifi className="size-3" strokeWidth={2.5} />
                    <BatteryFull className="h-3 w-4" strokeWidth={2.25} />
                  </span>
                </div>
                <div className="relative bg-white" style={{ width: SCREEN_WIDTH, height: SCREEN_HEIGHT }}>
                  {viewingHistorical && selectedSnapshot ? (
                    <div
                      className="absolute inset-0 bg-[#191b20]"
                      data-testid="max-historical-snapshot"
                    >
                      {selectedSnapshot.preview_url ? (
                        // Snapshot thumbnails are immutable remote render artifacts.
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={selectedSnapshot.preview_url}
                          alt={`Снимок версии v${selectedVersion}`}
                          className="size-full object-cover object-top"
                        />
                      ) : (
                        <div className="flex size-full flex-col items-center justify-center px-10 text-center">
                          <GitCommitHorizontal className="size-7 text-accent" />
                          <p className="mt-5 text-[15px] font-medium text-white">
                            Версия сохранена
                          </p>
                          <p className="mt-2 text-[12px] leading-5 text-[#828491]">
                            Изображение этой сборки не сохранилось, но её можно безопасно восстановить.
                          </p>
                        </div>
                      )}
                      <div className="absolute inset-x-3 top-3 rounded-[10px] border border-accent/25 bg-[#191b20]/95 px-3 py-2 text-left shadow-sm backdrop-blur">
                        <p className="text-[10px] font-semibold text-accent">
                          Версия v{selectedVersion} · только просмотр
                        </p>
                        <p className="mt-0.5 truncate text-[9px] text-[#9fa1b1]">
                          {maxSnapshotLabel(selectedSnapshot)}
                        </p>
                      </div>
                    </div>
                  ) : displayPreviewUrl ? (
                    <>
                      <iframe
                      ref={previewFrame}
                      key={displayPreviewUrl}
                      src={displayPreviewUrl}
                      title={`Превью ${project.name}`}
                      className="absolute inset-0 size-full border-0 bg-white"
                      allow="clipboard-read; clipboard-write"
                      referrerPolicy="no-referrer"
                      data-testid="max-live-iframe"
                      onLoad={(event) => {
                        if (previewUrl) setLastWorkingUrl(previewUrl);
                        event.currentTarget.contentWindow?.postMessage(
                          { type: "omnia:preview:chrome", hideScrollbar: true },
                          "*",
                        );
                      }}
                      />
                      {!previewUrl && preparing && (
                        <div className="absolute inset-x-3 top-3 z-20 rounded-[10px] border border-[#2b2d32] bg-[#191b20]/95 px-3 py-2 text-left shadow-sm backdrop-blur">
                          <p className="flex items-center gap-2 text-[11px] font-medium text-white">
                            <Loader2 className="size-3 animate-spin text-[#4f81f7]" />
                            {preparationLabel}
                          </p>
                          <p className="mt-1 text-[9px] text-[#828491]">
                            Пока показываем последнюю рабочую версию.
                          </p>
                        </div>
                      )}
                      {!previewUrl && showPreviewError && (
                        <div className="absolute inset-x-3 top-3 z-20 rounded-[10px] border border-[#c63d35]/25 bg-[#191b20]/95 px-3 py-2 text-left shadow-sm backdrop-blur">
                          <p className="flex items-center gap-2 text-[11px] font-medium text-white">
                            <CircleAlert className="size-3 text-danger-fg" />
                            Новая версия не открылась
                          </p>
                          <button
                            type="button"
                            onClick={retryPreview}
                            disabled={recoverPreview.isPending}
                            className="mt-1 text-[10px] font-medium text-[#6a95fa]"
                          >
                            {recoverPreview.isPending ? "Обновляем…" : "Повторить проверку"}
                          </button>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#191b20] px-10 text-center">
                      {preparing ? (
                        <Loader2 className="size-7 animate-spin text-[#4f81f7]" />
                      ) : (
                        <Play className="size-7 text-[#4f81f7]" />
                      )}
                      <p className="mt-5 text-[15px] font-medium text-white">
                        {showPreviewError
                          ? "Превью пока недоступно"
                          : preparationLabel}
                      </p>
                      <p className="mt-2 text-[12px] leading-5 text-[#828491]">
                        {showPreviewError
                          ? "Omnia не смогла создать защищённую сессию. Данные приложения не раскрыты."
                          : "Обычно подготовка занимает от 15 до 60 секунд."}
                      </p>
                      {!showPreviewError && (
                        <ol className="mt-5 w-full space-y-2 text-left">
                          {preparationSteps.map((step) => (
                            <li key={step.label} className="flex items-center gap-2 text-[10px] text-[#9fa1b1]">
                              <span className={`grid size-4 place-items-center rounded-full border ${step.done ? "border-[#248a4b] bg-[#248a4b]/10 text-success-fg" : "border-[#2b2d32] text-[#828491]"}`}>
                                {step.done ? <Check className="size-2.5" /> : <span className="size-1 rounded-full bg-current" />}
                              </span>
                              {step.label}
                            </li>
                          ))}
                        </ol>
                      )}
                      {showPreviewError && (
                        <div className="mt-6 flex flex-col items-center gap-2">
                          <button
                            type="button"
                            onClick={retryPreview}
                            disabled={recoverPreview.isPending}
                            className="inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-[#2b2d32] px-4 text-[12px] font-medium text-white"
                          >
                            {recoverPreview.isPending ? (
                              <Loader2 className="size-4 animate-spin" />
                            ) : (
                              <RefreshCw className="size-4" />
                            )}
                            {recoverPreview.isPending ? "Обновляем…" : "Повторить"}
                          </button>
                          <p className="text-[9px] leading-4 text-[#828491]">
                            Если ошибка повторяется, откройте панель запуска — там указан ответственный шаг.
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="pointer-events-none absolute inset-0 rounded-[48px] ring-1 ring-inset ring-white/10" aria-hidden="true" />
              </div>
            </div>
          </div>
        </div>
        <div className="shrink-0 text-center">
          {viewingHistorical && selectedSnapshot ? (
            <div className="mt-1 flex min-h-11 items-center justify-center gap-1.5 px-2">
              <button
                type="button"
                onClick={() => onSelectSnapshot(null)}
                disabled={restoringSnapshot}
                className="inline-flex min-h-11 items-center rounded-[9px] px-2.5 text-[10px] font-medium text-[#9fa1b1] hover:bg-[#2b2d32] hover:text-white disabled:opacity-45"
                data-testid="max-return-current-version"
              >
                Текущая
              </button>
              <button
                type="button"
                onClick={() => setRestoreTargetId(selectedSnapshot.id)}
                disabled={restoringSnapshot}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-[9px] border border-accent/30 bg-accent/10 px-3 text-[10px] font-semibold text-accent hover:bg-accent/15 disabled:opacity-45"
                data-testid="max-restore-version"
              >
                {restoringSnapshot && <Loader2 className="size-3 animate-spin" />}
                Восстановить v{selectedVersion}
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => void openSeparatePreview()}
              disabled={!connected || separatePreview.isPending}
              className="mt-1 inline-flex min-h-9 items-center gap-1.5 text-[10px] font-medium text-[#828491] transition-colors hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
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
          )}
        </div>
        </div>
      </div>
      <Dialog
        open={Boolean(
          restoreTargetSnapshot && restoreTargetId === selectedSnapshotId,
        )}
        onOpenChange={(open) => {
          if (!restoringSnapshot && !open) setRestoreTargetId(null);
        }}
      >
        <DialogContent className="border-[#2b2d32] bg-[#191b20] text-white">
          <DialogHeader>
            <DialogTitle>Вернуться к версии v{restoreTargetVersion}?</DialogTitle>
            <DialogDescription className="leading-6 text-[#9fa1b1]">
              Создадим новую текущую версию на основе{" "}
              <span className="font-mono text-white">
                {restoreTargetSnapshot
                  ? shortSha(restoreTargetSnapshot.commit_sha)
                  : ""}
              </span>
              . Нынешняя версия останется в истории.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setRestoreTargetId(null)}
              disabled={restoringSnapshot}
            >
              Отмена
            </Button>
            <Button
              type="button"
              onClick={() => void confirmRestoreSnapshot()}
              disabled={restoringSnapshot || !restoreTargetSnapshot}
            >
              {restoringSnapshot && <Loader2 className="animate-spin" />}
              Вернуться к версии
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  );
}
