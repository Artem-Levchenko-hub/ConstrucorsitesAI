"use client";

import { useRef, useState, type RefObject } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CircleAlert, MoreVertical, Trash2 } from "lucide-react";
import Link from "next/link";

import { DeleteProjectDialog } from "@/components/projects/DeleteProjectDialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";

export function MaxStudioProjectCard({
  project,
  index,
  successFocusRef,
}: {
  project: Project;
  index: number;
  successFocusRef: RefObject<HTMLElement | null>;
}) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const readiness = useQuery({
    queryKey: ["max-readiness", project.id],
    queryFn: () => getMaxReadiness(project.id),
    retry: false,
    staleTime: 20_000,
  });
  const journey = getMaxJourney(project.id, readiness.data?.items ?? []);
  const nextStage = readiness.isSuccess ? journey.currentStage : undefined;
  const nextHref = nextStage?.href ?? `/max/${project.id}/dashboard`;

  return (
    <article className="group relative overflow-hidden rounded-[12px] border border-border-default bg-surface-raised transition-colors hover:border-border-strong">
      <div className="absolute right-2 top-2 z-20">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              ref={menuTriggerRef}
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Действия с проектом ${project.name}`}
              className="size-11 bg-surface/90 text-fg-secondary shadow-sm backdrop-blur hover:bg-surface hover:text-fg-primary"
            >
              <MoreVertical className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              className="text-danger focus:text-danger"
              onSelect={() => setDeleteOpen(true)}
            >
              <Trash2 className="size-4" />
              Удалить проект
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <DeleteProjectDialog
        project={project}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        returnFocusRef={menuTriggerRef}
        successFocusRef={successFocusRef}
      />

      <Link
        href={`/max/${project.id}`}
        className="relative block aspect-[16/10] overflow-hidden bg-surface-3"
        aria-label={`Открыть редактор проекта ${project.name}`}
      >
        {project.preview_url ? (
          // The API returns a remote preview thumbnail, so next/image cannot know its host.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={project.preview_url}
            alt=""
            className="h-full w-full object-cover object-top transition-transform duration-200 group-hover:scale-[1.01]"
          />
        ) : (
          <div className="grid h-full place-items-center">
            <div className="w-[82px] rounded-[14px] border-4 border-[#171716] bg-white p-2 shadow-lg">
              <div className="h-8 rounded-[6px] bg-accent" />
              <div className="mt-2 h-2 rounded bg-surface-3" />
              <div className="mt-1 h-2 w-2/3 rounded bg-surface-3" />
            </div>
          </div>
        )}
        <span className="absolute left-3 top-3 rounded-full bg-[#171716] px-2.5 py-1 font-mono text-[9px] uppercase text-white">
          MAX Mini App
        </span>
      </Link>

      <div className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link href={`/max/${project.id}`} className="font-semibold hover:text-accent">
              {project.name}
            </Link>
            <p className="mt-1 text-xs text-[#8d887f]">
              Обновлён {new Date(project.updated_at).toLocaleDateString("ru-RU")}
            </p>
          </div>
          <span className="font-mono text-[9px] text-[#aaa59b]">
            {String(index + 1).padStart(2, "0")}
          </span>
        </div>

        <div className="mt-5 border-t border-[#e7e3da] pt-4">
          <div className="flex items-center justify-between gap-3 text-[10px] text-[#8d887f]">
            <span>Путь до запуска</span>
            <span className="tabular-nums">
              {readiness.isSuccess
                ? `${journey.completedCount} из ${journey.total}`
                : readiness.isError
                  ? "Нет статуса"
                  : "Проверяем…"}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e7e3da]">
            <div
              className="h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${readiness.isSuccess ? journey.progress : 0}%` }}
            />
          </div>

          <div className="mt-4 flex items-center gap-2">
            {readiness.isError ? (
              <CircleAlert className="size-3.5 shrink-0 text-[#b98618]" />
            ) : (
              <span className="size-2 shrink-0 rounded-full bg-accent" />
            )}
            <p className="min-w-0 flex-1 truncate text-xs font-medium text-[#171716]">
              {readiness.isError
                ? "Не удалось проверить готовность"
                : readiness.isLoading
                  ? "Получаем актуальное состояние"
                  : nextStage?.label ?? "Приложение готово к работе"}
            </p>
          </div>

          <Link
            href={nextHref}
            className="mt-4 flex min-h-11 items-center justify-between rounded-[8px] border border-border-default px-3 text-xs font-semibold text-fg-primary transition-colors hover:border-accent hover:bg-accent/[.04]"
          >
            {nextStage?.actionLabel ?? "Открыть управление"}
            <ArrowRight className="size-3.5 text-accent transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      </div>
    </article>
  );
}
