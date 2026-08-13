"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BatteryFull,
  Check,
  CircleAlert,
  ExternalLink,
  GitCommitHorizontal,
  Loader2,
  MousePointer2,
  PanelRightClose,
  Pencil,
  Play,
  RefreshCw,
  Signal,
  Sparkles,
  Wifi,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { JoyBurst } from "@/components/workspace/JoyBurst";
import { StylePanel } from "@/components/workspace/StylePanel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  createMaxPreviewSession,
  syncMaxManagedKit,
} from "@/lib/api/max-studio";
import {
  getRuntime,
  heartbeatRuntime,
  startRuntime,
} from "@/lib/api/runtime";
import type { Project, Snapshot } from "@/lib/api/types";
import {
  editorModeMessages,
  previewTargetOrigin,
  stopEditorPickingAfterPick,
  type EditorMode,
} from "@/lib/editor-bridge";
import { maxSnapshotLabel, maxSnapshotVersion } from "@/lib/max-version-history";
import { cn, shortSha } from "@/lib/utils";
import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";
import { MaxVersionRail } from "./MaxVersionRail";

const SCREEN_WIDTH = 390;
const SCREEN_HEIGHT = 844;
const STATUS_BAR_HEIGHT = 38;
const DEVICE_BEZEL = 10;
const DEVICE_WIDTH = SCREEN_WIDTH + DEVICE_BEZEL * 2;
const DEVICE_HEIGHT = SCREEN_HEIGHT + STATUS_BAR_HEIGHT + DEVICE_BEZEL * 2;

export function MaxLivePreview({
  project,
  deferInitialRuntimeStart = false,
  snapshots,
  snapshotsLoading,
  currentSnapshotId,
  selectedSnapshotId,
  historicalSessionUrl,
  historicalSessionLoading,
  onSelectSnapshot,
  onRestoreSnapshot,
  restoringSnapshot,
  onClose,
}: {
  project: Project;
  deferInitialRuntimeStart?: boolean;
  snapshots: Snapshot[];
  snapshotsLoading: boolean;
  currentSnapshotId: string | null;
  selectedSnapshotId: string | null;
  historicalSessionUrl: string | null;
  historicalSessionLoading: boolean;
  onSelectSnapshot: (snapshotId: string | null) => void;
  onRestoreSnapshot: (snapshotId: string) => Promise<void>;
  restoringSnapshot: boolean;
  onClose?: () => void;
}) {
  const queryClient = useQueryClient();
  const editorInstanceId = useId();
  const selectionIdPrefix = `${editorInstanceId}|`;
  const autoStartAttempted = useRef(false);
  const deviceStage = useRef<HTMLDivElement>(null);
  const previewFrame = useRef<HTMLIFrameElement>(null);
  const historicalPreviewFrame = useRef<HTMLIFrameElement>(null);
  const previousPickIds = useRef<string[]>([]);
  const lastHeartbeatAt = useRef(0);
  const [deviceScale, setDeviceScale] = useState(0.72);
  const [lastWorkingUrl, setLastWorkingUrl] = useState<string | null>(null);
  const [loadedPreviewUrl, setLoadedPreviewUrl] = useState<string | null>(null);
  const [inspectorReady, setInspectorReady] = useState(false);
  const [loadedHistoricalSessionUrl, setLoadedHistoricalSessionUrl] = useState<
    string | null
  >(null);
  const [restoreTarget, setRestoreTarget] = useState<{
    snapshotId: string;
    headId: string | null;
  } | null>(null);
  const inspectMode = useInspectorStore((state) => state.inspectMode);
  const setInspectMode = useInspectorStore((state) => state.setInspectMode);
  const addSelection = useInspectorStore((state) => state.addSelection);
  const selections = useInspectorStore((state) => state.selections);
  const styleMode = useStyleEditStore((state) => state.styleMode);
  const setStyleMode = useStyleEditStore((state) => state.setStyleMode);
  const stopStylePicking = useStyleEditStore(
    (state) => state.stopStylePicking,
  );
  const styleSelected = useStyleEditStore((state) => state.selected);
  const activeEditorMode: EditorMode = styleMode
    ? "style"
    : inspectMode
      ? "inspect"
      : "off";
  const selectedSnapshot = selectedSnapshotId
    ? snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) ?? null
    : null;
  const viewingHistorical = Boolean(
    selectedSnapshot && selectedSnapshot.id !== currentSnapshotId,
  );
  const selectedVersion = selectedSnapshot
    ? maxSnapshotVersion(snapshots, selectedSnapshot.id)
    : null;
  const restoreTargetId =
    restoreTarget?.headId === currentSnapshotId
      ? restoreTarget.snapshotId
      : null;
  const restoreTargetSnapshot = restoreTargetId
    ? snapshots.find((snapshot) => snapshot.id === restoreTargetId) ?? null
    : null;
  const restoreTargetVersion = restoreTargetSnapshot
    ? maxSnapshotVersion(snapshots, restoreTargetSnapshot.id)
    : null;

  const historicalSessionReady = Boolean(
    historicalSessionUrl && loadedHistoricalSessionUrl === historicalSessionUrl,
  );
  useEffect(() => {
    if (!historicalSessionUrl) return;
    const expectedOrigin = previewTargetOrigin(
      historicalSessionUrl,
      window.location.origin,
    );
    if (!expectedOrigin) return;

    function onHistoricalReady(event: MessageEvent) {
      if (
        event.source !== historicalPreviewFrame.current?.contentWindow ||
        event.origin !== expectedOrigin
      ) {
        return;
      }
      const data = event.data as { type?: string } | null;
      if (data?.type === "omnia:inspect:ready") {
        setLoadedHistoricalSessionUrl(historicalSessionUrl);
      }
    }

    window.addEventListener("message", onHistoricalReady);
    return () => window.removeEventListener("message", onHistoricalReady);
  }, [historicalSessionUrl]);
  const postToPreview = useCallback((message: Record<string, unknown>) => {
    const frame = previewFrame.current;
    if (!frame?.contentWindow) return;
    const targetOrigin = previewTargetOrigin(frame.src, window.location.origin);
    if (targetOrigin) frame.contentWindow.postMessage(message, targetOrigin);
  }, []);
  const sendRuntimeHeartbeat = useCallback(() => {
    const now = Date.now();
    if (now - lastHeartbeatAt.current < 10_000) return;
    lastHeartbeatAt.current = now;
    void heartbeatRuntime(project.id).catch(() => {
      // The status poll below owns recovery. A transient keepalive failure must
      // never block normal interactions inside the generated application.
    });
  }, [project.id]);
  const postToAllProjectPreviews = useCallback(
    (message: Record<string, unknown>) => {
      document
        .querySelectorAll<HTMLIFrameElement>(
          'iframe[data-testid="max-live-iframe"]',
        )
        .forEach((frame) => {
          if (
            frame.dataset.maxProjectId !== project.id ||
            frame.dataset.maxPreviewReady !== "true" ||
            !frame.contentWindow
          ) {
            return;
          }
          const targetOrigin = previewTargetOrigin(
            frame.src,
            window.location.origin,
          );
          if (targetOrigin) {
            frame.contentWindow.postMessage(message, targetOrigin);
          }
        });
    },
    [project.id],
  );
  const syncEditorMode = useCallback(() => {
    editorModeMessages(activeEditorMode).forEach(postToPreview);
  }, [activeEditorMode, postToPreview]);
  const replayPendingStyles = useCallback(() => {
    const propNames = {
      color: "color",
      background_color: "background-color",
      border_color: "border-color",
    } as const;
    const { elements } = useStyleEditStore.getState();
    Object.entries(elements).forEach(([selector, edit]) => {
      Object.entries(propNames).forEach(([key, prop]) => {
        const value = edit[key as keyof typeof propNames];
        if (!value) return;
        postToPreview({
          type: "omnia:style:set",
          target: "element",
          selector,
          prop,
          value,
        });
      });
    });
  }, [postToPreview]);
  const selectEditorMode = useCallback(
    (mode: EditorMode) => {
      setStyleMode(mode === "style");
      setInspectMode(mode === "inspect");
    },
    [setInspectMode, setStyleMode],
  );

  useEffect(() => {
    if (!viewingHistorical) return;
    selectEditorMode("off");
  }, [selectEditorMode, viewingHistorical]);

  useEffect(() => {
    if (
      selectedSnapshotId &&
      !snapshotsLoading &&
      !snapshots.some((snapshot) => snapshot.id === selectedSnapshotId)
    ) {
      onSelectSnapshot(null);
    }
  }, [
    onSelectSnapshot,
    selectedSnapshotId,
    snapshots,
    snapshotsLoading,
  ]);
  const runtime = useQuery({
    queryKey: ["runtime", project.id],
    queryFn: () => getRuntime(project.id),
    retry: false,
    // Keep observing a mounted preview even after it reaches `running`.
    // Otherwise a later idle-stop is invisible to this shell forever.
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "running" || state === "failed" ? 30_000 : 2_000;
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
    enabled: runtimeRunning && !deferInitialRuntimeStart,
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
    if (deferInitialRuntimeStart) return;
    if (runtime.isLoading || start.isPending) return;
    if (runtime.data?.state === "running") {
      autoStartAttempted.current = false;
      return;
    }
    if (
      !autoStartAttempted.current &&
      (runtime.isError ||
        !runtime.data ||
        ["stopped", "paused", "failed"].includes(runtime.data.state))
    ) {
      autoStartAttempted.current = true;
      start.mutate();
    }
  }, [
    deferInitialRuntimeStart,
    runtime.isLoading,
    runtime.isError,
    runtime.data,
    start,
    start.isPending,
  ]);

  // An SPA tab/button click can be a completely local state transition, so
  // Docker RX counters cannot distinguish an active viewer from an abandoned
  // iframe.  Send an authenticated liveness signal while this preview is
  // mounted, plus an immediate signal for each real interaction.  The latter
  // also covers background-tab timer throttling.
  useEffect(() => {
    if (deferInitialRuntimeStart || !runtimeRunning || viewingHistorical) return;
    sendRuntimeHeartbeat();
    const interval = window.setInterval(sendRuntimeHeartbeat, 60_000);
    return () => {
      window.clearInterval(interval);
    };
  }, [
    deferInitialRuntimeStart,
    runtimeRunning,
    sendRuntimeHeartbeat,
    viewingHistorical,
  ]);

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
  const connected = Boolean(previewUrl ?? lastWorkingUrl);
  // A cold provision can outlive an earlier start request. Once polling sees
  // the runtime running, that old mutation error is no longer relevant and
  // must not replace the active "preparing" state with a false failure.
  const previewError =
    (managedKit.isError ? managedKit.error : null) ??
    (previewSession.isError ? previewSession.error : null) ??
    (!runtimeRunning && start.isError ? start.error : null) ??
    (runtime.isError ? runtime.error : null);
  const preparing =
    runtime.isLoading ||
    start.isPending ||
    (runtimeRunning && (managedKit.isLoading || previewSession.isLoading));
  const showPreviewError = Boolean(previewError) && !preparing;
  const displayPreviewUrl = previewUrl ?? lastWorkingUrl;
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

  // Mode changes happen long after the iframe's initial load. Gate the sync by
  // the URL that actually completed loading so exact-origin postMessage never
  // targets the temporary about:blank document.
  useEffect(() => {
    if (!displayPreviewUrl || loadedPreviewUrl !== displayPreviewUrl) return;
    syncEditorMode();
    const retries = [120, 450, 1_100].map((delay) =>
      window.setTimeout(syncEditorMode, delay),
    );
    return () => retries.forEach(window.clearTimeout);
  }, [displayPreviewUrl, loadedPreviewUrl, syncEditorMode]);

  // One strict message boundary serves both editor paths. AI picks become
  // commentable chips in the existing MAX composer; manual picks open the
  // existing no-LLM StylePanel with the element's computed/source metadata.
  useEffect(() => {
    function onPreviewMessage(event: MessageEvent) {
      const frame = previewFrame.current;
      const frameWindow = frame?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) return;
      const expectedOrigin = previewTargetOrigin(
        frame.src,
        window.location.origin,
      );
      if (!expectedOrigin || event.origin !== expectedOrigin) return;

      const data = event.data as {
        type?: string;
        el?: Record<string, unknown>;
      };
      if (!data || typeof data.type !== "string") return;
      if (data.type === "omnia:preview:activity") {
        sendRuntimeHeartbeat();
        return;
      }
      if (data.type === "omnia:inspect:ready") {
        frame.dataset.maxPreviewReady = "true";
        setInspectorReady(true);
        postToPreview({ type: "omnia:preview:chrome", hideScrollbar: true });
        syncEditorMode();
        replayPendingStyles();
        return;
      }
      if (data.type !== "omnia:pick" || !data.el) return;

      const element = data.el;
      const selector = String(element.selector ?? "");
      if (!selector) return;
      const pickedMode: EditorMode = useStyleEditStore.getState().styleMode
        ? "style"
        : useInspectorStore.getState().inspectMode
          ? "inspect"
          : "off";
      if (pickedMode === "off") return;
      if (pickedMode === "style") {
        useStyleEditStore.getState().selectElement({
          selector,
          tag: String(element.tag ?? ""),
          color: String(element.color ?? ""),
          backgroundColor: String(element.backgroundColor ?? ""),
          borderColor: String(element.borderColor ?? ""),
          fontFamily: String(element.fontFamily ?? ""),
          src: String(element.src ?? ""),
          srcs: Array.isArray(element.srcs) ? element.srcs.map(String) : [],
          editableText: Boolean(element.editableText),
          editText: String(element.editText ?? ""),
          textIndex:
            typeof element.textIndex === "number" ? element.textIndex : 0,
          outerHTML: String(element.outerHTML ?? ""),
          htmlIndex:
            typeof element.htmlIndex === "number" ? element.htmlIndex : 0,
          prevHTML: String(element.prevHTML ?? ""),
          prevIndex:
            typeof element.prevIndex === "number" ? element.prevIndex : 0,
          nextHTML: String(element.nextHTML ?? ""),
          nextIndex:
            typeof element.nextIndex === "number" ? element.nextIndex : 0,
        });
        stopEditorPickingAfterPick("style", {
          setInspectMode,
          stopStylePicking,
          postMessage: postToAllProjectPreviews,
        });
        return;
      }

      const rawId = String(element.id ?? "");
      if (!rawId) return;
      const alreadySelected = useInspectorStore
        .getState()
        .selections.some((selection) => selection.selector === selector);
      if (alreadySelected) {
        postToPreview({ type: "omnia:inspect:remove", id: rawId });
        stopEditorPickingAfterPick("inspect", {
          setInspectMode,
          stopStylePicking,
          postMessage: postToAllProjectPreviews,
        });
        return;
      }
      const id = `${selectionIdPrefix}${rawId}`;
      addSelection({
        id,
        selector,
        label: element.label ? String(element.label) : null,
        text: element.text ? String(element.text) : null,
        html: element.html ? String(element.html) : null,
        comment: "",
      });
      toast.success("Элемент добавлен в правку", {
        description:
          "Опишите изменение в чате — ИИ затронет только выделенное.",
      });
      stopEditorPickingAfterPick("inspect", {
        setInspectMode,
        stopStylePicking,
        postMessage: postToAllProjectPreviews,
      });
    }

    window.addEventListener("message", onPreviewMessage);
    return () => window.removeEventListener("message", onPreviewMessage);
  }, [
    addSelection,
    postToAllProjectPreviews,
    postToPreview,
    replayPendingStyles,
    sendRuntimeHeartbeat,
    selectionIdPrefix,
    setInspectMode,
    stopStylePicking,
    syncEditorMode,
  ]);

  // A single-shot pick leaves its selected mark and panel visible while capture
  // mode is off. Clear the mark only when that panel is explicitly closed (or
  // an explicit mode change clears the selection), not on the automatic stop.
  const previousEditorMode = useRef<EditorMode>("off");
  const hadStyleSelection = useRef(false);
  useEffect(() => {
    const leftStyleMode =
      previousEditorMode.current === "style" &&
      activeEditorMode !== "style" &&
      !styleSelected;
    const closedStylePanel = hadStyleSelection.current && !styleSelected;
    if (leftStyleMode || closedStylePanel) {
      postToPreview({ type: "omnia:inspect:clear" });
    }
    previousEditorMode.current = activeEditorMode;
    hadStyleSelection.current = Boolean(styleSelected);
  }, [activeEditorMode, postToPreview, styleSelected]);

  // Removing a chip (or sending the prompt) must remove the matching outline in
  // every mounted MAX preview, including the responsive drawer instance.
  useEffect(() => {
    const current = selections
      .map((selection) => selection.id)
      .filter((id) => id.startsWith(selectionIdPrefix));
    const removed = previousPickIds.current.filter(
      (id) => !current.includes(id),
    );
    if (removed.length > 0) {
      if (current.length === 0) {
        postToPreview({ type: "omnia:inspect:clear" });
      } else {
        removed.forEach((id) => {
          const rawId = id.slice(selectionIdPrefix.length);
          postToPreview({ type: "omnia:inspect:remove", id: rawId });
        });
      }
    }
    previousPickIds.current = current;
  }, [postToPreview, selectionIdPrefix, selections]);

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

  async function confirmRestoreSnapshot() {
    if (!restoreTargetSnapshot) return;
    try {
      await onRestoreSnapshot(restoreTargetSnapshot.id);
      setRestoreTarget(null);
    } catch {
      // The shell keeps the dialog open and reports the API error in a toast so
      // a temporary failure can be retried without losing the chosen version.
    }
  }

  return (
    <aside
      className="relative flex h-full min-h-0 flex-col bg-transparent py-3 sm:py-4"
      data-testid="max-live-preview"
    >
      <JoyBurst projectId={project.id} label="Готово — приложение ожило" />
      <div className="flex shrink-0 items-center justify-between gap-3 px-3 sm:px-5">
        <h2 className="text-xs font-semibold">Превью</h2>
        <div className="flex items-center gap-1.5">
          <span
            className="grid size-6 place-items-center"
            aria-label={connected ? "Превью подключено" : "Превью запускается"}
            title={connected ? "Подключено" : "Запускается"}
          >
            <span className={`size-1.5 rounded-full ${connected ? "bg-[#248a4b]" : "bg-[#aaa59b]"}`} />
            <span className="sr-only">
              {connected ? "Подключено" : "Запускается"}
            </span>
          </span>
          <MaxEditMenu
            mode={activeEditorMode}
            disabled={!displayPreviewUrl || viewingHistorical}
            selectionCount={selections.length}
            onModeChange={selectEditorMode}
          />
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="grid size-11 place-items-center rounded-full text-[#8d887f] transition-colors hover:bg-[#ece8df] hover:text-[#171716]"
              aria-label="Скрыть панель превью"
              title="Скрыть превью"
              data-testid="max-desktop-preview-close"
            >
              <PanelRightClose className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="mt-3 flex min-h-0 flex-1 flex-col items-center">
        <div className="flex min-h-[340px] w-full min-w-0 flex-1 overflow-hidden px-1 sm:px-1.5">
          <MaxVersionRail
            snapshots={snapshots}
            currentSnapshotId={currentSnapshotId}
            selectedSnapshotId={selectedSnapshotId}
            loading={snapshotsLoading}
            onSelect={onSelectSnapshot}
          />
          <div
            ref={deviceStage}
            className="flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-hidden"
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
                      className="absolute inset-0 bg-[#f5f3ee]"
                      data-testid="max-historical-snapshot"
                    >
                      <span className="absolute left-3 top-3 z-10 inline-flex items-center gap-1.5 rounded-full border border-[#d8d4cb] bg-[#fcfbf7]/95 px-2.5 py-1 text-[10px] font-medium text-[#6d6962] shadow-sm backdrop-blur">
                        <GitCommitHorizontal className="size-3 text-accent" />
                        Версия v{selectedVersion} · изолированная сессия
                      </span>
                      {selectedSnapshot.preview_url ? (
                        <div className="absolute inset-0 overflow-hidden bg-white">
                          {/* The immutable PNG appears immediately. A single
                              isolated interactive sandbox fades over it only
                              after the exact historical commit is ready. */}
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={selectedSnapshot.preview_url}
                            alt={`Снимок версии ${selectedVersion}: ${maxSnapshotLabel(selectedSnapshot)}`}
                            className={cn(
                              "absolute inset-0 size-full object-cover object-top transition-opacity duration-300 ease-out motion-reduce:transition-none",
                              historicalSessionReady ? "opacity-0" : "opacity-100",
                            )}
                          />
                        </div>
                      ) : (
                        <div className="grid size-full place-items-center bg-[#fcfbf7] px-8 text-center">
                          <div>
                            <GitCommitHorizontal className="mx-auto size-6 text-[#aaa59b]" />
                            <p className="mt-3 text-[13px] font-medium text-[#171716]">
                              Снимок ещё готовится
                            </p>
                            <p className="mt-1 text-[10px] leading-4 text-[#8d887f]">
                              Версию уже можно восстановить. Изображение появится после обработки.
                            </p>
                          </div>
                        </div>
                      )}
                      {historicalSessionUrl && (
                        <iframe
                          ref={historicalPreviewFrame}
                          key={historicalSessionUrl}
                          src={historicalSessionUrl}
                          title={`Интерактивная версия ${selectedVersion}`}
                          className={cn(
                            "absolute inset-0 size-full border-0 bg-white transition-[opacity,transform] duration-300 ease-[cubic-bezier(.22,1,.36,1)] motion-reduce:transition-none",
                            historicalSessionReady
                              ? "pointer-events-auto translate-y-0 opacity-100"
                              : "pointer-events-none translate-y-1 opacity-0",
                          )}
                          allow="clipboard-read; clipboard-write"
                          referrerPolicy="no-referrer"
                          data-testid="max-historical-iframe"
                          onLoad={(event) => {
                            const frame = event.currentTarget;
                            const targetOrigin = previewTargetOrigin(
                              frame.src,
                              window.location.origin,
                            );
                            if (!targetOrigin || !frame.contentWindow) return;
                            [0, 160, 640, 1_400].forEach((delay) => {
                              window.setTimeout(() => {
                                frame.contentWindow?.postMessage(
                                  { type: "omnia:inspect:ping" },
                                  targetOrigin,
                                );
                              }, delay);
                            });
                          }}
                        />
                      )}
                      {(historicalSessionLoading || historicalSessionUrl) &&
                        !historicalSessionReady && (
                        <span className="pointer-events-none absolute bottom-3 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-[#d8d4cb] bg-[#fcfbf7]/92 px-2.5 py-1 text-[9px] font-medium text-[#6d6962] shadow-sm backdrop-blur">
                          <Loader2 className="size-2.5 animate-spin text-accent motion-reduce:animate-none" />
                          Открываем интерактивную версию
                        </span>
                        )}
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
                      data-max-project-id={project.id}
                      data-max-preview-ready={
                        loadedPreviewUrl === displayPreviewUrl && inspectorReady
                          ? "true"
                          : "false"
                      }
                      onLoad={(event) => {
                        event.currentTarget.dataset.maxPreviewReady = "false";
                        setInspectorReady(false);
                        if (previewUrl) setLastWorkingUrl(previewUrl);
                        if (displayPreviewUrl) {
                          setLoadedPreviewUrl(displayPreviewUrl);
                        }
                        postToPreview({
                          type: "omnia:preview:chrome",
                          hideScrollbar: true,
                        });
                      }}
                      />
                      {!previewUrl && preparing && (
                        <div className="absolute inset-x-3 top-3 z-20 rounded-[10px] border border-[#d8d4cb] bg-[#fcfbf7]/95 px-3 py-2 text-left shadow-sm backdrop-blur">
                          <p className="flex items-center gap-2 text-[11px] font-medium text-[#171716]">
                            <Loader2 className="size-3 animate-spin text-accent" />
                            {preparationLabel}
                          </p>
                          <p className="mt-1 text-[9px] text-[#8d887f]">
                            Пока показываем последнюю рабочую версию.
                          </p>
                        </div>
                      )}
                      {!previewUrl && showPreviewError && (
                        <div className="absolute inset-x-3 top-3 z-20 rounded-[10px] border border-[#c63d35]/25 bg-[#fcfbf7]/95 px-3 py-2 text-left shadow-sm backdrop-blur">
                          <p className="flex items-center gap-2 text-[11px] font-medium text-[#171716]">
                            <CircleAlert className="size-3 text-[#c63d35]" />
                            Новая версия не открылась
                          </p>
                          <button
                            type="button"
                            onClick={retryPreview}
                            className="mt-1 text-[10px] font-medium text-accent"
                          >
                            Повторить проверку
                          </button>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#fcfbf7] px-10 text-center">
                      {preparing ? (
                        <Loader2 className="size-7 animate-spin text-accent" />
                      ) : (
                        <Play className="size-7 text-accent" />
                      )}
                      <p className="mt-5 text-[15px] font-medium text-[#171716]">
                        {showPreviewError
                          ? "Превью пока недоступно"
                          : preparationLabel}
                      </p>
                      <p className="mt-2 text-[12px] leading-5 text-[#8d887f]">
                        {showPreviewError
                          ? "Omnia не смогла создать защищённую сессию. Данные приложения не раскрыты."
                          : "Обычно подготовка занимает от 15 до 60 секунд."}
                      </p>
                      {!showPreviewError && (
                        <ol className="mt-5 w-full space-y-2 text-left">
                          {preparationSteps.map((step) => (
                            <li key={step.label} className="flex items-center gap-2 text-[10px] text-[#6d6962]">
                              <span className={`grid size-4 place-items-center rounded-full border ${step.done ? "border-[#248a4b] bg-[#248a4b]/10 text-[#248a4b]" : "border-[#d8d4cb] text-[#aaa59b]"}`}>
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
                            className="inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-[#d8d4cb] px-4 text-[12px] font-medium text-[#171716]"
                          >
                            <RefreshCw className="size-4" />
                            Повторить
                          </button>
                          <p className="text-[9px] leading-4 text-[#aaa59b]">
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
        </div>
        <div className="shrink-0 text-center">
          {viewingHistorical && selectedSnapshot ? (
            <div className="mt-1 flex min-h-11 items-center justify-center gap-1.5 px-2">
              <button
                type="button"
                onClick={() => onSelectSnapshot(null)}
                disabled={restoringSnapshot}
                className="inline-flex min-h-11 items-center rounded-[9px] px-2.5 text-[10px] font-medium text-[#6d6962] transition-colors hover:bg-[#f5f3ee] hover:text-[#171716] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-45"
                data-testid="max-return-current-version"
              >
                Текущая
              </button>
              <button
                type="button"
                onClick={() =>
                  setRestoreTarget({
                    snapshotId: selectedSnapshot.id,
                    headId: currentSnapshotId,
                  })
                }
                disabled={restoringSnapshot}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-[9px] border border-accent/30 bg-accent/10 px-3 text-[10px] font-semibold text-accent transition-colors hover:border-accent/45 hover:bg-accent/15 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-45"
                data-testid="max-restore-version"
              >
                {restoringSnapshot && (
                  <Loader2 className="size-3 animate-spin" />
                )}
                Восстановить v{selectedVersion}
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => void openSeparatePreview()}
              disabled={!connected || separatePreview.isPending}
              className="mt-1 inline-flex min-h-9 items-center gap-1.5 text-[10px] font-medium text-[#8d887f] transition-colors hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
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
      {!viewingHistorical && styleSelected && (
        <StylePanel
          projectId={project.id}
          post={postToAllProjectPreviews}
          sourceEditing={false}
          fontEditing={false}
          tokenEditing={false}
        />
      )}
      <Dialog
        open={Boolean(
          restoreTargetSnapshot &&
            restoreTargetId === selectedSnapshotId &&
            viewingHistorical,
        )}
        onOpenChange={(open) => {
          if (!restoringSnapshot && !open) setRestoreTarget(null);
        }}
      >
        <DialogContent className="border-[#d8d4cb] bg-[#fcfbf7] text-[#171716] shadow-[0_30px_90px_rgba(23,23,22,.28)] [&>button]:text-[#8d887f] [&>button:hover]:bg-[#ece8df]">
          <DialogHeader>
            <DialogTitle className="text-[#171716]">
              Вернуться к версии v{restoreTargetVersion}?
            </DialogTitle>
            <DialogDescription className="leading-6 text-[#6d6962]">
              Создадим новую версию на основе{" "}
              <span className="font-mono text-[#171716]">
                {restoreTargetSnapshot
                  ? shortSha(restoreTargetSnapshot.commit_sha)
                  : ""}
              </span>
              . Текущая версия останется в истории — её можно будет вернуть.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="ghost"
              size="lg"
              onClick={() => setRestoreTarget(null)}
              disabled={restoringSnapshot}
              className="text-[#6d6962] hover:bg-[#ece8df] hover:text-[#171716]"
            >
              Отмена
            </Button>
            <Button
              type="button"
              size="lg"
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

function MaxEditMenu({
  mode,
  disabled,
  selectionCount,
  onModeChange,
}: {
  mode: EditorMode;
  disabled: boolean;
  selectionCount: number;
  onModeChange: (mode: EditorMode) => void;
}) {
  const active = mode !== "off";
  const label =
    mode === "inspect" ? "С ИИ" : mode === "style" ? "Вручную" : "Править";
  const Icon =
    mode === "inspect" ? Sparkles : mode === "style" ? Pencil : MousePointer2;
  const selectionLabel =
    mode === "inspect" && selectionCount > 0
      ? `, выбрано: ${selectionCount}`
      : "";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          data-testid="max-edit-menu-trigger"
          aria-label={`Режим правки: ${label}${selectionLabel}`}
          aria-pressed={active}
          title={active ? `Режим: ${label}` : "Править элементы"}
          className={cn(
            "relative grid size-11 shrink-0 place-items-center rounded-[9px] border transition-[color,background-color,border-color,transform] duration-150 ease-out active:scale-[.96] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-45 disabled:active:scale-100",
            active
              ? "border-accent/35 bg-accent/10 text-accent"
              : "border-[#d8d4cb] bg-[#fcfbf7] text-[#6d6962] hover:bg-[#f5f3ee] hover:text-[#171716]",
          )}
        >
          <Icon className="size-4" />
          {mode === "inspect" && selectionCount > 0 && (
            <span className="absolute right-1 top-1 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-accent px-0.5 text-[8px] font-semibold leading-none text-accent-fg tabular-nums">
              {selectionCount}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={8}
        className="w-52 border-[#d8d4cb] bg-[#fcfbf7] p-1 text-[#171716] shadow-[0_14px_36px_rgba(23,23,22,.14)]"
        data-testid="max-edit-menu"
      >
        <DropdownMenuRadioGroup
          value={mode}
          onValueChange={(value) => {
            if (value === "inspect" || value === "style" || value === "off") {
              onModeChange(value);
            }
          }}
        >
          <DropdownMenuRadioItem
            value="inspect"
            data-testid="max-edit-with-ai"
            className="min-h-11 gap-2 rounded-[8px] py-2 pl-8 pr-2.5 text-xs font-medium focus:bg-[#f5f3ee]"
          >
            <Sparkles className="size-3.5 shrink-0 text-accent" />
            Изменить с ИИ
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem
            value="style"
            data-testid="max-edit-manually"
            className="min-h-11 gap-2 rounded-[8px] py-2 pl-8 pr-2.5 text-xs font-medium focus:bg-[#f5f3ee]"
          >
            <Pencil className="size-3.5 shrink-0 text-[#725f4f]" />
            Настроить вручную
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        {active && (
          <>
            <DropdownMenuSeparator className="bg-[#e7e3da]" />
            <DropdownMenuItem
              onSelect={() => onModeChange("off")}
              className="min-h-10 rounded-[8px] px-2.5 py-2 text-[11px] text-[#6d6962] focus:bg-[#f5f3ee]"
            >
              <X className="size-3.5" />
              Готово
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
