"use client";

import { useState } from "react";
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
}: {
  project: Project;
  index: number;
}) {
  const [deleteOpen, setDeleteOpen] = useState(false);
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
    <article className="group relative overflow-hidden rounded-[12px] border border-[#2b2d32] bg-[#191b20] transition hover:border-[#828491]">
      <div className="absolute right-2 top-2 z-20">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Действия с проектом ${project.name}`}
              className="size-11 bg-[#191b20]/90 text-[#9fa1b1] shadow-sm backdrop-blur hover:bg-[#191b20] hover:text-white"
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
      />

      <Link
        href={`/max/${project.id}`}
        className="relative block aspect-[16/10] overflow-hidden bg-[#2b2d32]"
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
            <div className="w-[82px] rounded-[14px] border-4 border-[#25272b] bg-[#191b20] p-2 shadow-lg">
              <div className="h-8 rounded-[6px] bg-[#4f81f7]" />
              <div className="mt-2 h-2 rounded bg-[#2b2d32]" />
              <div className="mt-1 h-2 w-2/3 rounded bg-[#2b2d32]" />
            </div>
          </div>
        )}
        <span className="absolute left-3 top-3 rounded-full bg-[#121519] px-2.5 py-1 font-mono text-[9px] uppercase text-white">
          MAX Mini App
        </span>
      </Link>

      <div className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link href={`/max/${project.id}`} className="font-semibold hover:text-[#6a95fa]">
              {project.name}
            </Link>
            <p className="mt-1 text-xs text-[#828491]">
              Обновлён {new Date(project.updated_at).toLocaleDateString("ru-RU")}
            </p>
          </div>
          <span className="font-mono text-[9px] text-[#828491]">
            {String(index + 1).padStart(2, "0")}
          </span>
        </div>

        <div className="mt-5 border-t border-[#25272b] pt-4">
          <div className="flex items-center justify-between gap-3 text-[10px] text-[#828491]">
            <span>Путь до запуска</span>
            <span className="tabular-nums">
              {readiness.isSuccess
                ? `${journey.completedCount} из ${journey.total}`
                : readiness.isError
                  ? "Нет статуса"
                  : "Проверяем…"}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#25272b]">
            <div
              className="h-full rounded-full bg-[#4f81f7] transition-[width]"
              style={{ width: `${readiness.isSuccess ? journey.progress : 0}%` }}
            />
          </div>

          <div className="mt-4 flex items-center gap-2">
            {readiness.isError ? (
              <CircleAlert className="size-3.5 shrink-0 text-[#6a95fa]" />
            ) : (
              <span className="size-2 shrink-0 rounded-full bg-[#4f81f7]" />
            )}
            <p className="min-w-0 flex-1 truncate text-xs font-medium text-white">
              {readiness.isError
                ? "Не удалось проверить готовность"
                : readiness.isLoading
                  ? "Получаем актуальное состояние"
                  : nextStage?.label ?? "Приложение готово к работе"}
            </p>
          </div>

          <Link
            href={nextHref}
            className="mt-4 flex min-h-10 items-center justify-between rounded-[8px] border border-[#2b2d32] px-3 text-xs font-semibold text-white transition-colors hover:border-[#4f81f7] hover:bg-[#4f81f7] hover:text-[#121519]"
          >
            {nextStage?.actionLabel ?? "Открыть управление"}
            <ArrowRight className="size-3.5 text-[#4f81f7] transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      </div>
    </article>
  );
}
