"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History } from "lucide-react";
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
      <div className="h-8 flex items-center justify-between gap-2 px-3 border-b border-border-subtle">
        <div className="flex items-center gap-1.5 min-w-0">
          <History className="h-3 w-3 text-accent/80" aria-hidden="true" />
          <span className="text-[10px] font-mono text-fg-tertiary uppercase tracking-wider">
            История
          </span>
          <div
            aria-hidden="true"
            className="flex-1 h-px bg-gradient-to-r from-border-subtle to-transparent ml-0.5"
          />
        </div>
        {data && (
          <span className="text-[10px] font-mono px-1.5 py-px rounded-md bg-surface-raised text-fg-tertiary border border-border-subtle tabular-nums">
            {data.length}
          </span>
        )}
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

          {data?.map((snap) => (
            <SnapshotCard
              key={snap.id}
              snapshot={snap}
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
