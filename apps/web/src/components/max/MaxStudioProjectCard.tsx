"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, CircleAlert, CircleDot, MoreVertical, Trash2 } from "lucide-react";
import Link from "next/link";
import { DeleteProjectDialog } from "@/components/projects/DeleteProjectDialog";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";

export function MaxStudioProjectCard({ project }: { project: Project }) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const readiness = useQuery({ queryKey: ["max-readiness", project.id], queryFn: () => getMaxReadiness(project.id), retry: false, staleTime: 20_000 });
  const journey = getMaxJourney(project.id, readiness.data?.items ?? []);
  const nextStage = readiness.isSuccess ? journey.currentStage : undefined;
  const nextHref = nextStage?.href ?? `/max/${project.id}/dashboard`;

  return (
    <article className="max-project-row">
      <div className="max-project-identity">
        <span className="max-project-monogram" aria-hidden="true">{Array.from(project.name.trim())[0]?.toUpperCase() || "М"}</span>
        <div className="min-w-0">
          <h2><Link href={`/max/${project.id}`}>{project.name}</Link></h2>
          <p>Обновлён {new Date(project.updated_at).toLocaleDateString("ru-RU")}</p>
        </div>
      </div>
      <div className="max-project-state">
        <p className={readiness.isError ? "text-warning" : readiness.isSuccess && !nextStage ? "text-success-fg" : "text-fg-secondary"}>
          {readiness.isError ? <CircleAlert className="mt-0.5 size-4 shrink-0" /> : readiness.isSuccess && !nextStage ? <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> : <CircleDot className="mt-0.5 size-4 shrink-0" />}
          <span>{readiness.isError ? "Не удалось проверить готовность" : readiness.isPending ? "Получаем актуальное состояние" : nextStage?.label ?? "Приложение готово к работе"}</span>
        </p>
        <small>{readiness.isSuccess ? `${journey.completedCount} из ${journey.total} шагов до запуска` : readiness.isError ? "Статус недоступен" : "Проверяем готовность…"}</small>
      </div>
      <div className="max-project-actions">
        <Link href={nextHref} className="max-project-next">{nextStage?.actionLabel ?? "Открыть управление"}<ArrowRight className="size-4 shrink-0" /></Link>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button type="button" variant="ghost" size="icon" aria-label={`Действия с проектом ${project.name}`} className="size-11 shrink-0"><MoreVertical className="size-4" /></Button></DropdownMenuTrigger>
          <DropdownMenuContent data-max-studio align="end">
            <DropdownMenuItem className="text-danger focus:text-danger" onSelect={() => setDeleteOpen(true)}><Trash2 className="size-4" />Удалить проект</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <DeleteProjectDialog project={project} open={deleteOpen} onOpenChange={setDeleteOpen} maxStudio />
    </article>
  );
}
