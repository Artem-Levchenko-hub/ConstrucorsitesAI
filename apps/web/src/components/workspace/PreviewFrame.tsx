"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  RotateCw,
  Smartphone,
  Tablet,
  Monitor,
  Eye,
  Code as CodeIcon,
  Clock,
  Loader2,
  Play,
  ServerCog,
  MousePointerClick,
  PanelLeftOpen,
  PanelRightOpen,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { listSnapshots } from "@/lib/api/snapshots";
import { listMessages } from "@/lib/api/messages";
import { getRuntime, startRuntime } from "@/lib/api/runtime";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace";
import { useInspectorStore } from "@/store/inspector";
import { toast } from "sonner";
import type { Project, Snapshot } from "@/lib/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { shortSha, cn, formatRelativeTime } from "@/lib/utils";
import { StreamingPreviewFrame } from "./StreamingPreviewFrame";
import { CodeView } from "./CodeView";

type Device = "mobile" | "tablet" | "desktop";
const DEVICE_WIDTH: Record<Device, string> = {
  mobile: "390px",
  tablet: "768px",
  desktop: "100%",
};

export function PreviewFrame({ project }: { project: Project }) {
  const selectedSnapshotId = useWorkspaceStore((s) => s.selectedSnapshotId);
  const selectSnapshot = useWorkspaceStore((s) => s.selectSnapshot);
  const viewMode = useWorkspaceStore((s) => s.viewMode);
  const setViewMode = useWorkspaceStore((s) => s.setViewMode);
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const toggleTimeline = useWorkspaceStore((s) => s.toggleTimeline);

  // Select-mode (element picker). The inspector script lives inside the preview
  // document; we drive it over postMessage through the iframe ref below.
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const inspectMode = useInspectorStore((s) => s.inspectMode);
  const toggleInspect = useInspectorStore((s) => s.toggleInspectMode);
  const setInspectMode = useInspectorStore((s) => s.setInspectMode);
  const addSelection = useInspectorStore((s) => s.addSelection);
  const selections = useInspectorStore((s) => s.selections);
  const prevPickIds = useRef<string[]>([]);
  // Tracks which iframe key has emitted `omnia:inspect:ready` — used by the
  // race-fallback timer to know whether to warn the user.
  const inspectorReadyKeyRef = useRef<number | null>(null);
  const postToPreview = useCallback((msg: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(msg, "*");
  }, []);

  const { data: snapshots, isPending } = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });
  // Реалтайм-preview: тянем messages, чтобы достать частичный <file path="index.html">
  // из текущего стриминг-сообщения. Кэш разделяем с ChatPanel, второй запрос
  // дёшев и react-query сам дедуплицирует.
  const { data: messages } = useQuery({
    queryKey: ["messages", project.id],
    queryFn: () => listMessages(project.id),
  });

  // Fullstack projects (V2 Phase A) preview a live Next.js dev container,
  // not a static Playwright PNG. We hit /api/projects/:id/runtime on a short
  // poll while the container is provisioning, then settle on the dev_url
  // returned by orchestrator. For V1 projects (template !== "fullstack")
  // this query is skipped — we keep the existing /p/<slug> iframe.
  const isFullstack = project.template === "fullstack";
  const qc = useQueryClient();
  const {
    data: runtime,
    isError: runtimeError,
    isLoading: runtimeLoading,
  } = useQuery({
    queryKey: ["runtime", project.id],
    queryFn: () => getRuntime(project.id),
    enabled: isFullstack,
    refetchInterval: (q) =>
      q.state.data?.state === "provisioning" ? 2_000 : false,
    retry: false,
  });

  // V2: provision the dev container on open so the live Next.js app appears
  // by itself — no need to hunt for the TopBar "Запустить". `provision` is
  // idempotent; we fire it once. If the orchestrator is down the mutation
  // errors and the in-frame panel below shows a "Запустить" retry instead of
  // a blank iframe.
  const startMut = useMutation({
    mutationFn: () => startRuntime(project.id),
    onSuccess: (s) => qc.setQueryData(["runtime", project.id], s),
  });
  const autoStarted = useRef(false);
  const runtimeState = runtime?.state;
  useEffect(() => {
    if (!isFullstack || autoStarted.current || runtimeLoading) return;
    const idle =
      runtimeError ||
      runtimeState === "stopped" ||
      runtimeState === "failed" ||
      runtimeState === undefined;
    if (idle) {
      autoStarted.current = true;
      startMut.mutate();
    }
  }, [isFullstack, runtimeLoading, runtimeError, runtimeState, startMut]);

  const headSnapshot = snapshots?.[0];
  const visible: Snapshot | undefined = selectedSnapshotId
    ? snapshots?.find((s) => s.id === selectedSnapshotId)
    : headSnapshot;
  const viewingOld = !!visible && !!headSnapshot && visible.id !== headSnapshot.id;

  const [device, setDevice] = useState<Device>("desktop");
  const [iframeKey, setIframeKey] = useState(0);

  // Reset the per-iframe ready latch whenever the frame remounts so the toggle
  // effect below can correctly tell "inspector is alive in this frame" from
  // "stale ready signal from the previous iframe".
  useEffect(() => {
    inspectorReadyKeyRef.current = null;
  }, [iframeKey]);

  // Flip picking on/off when the toolbar toggle changes. (Re)loads while the
  // mode is on are handled by the `omnia:inspect:ready` branch below.
  //
  // Race fix (fullstack template): the inspector loads via Next.js
  // `<Script strategy="afterInteractive">`, which attaches its message
  // listener AFTER React hydration — by then our initial `enable` may have
  // flown past a not-yet-listening script. We re-send at ~700ms and, if no
  // `omnia:inspect:ready` arrived by 3s, surface a user-facing toast so the
  // owner doesn't sit in front of a dead toggle wondering why hover doesn't
  // highlight anything.
  useEffect(() => {
    postToPreview({
      type: inspectMode ? "omnia:inspect:enable" : "omnia:inspect:disable",
    });
    if (!inspectMode) return;
    const retry = window.setTimeout(() => {
      postToPreview({ type: "omnia:inspect:enable" });
    }, 700);
    const warn = window.setTimeout(() => {
      if (inspectorReadyKeyRef.current !== iframeKey) {
        toast.error("Инспектор не загрузился в превью", {
          description:
            "Нажмите кнопку перезагрузки превью (↻ справа от инспектора) и попробуйте ещё раз.",
        });
      }
    }, 3000);
    return () => {
      window.clearTimeout(retry);
      window.clearTimeout(warn);
    };
  }, [inspectMode, iframeKey, postToPreview]);

  // Picking on an old snapshot would edit HEAD — confusing, so disable it.
  useEffect(() => {
    if (viewingOld && inspectMode) setInspectMode(false);
  }, [viewingOld, inspectMode, setInspectMode]);

  // Receive picks from the preview; re-arm after a (re)load.
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const win = iframeRef.current?.contentWindow;
      if (!win || e.source !== win) return; // trust only our own preview
      const d = e.data as {
        type?: string;
        el?: Record<string, string>;
      };
      if (!d || typeof d.type !== "string") return;
      if (d.type === "omnia:inspect:ready") {
        // Latch which frame is alive so the toggle effect's fallback timer
        // can distinguish "script loaded" from "script never loaded".
        inspectorReadyKeyRef.current = iframeKey;
        if (inspectMode) postToPreview({ type: "omnia:inspect:enable" });
        return;
      }
      if (d.type === "omnia:pick" && d.el) {
        const el = d.el;
        addSelection({
          id: String(el.id),
          selector: String(el.selector ?? ""),
          label: el.label ?? null,
          text: el.text ?? null,
          html: el.html ?? null,
          comment: "",
        });
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [inspectMode, iframeKey, addSelection, postToPreview]);

  // Keep the preview's outlines in sync when chips are removed/cleared (e.g.
  // after send): diff against the previous pick ids and tell the inspector.
  useEffect(() => {
    const curr = selections.map((s) => s.id);
    const removed = prevPickIds.current.filter((id) => !curr.includes(id));
    if (removed.length) {
      if (curr.length === 0) postToPreview({ type: "omnia:inspect:clear" });
      else
        removed.forEach((id) =>
          postToPreview({ type: "omnia:inspect:remove", id }),
        );
    }
    prevPickIds.current = curr;
  }, [selections, postToPreview]);

  const apiOrigin =
    process.env.NEXT_PUBLIC_API_URL ??
    (typeof window !== "undefined" ? window.location.origin : "");
  const publicUrl = apiOrigin
    ? `${apiOrigin.replace(/\/$/, "")}/p/${project.slug}`
    : `https://${project.slug}.omnia.ai`;

  // For V1 projects the /p/<slug> endpoint always serves the project's
  // current_snapshot HEAD. To show a *historical* snapshot, append
  // `?snapshot=<id>`. Re-key the iframe when the visible snapshot changes
  // so React fully remounts (clean reload).
  //
  // For V2 fullstack: when the dev container is up, swap the iframe to
  // `runtime.dev_url` — the live Next.js process with HMR. We deliberately
  // do NOT pass `?snapshot=…` here because the dev container only knows
  // about HEAD (orchestrator hot-reload always writes the latest snapshot's
  // files). Historical snapshots on fullstack will need a deploy-time
  // checkout in a later sprint.
  const fullstackLive =
    isFullstack && runtime?.state === "running" && !!runtime.dev_url;
  const liveSrc = `${runtime?.dev_url ?? ""}#k=${iframeKey}`;
  // `inspect=1` opts the workspace preview into select-mode (serves the inspector
  // script). The external "Открыть"/address links use `publicUrl` untouched, so
  // public share links stay clean.
  const staticSrc =
    visible && snapshots && visible.id !== snapshots[0]?.id
      ? `${publicUrl}?snapshot=${visible.id}&inspect=1#k=${iframeKey}`
      : `${publicUrl}?inspect=1#k=${iframeKey}`;

  // Пока ассистент стримит ответ — показываем долгоживущий streaming iframe
  // (StreamingPreviewFrame) с morphdom-патчингом. Когда llm.done приходит и
  // isStreaming становится false, AnimatePresence переключается на iframe
  // committed-снапшота (бэкенд `/p/<slug>`).
  const last = messages?.[messages.length - 1];
  const isStreaming = last?.role === "assistant" && last.tokens_out === null;
  // Streaming morphdom preview only applies to static HTML (it patches the
  // index.html body). Full-stack projects render via the live dev container
  // (HMR), so we never show the morph frame for them.
  const showStreaming = isStreaming && !selectedSnapshotId && !isFullstack;

  return (
    <div className="flex flex-col h-full bg-surface-base">
      <div className="h-10 flex items-center justify-between px-4 gap-3">
        <div className="flex items-center gap-1.5 shrink-0">
          {/* Развернуть чат — появляется когда левая панель свёрнута (preview
              на всю ширину); свернуть обратно можно шевроном в шапке чата. */}
          {chatCollapsed && (
            <Button
              size="sm"
              variant="ghost"
              className="px-1.5"
              onClick={toggleChat}
              aria-label="Развернуть чат"
              title="Развернуть чат"
            >
              <PanelLeftOpen className="h-4 w-4 text-fg-tertiary" />
            </Button>
          )}
          {/* Preview / Code tabs — pill style matching landing */}
          <div className="flex items-center rounded-full border border-border-subtle bg-surface-raised p-0.5">
            {(
              [
                ["preview", Eye, "Preview"],
                ["code", CodeIcon, "Код"],
              ] as const
            ).map(([mode, Icon, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => setViewMode(mode)}
                className={cn(
                  "px-2.5 h-6 rounded-full text-xs font-medium transition-all flex items-center gap-1.5",
                  viewMode === mode
                    ? "bg-accent-subtle text-accent ring-1 ring-inset ring-[rgba(124,92,255,0.25)]"
                    : "text-fg-tertiary hover:text-fg-secondary",
                )}
              >
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
          </div>
          {visible?.commit_sha && (
            <span className="text-[11px] font-mono text-fg-tertiary ml-1">
              · {shortSha(visible.commit_sha)}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {/* Inspect + reload — только для preview-режима. Переключатель
              устройств переехал в браузер-бар превью (он про сам сайт). */}
          {viewMode === "preview" && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => toggleInspect()}
                disabled={viewingOld}
                title={
                  viewingOld
                    ? "Выбор недоступен при просмотре старой версии"
                    : inspectMode
                      ? "Выключить выбор элементов"
                      : "Выбрать элементы в превью для точечной правки"
                }
                className={cn(inspectMode && "text-accent bg-accent-subtle")}
              >
                <MousePointerClick className="h-3.5 w-3.5" />
              </Button>

              <Button
                size="sm"
                variant="ghost"
                onClick={() => setIframeKey((k) => k + 1)}
                title="Перезагрузить превью"
              >
                <RotateCw className="h-3.5 w-3.5" />
              </Button>
            </>
          )}

          <Button size="sm" variant="ghost" asChild>
            <a href={publicUrl} target="_blank" rel="noreferrer">
              <ExternalLink className="h-3.5 w-3.5" />
              Открыть
            </a>
          </Button>

          {/* Развернуть историю — появляется когда правая панель свёрнута. */}
          {timelineCollapsed && (
            <Button
              size="sm"
              variant="ghost"
              className="px-1.5"
              onClick={toggleTimeline}
              aria-label="Развернуть историю"
              title="Развернуть историю"
            >
              <PanelRightOpen className="h-4 w-4 text-fg-tertiary" />
            </Button>
          )}
        </div>
      </div>

      {viewingOld && (
        <div className="px-4 py-2 border-b border-amber-500/30 bg-amber-500/10 flex items-center gap-2 text-xs">
          <Clock className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400 shrink-0" />
          <span className="text-fg-primary">
            Просматриваете старую версию ({formatRelativeTime(visible.created_at)}) —{" "}
            <span className="font-mono">{shortSha(visible.commit_sha)}</span>
          </span>
          <button
            type="button"
            onClick={() => selectSnapshot(null)}
            className="ml-auto text-accent hover:underline shrink-0"
          >
            Вернуться к текущей →
          </button>
        </div>
      )}

      <div className="flex-1 p-2 overflow-hidden">
        <div className="h-full w-full rounded-lg border border-border-default bg-surface-raised overflow-hidden flex flex-col">
          {viewMode === "code" && visible ? (
            <CodeView projectId={project.id} snapshotId={visible.id} />
          ) : (
            <>
              <div className="h-9 flex items-center gap-1.5 px-3 shrink-0">
                <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
                <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
                <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
                <a
                  href={fullstackLive ? runtime!.dev_url! : publicUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-3 min-w-0 flex-1 text-xs font-mono text-fg-tertiary truncate hover:text-fg-secondary transition-colors"
                  title="Открыть в новой вкладке"
                >
                  {fullstackLive ? runtime!.dev_url : publicUrl}
                </a>

                {/* Device size — свойство превьюемого сайта, поэтому живёт в
                    браузер-баре, а не в инструментах сверху. */}
                <div className="flex items-center rounded-full border border-border-subtle bg-surface-raised p-0.5 shrink-0">
                  {(
                    [
                      ["mobile", Smartphone],
                      ["tablet", Tablet],
                      ["desktop", Monitor],
                    ] as const
                  ).map(([d, Icon]) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDevice(d)}
                      title={d}
                      className={cn(
                        "p-1 rounded-full transition-all",
                        device === d
                          ? "bg-accent-subtle text-accent ring-1 ring-inset ring-[rgba(124,92,255,0.25)]"
                          : "text-fg-tertiary hover:text-fg-secondary",
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex-1 relative bg-surface-base flex items-start justify-center overflow-auto">
                {isPending && (
                  <div className="absolute inset-0 p-4">
                    <Skeleton className="w-full h-full" />
                  </div>
                )}

                <AnimatePresence mode="wait">
                  {showStreaming ? (
                    <StreamingPreviewFrame
                      key="streaming"
                      content={last?.content ?? ""}
                      device={device}
                    />
                  ) : fullstackLive ? (
                    <motion.iframe
                      key={`live-${iframeKey}`}
                      ref={iframeRef}
                      src={liveSrc}
                      title="Preview (live dev container)"
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      style={{ width: DEVICE_WIDTH[device], maxWidth: "100%" }}
                      className="h-full bg-white border-0 mx-auto shadow-xl"
                      onLoad={() => {
                        // Belt-and-braces: re-fire enable after iframe DOM
                        // finishes loading. The Next.js Script tag (afterInteractive)
                        // may attach its message listener AFTER our toggle effect
                        // already posted, so we re-emit here for the late case.
                        if (inspectMode) {
                          window.setTimeout(
                            () => postToPreview({ type: "omnia:inspect:enable" }),
                            150,
                          );
                        }
                      }}
                    />
                  ) : isFullstack ? (
                    <RuntimeStartupPanel
                      key="runtime"
                      state={runtimeState}
                      starting={startMut.isPending}
                      onStart={() => {
                        autoStarted.current = true;
                        startMut.mutate();
                      }}
                    />
                  ) : visible ? (
                    <motion.iframe
                      key={`${visible.id}-${iframeKey}`}
                      ref={iframeRef}
                      src={staticSrc}
                      title={`Preview ${shortSha(visible.commit_sha)}`}
                      // sandbox lets the rendered site run JS, fonts, links inside
                      // the iframe but blocks privileged APIs (downloads, top-level
                      // navigation away from us). same-origin so cookies don't leak.
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      style={{ width: DEVICE_WIDTH[device], maxWidth: "100%" }}
                      className="h-full bg-white border-0 mx-auto shadow-xl"
                      onLoad={() => {
                        // Same race protection as the live iframe — defensive
                        // re-enable after inspector script settles.
                        if (inspectMode) {
                          window.setTimeout(
                            () => postToPreview({ type: "omnia:inspect:enable" }),
                            150,
                          );
                        }
                      }}
                    />
                  ) : null}
                  {!visible && !isPending && !isFullstack && (
                    <motion.div
                      key="empty"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center px-6"
                    >
                      <div className="text-sm text-fg-secondary">
                        Здесь появится ваш сайт.
                      </div>
                      <div className="text-xs text-fg-tertiary leading-5 max-w-xs">
                        Отправьте первый промпт — мы сгенерируем код, закоммитим
                        в git и сделаем скриншот.
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * In-frame state for full-stack projects whose dev container isn't live yet.
 * Replaces what used to be a blank/404 iframe with an honest "запускается" /
 * "Запустить" affordance. Provisioning is also auto-triggered on open (see the
 * effect in PreviewFrame); this panel is the manual + status surface.
 */
function RuntimeStartupPanel({
  state,
  starting,
  onStart,
}: {
  state?: string;
  starting: boolean;
  onStart: () => void;
}) {
  const provisioning = starting || state === "provisioning";
  const failed = state === "failed" && !starting;
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-6"
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border-subtle bg-surface-base">
        {provisioning ? (
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
        ) : (
          <ServerCog className="h-5 w-5 text-fg-tertiary" />
        )}
      </div>
      {provisioning ? (
        <>
          <div className="text-sm text-fg-secondary">Приложение запускается…</div>
          <div className="max-w-xs text-xs leading-5 text-fg-tertiary">
            Поднимаем dev-контейнер Next.js. Первый запуск занимает до минуты —
            дальше превью обновляется мгновенно.
          </div>
        </>
      ) : (
        <>
          <div className="text-sm text-fg-secondary">
            {failed ? "Не удалось запустить приложение" : "Full-stack приложение"}
          </div>
          <div className="max-w-xs text-xs leading-5 text-fg-tertiary">
            {failed
              ? "Среда выполнения сейчас недоступна. Попробуйте ещё раз."
              : "Это проект на Next.js. Запустите dev-контейнер, чтобы увидеть живое превью."}
          </div>
          <Button size="sm" onClick={onStart} className="mt-1 gap-1.5">
            <Play className="h-3.5 w-3.5" />
            {failed ? "Повторить" : "Запустить приложение"}
          </Button>
        </>
      )}
    </motion.div>
  );
}
