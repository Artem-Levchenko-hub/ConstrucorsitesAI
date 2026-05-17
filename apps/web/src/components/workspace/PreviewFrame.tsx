"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ExternalLink,
  RotateCw,
  Smartphone,
  Tablet,
  Monitor,
  Eye,
  Code as CodeIcon,
  Clock,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { listSnapshots } from "@/lib/api/snapshots";
import { listMessages } from "@/lib/api/messages";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace";
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

  const headSnapshot = snapshots?.[0];
  const visible: Snapshot | undefined = selectedSnapshotId
    ? snapshots?.find((s) => s.id === selectedSnapshotId)
    : headSnapshot;
  const viewingOld = !!visible && !!headSnapshot && visible.id !== headSnapshot.id;

  const [device, setDevice] = useState<Device>("desktop");
  const [iframeKey, setIframeKey] = useState(0);

  const apiOrigin =
    process.env.NEXT_PUBLIC_API_URL ??
    (typeof window !== "undefined" ? window.location.origin : "");
  const publicUrl = apiOrigin
    ? `${apiOrigin.replace(/\/$/, "")}/p/${project.slug}`
    : `https://${project.slug}.omnia.ai`;

  // The /p/<slug> endpoint always serves the project's current_snapshot HEAD.
  // To show a *historical* snapshot, append ?snapshot=<id>. Re-key the iframe
  // when the visible snapshot changes so React fully remounts (clean reload).
  const iframeSrc =
    visible && snapshots && visible.id !== snapshots[0]?.id
      ? `${publicUrl}?snapshot=${visible.id}#k=${iframeKey}`
      : `${publicUrl}#k=${iframeKey}`;

  // Пока ассистент стримит ответ — показываем долгоживущий streaming iframe
  // (StreamingPreviewFrame) с morphdom-патчингом. Когда llm.done приходит и
  // isStreaming становится false, AnimatePresence переключается на iframe
  // committed-снапшота (бэкенд `/p/<slug>`).
  const last = messages?.[messages.length - 1];
  const isStreaming = last?.role === "assistant" && last.tokens_out === null;
  const showStreaming = isStreaming && !selectedSnapshotId;

  return (
    <div className="flex flex-col h-full bg-surface-base">
      <div className="h-10 flex items-center justify-between px-4 border-b border-border-subtle gap-3">
        <div className="flex items-center gap-1.5 shrink-0">
          {/* Preview / Code tabs */}
          <div className="flex items-center rounded-md border border-border-subtle p-0.5">
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
                  "px-2 h-6 rounded text-xs font-medium transition-colors flex items-center gap-1.5",
                  viewMode === mode
                    ? "bg-surface-raised text-fg-primary"
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
          {/* Device toggle + reload — только для preview-режима */}
          {viewMode === "preview" && (
            <>
              <div className="flex items-center rounded-md border border-border-subtle p-0.5">
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
                      "p-1.5 rounded transition-colors",
                      device === d
                        ? "bg-surface-raised text-fg-primary"
                        : "text-fg-tertiary hover:text-fg-secondary",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </button>
                ))}
              </div>

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

      <div className="flex-1 p-4 overflow-hidden">
        <div className="h-full w-full rounded-lg border border-border-default bg-surface-raised overflow-hidden flex flex-col">
          {viewMode === "code" && visible ? (
            <CodeView projectId={project.id} snapshotId={visible.id} />
          ) : (
            <>
              <div className="h-9 border-b border-border-subtle flex items-center gap-1.5 px-3 shrink-0">
                <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
                <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
                <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
                <a
                  href={publicUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-3 text-xs font-mono text-fg-tertiary truncate hover:text-fg-secondary transition-colors"
                  title="Открыть в новой вкладке"
                >
                  {publicUrl}
                </a>
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
                  ) : visible && (
                    <motion.iframe
                      key={`${visible.id}-${iframeKey}`}
                      src={iframeSrc}
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
                    />
                  )}
                  {!visible && !isPending && (
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
