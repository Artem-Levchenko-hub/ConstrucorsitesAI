"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ExternalLink,
  Loader2,
  RotateCw,
  Smartphone,
  Tablet,
  Monitor,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { listSnapshots } from "@/lib/api/snapshots";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace";
import type { Project, Snapshot } from "@/lib/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { shortSha, cn } from "@/lib/utils";

type Device = "mobile" | "tablet" | "desktop";
const DEVICE_WIDTH: Record<Device, string> = {
  mobile: "390px",
  tablet: "768px",
  desktop: "100%",
};

export function PreviewFrame({ project }: { project: Project }) {
  const selectedSnapshotId = useWorkspaceStore((s) => s.selectedSnapshotId);
  const { data: snapshots, isPending } = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });

  const visible: Snapshot | undefined = selectedSnapshotId
    ? snapshots?.find((s) => s.id === selectedSnapshotId)
    : snapshots?.[0];

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

  return (
    <div className="flex flex-col h-full bg-surface-base">
      <div className="h-10 flex items-center justify-between px-4 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
            Preview
          </span>
          {visible?.commit_sha && (
            <span className="text-[11px] font-mono text-fg-tertiary">
              · {shortSha(visible.commit_sha)}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {/* Device toggle */}
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

          <Button size="sm" variant="ghost" asChild>
            <a href={publicUrl} target="_blank" rel="noreferrer">
              <ExternalLink className="h-3.5 w-3.5" />
              Открыть
            </a>
          </Button>
        </div>
      </div>

      <div className="flex-1 p-4 overflow-hidden">
        <div className="h-full w-full rounded-lg border border-border-default bg-surface-raised overflow-hidden flex flex-col">
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
              {visible && (
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
        </div>
      </div>
    </div>
  );
}
