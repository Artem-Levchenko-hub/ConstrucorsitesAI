"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { listSnapshots } from "@/lib/api/snapshots";
import { Button } from "@/components/ui/button";
import { useWorkspaceStore } from "@/store/workspace";
import type { Project, Snapshot } from "@/lib/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { shortSha } from "@/lib/utils";
import { toast } from "sonner";

export function PreviewFrame({ project }: { project: Project }) {
  const selectedSnapshotId = useWorkspaceStore((s) => s.selectedSnapshotId);
  const { data: snapshots, isPending } = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });

  const visible: Snapshot | undefined = selectedSnapshotId
    ? snapshots?.find((s) => s.id === selectedSnapshotId)
    : snapshots?.[0];

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

        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            toast.info("В демо preview-окно симулировано", {
              description: `${project.slug}.omnia.ai откроется после деплоя`,
            })
          }
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Открыть
        </Button>
      </div>

      <div className="flex-1 p-4 overflow-hidden">
        <div className="h-full w-full rounded-lg border border-border-default bg-surface-raised overflow-hidden flex flex-col">
          <div className="h-9 border-b border-border-subtle flex items-center gap-1.5 px-3 shrink-0">
            <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
            <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
            <span className="w-2.5 h-2.5 rounded-full bg-border-strong" />
            <span className="ml-3 text-xs font-mono text-fg-tertiary truncate">
              https://{project.slug}.omnia.ai
            </span>
          </div>

          <div className="flex-1 relative bg-surface-base">
            {isPending && (
              <div className="absolute inset-0 p-4">
                <Skeleton className="w-full h-full" />
              </div>
            )}

            <AnimatePresence mode="wait">
              {visible && visible.preview_url && (
                <motion.img
                  key={visible.id}
                  src={visible.preview_url}
                  alt="Превью сайта"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3 }}
                  className="absolute inset-0 w-full h-full object-cover"
                />
              )}
              {visible && !visible.preview_url && !isPending && (
                <motion.div
                  key="rendering"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-fg-secondary"
                >
                  <Loader2 className="h-5 w-5 animate-spin text-accent" />
                  <div className="text-sm">Рендерим preview через Playwright…</div>
                  <div className="text-xs text-fg-tertiary font-mono">
                    {shortSha(visible.commit_sha)}
                  </div>
                </motion.div>
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
