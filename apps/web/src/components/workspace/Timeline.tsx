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
    <div
      className="relative flex flex-col h-full backdrop-blur-xl"
      style={{
        // Mirror of ChatPanel but with the cyan accent so the two side panels
        // read as distinct surfaces. The Workspace ambient cyan orb anchored
        // bottom-right glows through this layer.
        background:
          "linear-gradient(180deg, rgb(92 184 255 / 0.18) 0%, rgb(92 184 255 / 0.08) 30%, rgb(92 184 255 / 0.04) 100%), radial-gradient(ellipse 120% 60% at 100% 100%, rgb(92 184 255 / 0.28), transparent 70%), rgb(8 8 12 / 0.55)",
      }}
    >
      {/* Top accent bar — 3 px cyan gradient to mirror the violet bar on
          ChatPanel. Side-panel identity reads at a glance. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[3px] bg-gradient-to-l from-accent-secondary via-accent-secondary/60 to-transparent"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "radial-gradient(circle at 1px 1px, #ffffff 1px, transparent 0)",
          backgroundSize: "24px 24px",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0 w-px bg-gradient-to-b from-transparent via-accent-secondary/40 to-transparent"
      />

      <div className="relative h-8 flex items-center justify-between gap-2 px-3 border-b border-border-subtle">
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
