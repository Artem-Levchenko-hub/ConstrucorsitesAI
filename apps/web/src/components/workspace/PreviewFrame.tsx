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
  Sparkles,
  Pencil,
  PanelLeftOpen,
  PanelRightOpen,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { EASE_OUT, springSnappy } from "@/lib/motion";
import { listSnapshots } from "@/lib/api/snapshots";
import { listMessages, reportClientError } from "@/lib/api/messages";
import { getRuntime, startRuntime } from "@/lib/api/runtime";
import { getProject } from "@/lib/api/projects";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace";
import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";
import { toast } from "sonner";
import type { Project, Snapshot, StreamBrief, TurnMode } from "@/lib/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { shortSha, cn, formatRelativeTime } from "@/lib/utils";
import { StreamingPreviewFrame } from "./StreamingPreviewFrame";
import { StreamingCodeView } from "./StreamingCodeView";
import { StreamingAgentCodeView } from "./StreamingAgentCodeView";
import { StylePanel } from "./StylePanel";
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
  // Client-side dedup + cap for preview runtime errors forwarded by the inspector.
  // The inspector already dedups per page load; this guards across reloads so a
  // persistently-broken preview can't refill the chat with the same card.
  const reportedErrorsRef = useRef<Set<string>>(new Set());

  // Style-edit mode (1.5) — direct color/font picker, mutually exclusive with
  // select-mode (both hijack clicks in the preview).
  const styleMode = useStyleEditStore((s) => s.styleMode);
  const setStyleMode = useStyleEditStore((s) => s.setStyleMode);
  const styleSelected = useStyleEditStore((s) => s.selected);
  const onToggleInspect = useCallback(() => {
    setStyleMode(false);
    toggleInspect();
  }, [setStyleMode, toggleInspect]);
  const onToggleStyle = useCallback(() => {
    if (!styleMode) setInspectMode(false);
    setStyleMode(!styleMode);
  }, [styleMode, setStyleMode, setInspectMode]);
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
  // Art-director brief for the latest message (cached by usePromptStream on the
  // `omnia:brief` event — same client-only key the streaming freeform preview
  // reads). We forward it into the live entity/fullstack container below so the
  // generated app narrates its own birth (omnia-brief-narration.js); otherwise
  // entity surfaces are born silent — the brief reaches only the workspace chat.
  const briefMsgId = messages?.[messages.length - 1]?.id ?? "";
  const { data: streamBrief } = useQuery<StreamBrief | null>({
    queryKey: ["stream-brief", project.id, briefMsgId],
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });

  // Fullstack projects (V2 Phase A) preview a live Next.js dev container,
  // not a static Playwright PNG. We hit /api/projects/:id/runtime on a short
  // poll while the container is provisioning, then settle on the dev_url
  // returned by orchestrator. For V1 (static) projects this query is skipped —
  // we keep the existing /p/<slug> iframe. Both container-backed Next stacks
  // (fullstack + nextjs_entities) take the live-container path.
  const qc = useQueryClient();
  // The `project` prop is server-rendered once at page load and never updates
  // client-side. When a build auto-routes the stack (static → nextjs_entities
  // via the orchestrator), `project.template` here would stay stale for the
  // whole session, so the preview keeps showing the static placeholder instead
  // of switching to the live dev container. Mirror the project through React
  // Query (seeded with the server prop) so `usePromptStream` can refresh the
  // template on `llm.done` and the preview flips to the container path with no
  // manual reload. (R-02: the staleness is hidden behind one read.)
  const { data: liveProject } = useQuery({
    queryKey: ["project", project.id],
    queryFn: () => getProject(project.id),
    initialData: project,
  });
  const template = liveProject?.template ?? project.template;
  // Container-backed WEB stacks — mirror the backend `is_fullstack`
  // (schemas/project.py `_ORCHESTRATOR_TEMPLATE_BY_API`) for every stack that
  // renders in a browser: their build runs through the native agent (tools, not
  // `<file>` blocks), so «Код» must use the agent-step live tree AND the preview
  // uses the live dev container. `spa` (Vite+React) was missing here — its build
  // fell to the freeform `StreamingCodeView`, which waits for `<file>` blocks the
  // agent never emits, so «Код» sat on "AI собирает приложение…" the whole build
  // (owner report 2026-07-17). `tgbot`/`api` are container-backed too but have no
  // browser UI, so they stay out of the web-preview path (handled like `code`).
  const isFullstack =
    template === "fullstack" ||
    template === "nextjs_entities" ||
    template === "spa" ||
    template === "realtime";
  // `code` projects (owner 2026-06-18) are language-agnostic source, not a
  // website — there's nothing to render in an iframe. Land the user on the «Код»
  // tab and show an explainer panel in Preview mode instead of a blank /p/<slug>.
  const isCode = template === "code";
  const codeDefaultApplied = useRef(false);
  useEffect(() => {
    if (isCode && !codeDefaultApplied.current) {
      codeDefaultApplied.current = true;
      setViewMode("code");
    }
  }, [isCode, setViewMode]);
  const {
    data: runtime,
    isError: runtimeError,
    isLoading: runtimeLoading,
  } = useQuery({
    queryKey: ["runtime", project.id],
    queryFn: () => getRuntime(project.id),
    enabled: isFullstack,
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
  // Phase 5.3 — content-load gate for the preview iframe. Both iframes (live dev
  // container + static /p/<slug>) render with bg-white and paint white until
  // their OWN document finishes loading — the "белый экран до первого хита". We
  // cover that gap with a loading skeleton and lift it on the `load` event. We
  // track the iframe identity that has loaded (not a bare boolean) so a remount
  // — reload, snapshot switch, flip to the live container — re-covers on its own
  // without a setState-in-effect reset.
  const [loadedFrameKey, setLoadedFrameKey] = useState<string | null>(null);

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

  // Style-mode enable/disable bridge (mirrors the select-mode effect above).
  useEffect(() => {
    postToPreview({
      type: styleMode ? "omnia:style:enable" : "omnia:style:disable",
    });
    if (!styleMode) return;
    const retry = window.setTimeout(() => {
      postToPreview({ type: "omnia:style:enable" });
    }, 700);
    return () => window.clearTimeout(retry);
  }, [styleMode, iframeKey, postToPreview]);

  // Editing an old snapshot would touch HEAD — confusing, so disable both modes.
  useEffect(() => {
    if (viewingOld && inspectMode) setInspectMode(false);
    if (viewingOld && styleMode) setStyleMode(false);
  }, [viewingOld, inspectMode, styleMode, setInspectMode, setStyleMode]);

  // Receive picks from the preview; re-arm after a (re)load.
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const win = iframeRef.current?.contentWindow;
      if (!win || e.source !== win) return; // trust only our own preview
      const d = e.data as {
        type?: string;
        el?: Record<string, string> & {
          srcs?: string[];
          editableText?: boolean;
          editText?: string;
          textIndex?: number;
          outerHTML?: string;
          htmlIndex?: number;
          prevHTML?: string;
          prevIndex?: number;
          nextHTML?: string;
          nextIndex?: number;
        };
      };
      if (!d || typeof d.type !== "string") return;
      if (d.type === "omnia:preview:error") {
        // Uncaught JS error from the live preview → chat card. Skip old-snapshot
        // views (not actionable) and dedup/cap so a broken page can't spam.
        if (viewingOld) return;
        const err = (
          e.data as {
            err?: {
              message?: string;
              source?: string;
              line?: number;
              col?: number;
              stack?: string;
              route?: string;
              crumbs?: string[];
            };
          }
        ).err;
        if (!err || !err.message) return;
        const sig = `${err.message}@${err.source ?? ""}:${err.line ?? 0}`;
        const seen = reportedErrorsRef.current;
        if (seen.has(sig) || seen.size >= 5) return;
        seen.add(sig);
        void reportClientError(project.id, {
          message: String(err.message),
          source: err.source ? String(err.source) : undefined,
          line: typeof err.line === "number" ? err.line : undefined,
          col: typeof err.col === "number" ? err.col : undefined,
          stack: err.stack ? String(err.stack) : undefined,
          route: err.route ? String(err.route) : undefined,
          crumbs: Array.isArray(err.crumbs)
            ? err.crumbs.filter((c) => typeof c === "string").slice(0, 6)
            : undefined,
        })
          .then(() => {
            // The server commits the card into the assistant message before the
            // 204 returns, so refetch now to render it WITHOUT a reload. The
            // app.error WS event also invalidates this key, but the socket is
            // only open while a generation streams — a client error on a
            // FINISHED app (the common case) has no live subscriber, so this
            // self-invalidation is what makes the card appear. Idempotent: when
            // the WS is open both fire, React Query dedupes the refetch.
            void qc.invalidateQueries({ queryKey: ["messages", project.id] });
          })
          .catch(() => {
            // Best-effort: a dropped error report must not disrupt the preview.
          });
        return;
      }
      if (d.type === "omnia:inspect:ready") {
        // Latch which frame is alive so the toggle effect's fallback timer
        // can distinguish "script loaded" from "script never loaded".
        inspectorReadyKeyRef.current = iframeKey;
        if (inspectMode) postToPreview({ type: "omnia:inspect:enable" });
        if (useStyleEditStore.getState().styleMode)
          postToPreview({ type: "omnia:style:enable" });
        return;
      }
      if (d.type === "omnia:pick" && d.el) {
        const el = d.el;
        // Style mode routes the pick to the style panel (computed color/font);
        // select mode attaches it as a commentable chip for an AI edit.
        if (useStyleEditStore.getState().styleMode) {
          useStyleEditStore.getState().selectElement({
            selector: String(el.selector ?? ""),
            tag: String(el.tag ?? ""),
            color: String(el.color ?? ""),
            backgroundColor: String(el.backgroundColor ?? ""),
            borderColor: String(el.borderColor ?? ""),
            fontFamily: String(el.fontFamily ?? ""),
            src: String(el.src ?? ""),
            srcs: Array.isArray(el.srcs) ? el.srcs.map(String) : [],
            editableText: Boolean(el.editableText),
            editText: String(el.editText ?? ""),
            textIndex: typeof el.textIndex === "number" ? el.textIndex : 0,
            outerHTML: String(el.outerHTML ?? ""),
            htmlIndex: typeof el.htmlIndex === "number" ? el.htmlIndex : 0,
            prevHTML: String(el.prevHTML ?? ""),
            prevIndex: typeof el.prevIndex === "number" ? el.prevIndex : 0,
            nextHTML: String(el.nextHTML ?? ""),
            nextIndex: typeof el.nextIndex === "number" ? el.nextIndex : 0,
          });
        } else {
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
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [inspectMode, iframeKey, addSelection, postToPreview, project.id, viewingOld, qc]);

  // A new committed snapshot = a fresh build/edit: forget which preview errors
  // we've already reported so genuine errors on the new code surface again.
  useEffect(() => {
    reportedErrorsRef.current = new Set();
  }, [headSnapshot?.id]);

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

  // Identity of the iframe currently mounted. Changes on reload (iframeKey),
  // snapshot switch (visible.id), or flip between static and live container —
  // each of which remounts the iframe — so comparing it to loadedFrameKey tells
  // us whether the *current* frame has painted yet (no reset effect needed).
  const frameKey = fullstackLive
    ? `live-${iframeKey}`
    : `${visible?.id ?? "none"}-${iframeKey}`;
  // Fail-safe (R-10): a load event that never fires (dead container, blocked
  // request) must not leave the skeleton covering the preview forever. Bounded
  // timeout lifts the gate regardless. setState here runs inside setTimeout (not
  // synchronously in the effect body), so it doesn't cascade renders.
  useEffect(() => {
    if (loadedFrameKey === frameKey) return;
    const t = window.setTimeout(() => setLoadedFrameKey(frameKey), 8_000);
    return () => window.clearTimeout(t);
  }, [loadedFrameKey, frameKey]);

  // Shared iframe `load` handler — lift the load gate, then re-arm select/style
  // mode after the inspector script settles. Both iframes (live + static) had
  // near-identical onLoad bodies; folding them here keeps the re-arm logic in
  // one place (R-04). The 150ms defer covers Next's afterInteractive script
  // attaching its message listener after our toggle effect already posted.
  const handleFrameLoad = useCallback(() => {
    setLoadedFrameKey(frameKey);
    if (inspectMode) {
      window.setTimeout(
        () => postToPreview({ type: "omnia:inspect:enable" }),
        150,
      );
    }
    if (styleMode) {
      window.setTimeout(
        () => postToPreview({ type: "omnia:style:enable" }),
        150,
      );
    }
    // Replay the birth narration on (re)load if the brief is already here.
    if (streamBrief) {
      window.setTimeout(
        () => postToPreview({ type: "omnia:brief", brief: streamBrief }),
        150,
      );
    }
  }, [frameKey, inspectMode, styleMode, streamBrief, postToPreview]);

  // Forward the art-director brief into the live container so the generated app
  // plays its "AI is designing" reveal (omnia-brief-narration.js). Cross-origin
  // send is fine — only reads are blocked. The brief can land before OR after
  // the iframe loads, so we post on every relevant change plus retry once for a
  // listener that registered late (afterInteractive script on a fresh frame).
  useEffect(() => {
    if (!fullstackLive || !streamBrief) return;
    postToPreview({ type: "omnia:brief", brief: streamBrief });
    const retry = window.setTimeout(
      () => postToPreview({ type: "omnia:brief", brief: streamBrief }),
      900,
    );
    return () => window.clearTimeout(retry);
  }, [fullstackLive, streamBrief, iframeKey, postToPreview]);

  // Пока ассистент стримит ответ — показываем долгоживущий streaming iframe
  // (StreamingPreviewFrame) с morphdom-патчингом. Когда llm.done приходит и
  // isStreaming становится false, AnimatePresence переключается на iframe
  // committed-снапшота (бэкенд `/p/<slug>`).
  const last = messages?.[messages.length - 1];
  const isStreaming = last?.role === "assistant" && last.tokens_out === null;
  // Turn mode for the in-flight message (set by usePromptStream from the POST
  // response). A surgical EDIT streams an <edit> SEARCH/REPLACE diff — NOT HTML
  // — so morphing it would blank the preview with garbage. For an edit we keep
  // the current committed preview visible; it swaps to the new snapshot when
  // snapshot.created arrives. Reactive read of the cache (enabled:false).
  const turnMode = useQuery<TurnMode>({
    queryKey: ["turn-mode", project.id, last?.id ?? ""],
    queryFn: (): TurnMode => "build",
    enabled: false,
  }).data;
  const isEditTurn = isStreaming && turnMode === "edit";
  // Streaming morphdom preview only applies to static HTML (it patches the
  // index.html body). Full-stack projects render via the live dev container
  // (HMR), so we never show the morph frame for them. Edits keep the current
  // preview (see above), so they opt out of both streaming views.
  const showStreaming =
    isStreaming && !selectedSnapshotId && !isFullstack && !isEditTurn;
  // Fullstack/React can't morph-render mid-build (needs a compile step), so the
  // preview area would otherwise sit frozen on the old static page. Show the
  // live code streaming in instead — "building before your eyes" on every stack.
  const showStreamingCode =
    isStreaming && !selectedSnapshotId && isFullstack && !isEditTurn;

  // Phase 0.4 — never present the bare starter scaffold as if it were the
  // generated result. The starter snapshot is committed at project creation
  // with no prompt_text (and no parent); if it is still the auto-shown head,
  // no real generation has landed yet — fresh project, or a build that was
  // interrupted before producing any snapshot. Rendering its iframe makes an
  // empty template look like a finished site (the user thinks it "worked").
  // For static projects we suppress it and fall through to the explicit
  // await/empty status instead. `selectedSnapshotId` set → the user is
  // deliberately inspecting the scaffold from the timeline, so allow that.
  const headIsStarter =
    !!headSnapshot &&
    headSnapshot.prompt_text === null &&
    headSnapshot.parent_id === null;
  const suppressStarter = headIsStarter && !selectedSnapshotId && !isStreaming;

  // Phase 5.3 — whether a real preview iframe is the active branch (live dev
  // container, or static /p/<slug>). The streaming/startup/empty branches paint
  // their own content, so the load skeleton only covers the two iframes while
  // their document loads (handleFrameLoad lifts it).
  const iframeActive =
    !showStreaming &&
    !showStreamingCode &&
    !isCode &&
    (fullstackLive || (!isFullstack && !!visible && !suppressStarter));
  const showFrameLoading =
    iframeActive && loadedFrameKey !== frameKey && !isPending;

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
            ).map(([mode, Icon, label]) => {
              const active = viewMode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setViewMode(mode)}
                  className={cn(
                    "relative px-2.5 h-6 rounded-full text-xs font-medium transition-colors flex items-center gap-1.5",
                    active ? "text-accent" : "text-fg-tertiary hover:text-fg-secondary",
                  )}
                >
                  {/* Sliding selection — glides between Preview/Код instead of
                      snapping. Shared layoutId; only the active tab renders it. */}
                  {active && (
                    <motion.span
                      layoutId="preview-tab-pill"
                      transition={springSnappy}
                      className="absolute inset-0 rounded-full bg-accent-subtle ring-1 ring-inset ring-[rgba(124,92,255,0.25)]"
                    />
                  )}
                  <Icon className="relative z-10 h-3 w-3" />
                  <span className="relative z-10">{label}</span>
                </button>
              );
            })}
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
              <span className="text-[11px] font-medium text-fg-tertiary ml-1 select-none">
                Править
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={onToggleInspect}
                disabled={viewingOld}
                title={
                  viewingOld
                    ? "Недоступно при просмотре старой версии"
                    : inspectMode
                      ? "Выключить ИИ-правку"
                      : "Выдели элементы и опиши правку в чате — изменит ИИ"
                }
                className={cn(
                  "gap-1.5",
                  inspectMode && "text-accent bg-accent-subtle",
                )}
              >
                <Sparkles className="h-3.5 w-3.5" />
                С ИИ
              </Button>

              <Button
                size="sm"
                variant="ghost"
                onClick={onToggleStyle}
                disabled={viewingOld}
                title={
                  viewingOld
                    ? "Недоступно при просмотре старой версии"
                    : styleMode
                      ? "Выключить ручную правку"
                      : "Меняй цвет, шрифт и картинки сам, без ИИ"
                }
                className={cn(
                  "gap-1.5",
                  styleMode && "text-accent bg-accent-subtle",
                )}
              >
                <Pencil className="h-3.5 w-3.5" />
                Вручную
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
          {viewMode === "code" ? (
            isStreaming ? (
              // Fullstack/agentic builds write files via tools (no <file> blocks
              // in the chat stream) — drive the live view off the agent-step
              // stream so «Код» shows files appearing as they're written. Static
              // freeform builds still stream <file> blocks out of the content.
              isFullstack && last?.id ? (
                <StreamingAgentCodeView
                  projectId={project.id}
                  messageId={last?.id ?? ""}
                />
              ) : (
                <StreamingCodeView content={last?.content ?? ""} />
              )
            ) : visible ? (
              <CodeView projectId={project.id} snapshotId={visible.id} />
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-fg-tertiary">
                Нет кода для показа
              </div>
            )
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
                <div className="flex items-center gap-0.5 shrink-0">
                  {(
                    [
                      ["mobile", Smartphone],
                      ["tablet", Tablet],
                      ["desktop", Monitor],
                    ] as const
                  ).map(([d, Icon]) => {
                    const active = device === d;
                    return (
                      <button
                        key={d}
                        type="button"
                        onClick={() => setDevice(d)}
                        title={d}
                        className={cn(
                          "relative p-1.5 rounded-md transition-colors",
                          active
                            ? "text-accent"
                            : "text-fg-tertiary hover:text-fg-secondary",
                        )}
                      >
                        {active && (
                          <motion.span
                            layoutId="device-pill"
                            transition={springSnappy}
                            className="absolute inset-0 rounded-md bg-accent-subtle"
                          />
                        )}
                        <Icon className="relative z-10 h-4 w-4" />
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="flex-1 relative bg-surface-base flex items-start justify-center overflow-auto">
                {isPending && (
                  <div className="absolute inset-0 p-4">
                    <Skeleton className="w-full h-full" />
                  </div>
                )}

                {/* Surgical edit in flight — the current preview stays visible
                    (no rebuild, no blank); a quiet chip confirms work is happening
                    and that only the requested bit will change. */}
                <AnimatePresence>
                  {isEditTurn && (
                    <motion.div
                      key="edit-turn-chip"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2, ease: EASE_OUT }}
                      className="absolute top-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 rounded-full border border-border-subtle bg-surface-raised/90 px-3 py-1.5 text-xs text-fg-secondary shadow-sm backdrop-blur"
                    >
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Точечная правка…
                    </motion.div>
                  )}
                </AnimatePresence>

                <AnimatePresence mode="wait">
                  {isCode ? (
                    <CodeProjectPanel
                      key="code-project"
                      onOpenCode={() => setViewMode("code")}
                    />
                  ) : showStreaming ? (
                    <StreamingPreviewFrame
                      key="streaming"
                      content={last?.content ?? ""}
                      device={device}
                      projectId={project.id}
                      messageId={last?.id ?? ""}
                    />
                  ) : showStreamingCode ? (
                    <motion.div
                      key="streaming-code"
                      className="w-full h-full flex flex-col"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2 }}
                    >
                      <div className="flex-1 min-h-0">
                        {/* showStreamingCode ⟹ fullstack, so files come from the
                            agent-step stream, not <file> blocks in content. */}
                        <StreamingAgentCodeView
                          projectId={project.id}
                          messageId={last?.id ?? ""}
                        />
                      </div>
                      {/* Phase 3.3 — fullstack/container builds are single-shot:
                          the orchestrator emits no per-stage `llm.pass`, so the
                          PassProgressBar (static/freeform only) stays empty and
                          the user watching the code stream gets no progress
                          signal — it can read as "stalled". Honest two-step hint
                          (write code → compile container) derived purely from the
                          streaming state (no fake granularity, no cache deps).
                          Fail-soft: static text, never throws. */}
                      <div
                        role="status"
                        aria-label="Идёт сборка приложения"
                        className="shrink-0 border-t border-border-subtle bg-surface-raised px-4 py-2 flex items-center gap-3 text-xs"
                      >
                        <span className="flex items-center gap-1.5 text-fg-primary font-medium">
                          <Loader2 className="h-3 w-3 animate-spin text-accent" />
                          Пишем код
                        </span>
                        <span className="h-px w-4 bg-border-subtle" />
                        <span className="text-fg-tertiary">
                          Собираем контейнер
                        </span>
                        <span className="ml-auto text-fg-tertiary text-[11px]">
                          обычно 15–45 сек · код пишется вживую
                        </span>
                      </div>
                    </motion.div>
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
                      onLoad={handleFrameLoad}
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
                  ) : visible && !suppressStarter ? (
                    <motion.iframe
                      key={`${visible.id}-${iframeKey}`}
                      ref={iframeRef}
                      src={staticSrc}
                      title={`Preview ${shortSha(visible.commit_sha)}`}
                      // sandbox lets the rendered site run JS, fonts, links inside
                      // the iframe but blocks privileged APIs (downloads, top-level
                      // navigation away from us). same-origin so cookies don't leak.
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"
                      // "Settle" reveal: the finished site materialises with a
                      // subtle scale-up instead of a flat fade, so committing a
                      // build feels like a result landing (not a tab swap). Tiny
                      // (0.985→1), one-shot, GPU-composited — no reflow, motion-safe.
                      initial={{ opacity: 0, scale: 0.985 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.4, ease: EASE_OUT }}
                      style={{ width: DEVICE_WIDTH[device], maxWidth: "100%" }}
                      className="h-full bg-white border-0 mx-auto shadow-xl"
                      onLoad={handleFrameLoad}
                    />
                  ) : null}
                  {(!visible || suppressStarter) && !isPending && !isFullstack && !isCode && (
                    <motion.div
                      key="empty"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center px-6"
                    >
                      <div className="text-sm text-fg-secondary">
                        Сайт ещё не сгенерирован.
                      </div>
                      <div className="text-xs text-fg-tertiary leading-5 max-w-xs">
                        Опишите проект в чате — мы сгенерируем код, закоммитим в
                        git и сделаем скриншот. Здесь появится готовый сайт, а не
                        пустой шаблон.
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Phase 5.3 — load skeleton over the preview iframe until its
                    document paints, so neither the live container's first paint
                    nor the static /p/<slug> fetch shows a bare white screen. */}
                <AnimatePresence>
                  {showFrameLoading && (
                    <motion.div
                      key="frame-loading"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2, ease: EASE_OUT }}
                      className="absolute inset-0 z-10 p-4 bg-surface-base"
                      role="status"
                      aria-label="Превью загружается"
                    >
                      <Skeleton className="w-full h-full" />
                      <div className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-fg-tertiary">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        Загрузка превью…
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {styleMode && styleSelected && (
                  <StylePanel projectId={project.id} post={postToPreview} />
                )}
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

/**
 * In-frame state for `code` projects (language-agnostic source, owner 2026-06-18).
 * A script/program isn't a website — there's no live preview to render. Instead
 * of a blank /p/<slug> iframe, point the user at the «Код» tab and the
 * download / GitHub-push affordances (like browsing a repo on GitHub).
 */
function CodeProjectPanel({ onOpenCode }: { onOpenCode: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-6"
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border-subtle bg-surface-base">
        <CodeIcon className="h-5 w-5 text-fg-tertiary" />
      </div>
      <div className="text-sm text-fg-secondary">Это код-проект</div>
      <div className="max-w-xs text-xs leading-5 text-fg-tertiary">
        Программа/скрипт, а не сайт — живого превью нет. Открой вкладку «Код»,
        чтобы посмотреть файлы, скачать их или запушить в GitHub.
      </div>
      <Button size="sm" onClick={onOpenCode} className="mt-1 gap-1.5">
        <CodeIcon className="h-3.5 w-3.5" />
        Открыть код
      </Button>
    </motion.div>
  );
}
