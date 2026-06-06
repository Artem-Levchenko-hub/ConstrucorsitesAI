"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PanelRightClose } from "lucide-react";
import { toast } from "sonner";
import { listSnapshots, rollback } from "@/lib/api/snapshots";
import type { Project } from "@/lib/api/types";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspaceStore } from "@/store/workspace";
import { SnapshotCard } from "./SnapshotCard";

export function Timeline({ project }: { project: Project }) {
  const qc = useQueryClient();
  const selectedSnapshotId = useWorkspaceStore((s) => s.selectedSnapshotId);
  const selectSnapshot = useWorkspaceStore((s) => s.selectSnapshot);
  const toggleTimeline = useWorkspaceStore((s) => s.toggleTimeline);

  const { data, isPending } = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });

  const rollbackMutation = useMutation({
    mutationFn: (snapshotId: string) => rollback(project.id, snapshotId),
    onSuccess: () => {
      toast.success("Откат выполнен", {
        description: "Создан новый snapshot — старая версия осталась в истории",
      });
      qc.invalidateQueries({ queryKey: ["snapshots", project.id] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      selectSnapshot(null);
    },
    onError: () =>
      toast.error("Не удалось откатиться", {
        description: "Попробуйте ещё раз через секунду",
      }),
  });

  return (
    <div className="flex flex-col h-full bg-surface-panel-dark">
      <div className="h-8 flex items-center justify-between px-3 border-b border-border-subtle">
        <span className="text-[10px] font-mono text-fg-tertiary uppercase tracking-wider">
          История
        </span>
        <div className="flex items-center gap-1.5">
          {data && (
            <span className="text-[10px] font-mono text-fg-tertiary tabular-nums">
              {data.length}
            </span>
          )}
          <button
            type="button"
            onClick={toggleTimeline}
            aria-label="Свернуть историю версий"
            title="Свернуть историю"
            className="-mr-1 flex h-6 w-6 items-center justify-center rounded text-fg-tertiary transition-colors hover:bg-surface-overlay hover:text-fg-secondary"
          >
            <PanelRightClose className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <ScrollArea className="flex-1">
        {/*
          Карточки используют framer-motion `layout` — их реальные размеры
          (paddings, font-size) меньше прежних, и плавно растут на hover.
          Никаких CSS-scale-трюков → никакого фантомного пустого места между
          карточками. Стандартный space-y-2 даёт чистый ритм ленты.
        */}
        <div className="p-2 space-y-2">
          {isPending && (
            <>
              <Skeleton className="h-40" />
              <Skeleton className="h-40" />
            </>
          )}

          {!isPending && data && data.length === 0 && (
            <div className="text-center text-xs text-fg-tertiary leading-5 py-6">
              Здесь будет лента версий.
              <br />
              Каждый промпт = новый snapshot.
            </div>
          )}

          {data?.map((snap, i) => (
            <SnapshotCard
              key={snap.id}
              snapshot={snap}
              versionNumber={data.length - i}
              isCurrent={project.current_snapshot_id === snap.id}
              isSelected={selectedSnapshotId === snap.id}
              onSelect={() => selectSnapshot(snap.id)}
              onRollback={() => rollbackMutation.mutate(snap.id)}
              rolling={rollbackMutation.isPending}
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
