"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BatteryFull,
  Check,
  CircleAlert,
  ExternalLink,
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
import { getRuntime, startRuntime } from "@/lib/api/runtime";
import type { Project } from "@/lib/api/types";
import {
  editorModeMessages,
  previewTargetOrigin,
  type EditorMode,
} from "@/lib/editor-bridge";
import { cn } from "@/lib/utils";
import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";

const SCREEN_WIDTH = 390;
const SCREEN_HEIGHT = 844;
const STATUS_BAR_HEIGHT = 38;
const DEVICE_BEZEL = 10;
const DEVICE_WIDTH = SCREEN_WIDTH + DEVICE_BEZEL * 2;
const DEVICE_HEIGHT = SCREEN_HEIGHT + STATUS_BAR_HEIGHT + DEVICE_BEZEL * 2;

export function MaxLivePreview({
  project,
  onClose,
}: {
  project: Project;
  onClose?: () => void;
}) {
  const queryClient = useQueryClient();
  const editorInstanceId = useId();
  const selectionIdPrefix = `${editorInstanceId}|`;
  const started = useRef(false);
  const deviceStage = useRef<HTMLDivElement>(null);
  const previewFrame = useRef<HTMLIFrameElement>(null);
  const previousPickIds = useRef<string[]>([]);
  const [deviceScale, setDeviceScale] = useState(0.72);
  const [lastWorkingUrl, setLastWorkingUrl] = useState<string | null>(null);
  const [loadedPreviewUrl, setLoadedPreviewUrl] = useState<string | null>(null);
  const [inspectorReady, setInspectorReady] = useState(false);
  const inspectMode = useInspectorStore((state) => state.inspectMode);
  const setInspectMode = useInspectorStore((state) => state.setInspectMode);
  const addSelection = useInspectorStore((state) => state.addSelection);
  const selections = useInspectorStore((state) => state.selections);
  const styleMode = useStyleEditStore((state) => state.styleMode);
  const setStyleMode = useStyleEditStore((state) => state.setStyleMode);
  const styleSelected = useStyleEditStore((state) => state.selected);
  const activeEditorMode: EditorMode = styleMode
    ? "style"
    : inspectMode
      ? "inspect"
      : "off";
  const postToPreview = useCallback((message: Record<string, unknown>) => {
    const frame = previewFrame.current;
    if (!frame?.contentWindow) return;
    const targetOrigin = previewTargetOrigin(frame.src, window.location.origin);
    if (targetOrigin) frame.contentWindow.postMessage(message, targetOrigin);
  }, []);
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
      if (useStyleEditStore.getState().styleMode) {
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
        return;
      }

      const rawId = String(element.id ?? "");
      if (!rawId) return;
      const alreadySelected = useInspectorStore
        .getState()
        .selections.some((selection) => selection.selector === selector);
      if (alreadySelected) {
        postToPreview({ type: "omnia:inspect:remove", id: rawId });
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
    }

    window.addEventListener("message", onPreviewMessage);
    return () => window.removeEventListener("message", onPreviewMessage);
  }, [
    addSelection,
    postToPreview,
    replayPendingStyles,
    selectionIdPrefix,
    syncEditorMode,
  ]);

  // The style inspector keeps its selected mark when disabled so it can resume
  // quickly. MAX deliberately clears that mark when the panel closes or the
  // user leaves manual mode; otherwise the phone looks editable after editing
  // has visibly ended.
  const previousEditorMode = useRef<EditorMode>("off");
  useEffect(() => {
    const leftStyleMode =
      previousEditorMode.current === "style" && activeEditorMode !== "style";
    const closedStylePanel = activeEditorMode === "style" && !styleSelected;
    if (leftStyleMode || closedStylePanel) {
      postToPreview({ type: "omnia:inspect:clear" });
    }
    previousEditorMode.current = activeEditorMode;
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
            disabled={!displayPreviewUrl}
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
                  {displayPreviewUrl ? (
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
        <div className="shrink-0 text-center">
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
        </div>
      </div>
      {styleMode && styleSelected && (
        <StylePanel
          projectId={project.id}
          post={postToAllProjectPreviews}
          sourceEditing={false}
          fontEditing={false}
          tokenEditing={false}
        />
      )}
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
