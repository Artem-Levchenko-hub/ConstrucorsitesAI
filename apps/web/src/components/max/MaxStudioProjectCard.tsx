"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, CircleAlert, Clock3, MoreVertical, Trash2, TriangleAlert, Wrench } from "lucide-react";
import Link from "next/link";
import { DeleteProjectDialog } from "@/components/projects/DeleteProjectDialog";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";

type ProjectStatusKind = "failed" | "needs-input" | "pending" | "ready" | "setup";

function safeProjectPreviewUrl(value: string | null | undefined): string | null {
  const candidate = value?.trim();
  if (!candidate) return null;

  try {
    const url = new URL(candidate, "https://max-studio.invalid");
    if (!(["http:", "https:"] as string[]).includes(url.protocol)) return null;
    if (url.username || url.password) return null;
    return candidate;
  } catch {
    return null;
  }
}

function MaxProjectArtwork({ project }: { project: Project }) {
  const [failed, setFailed] = useState(false);
  const previewUrl = safeProjectPreviewUrl(project.preview_url);

  if (!previewUrl || failed) {
    return (
      <span className="max-project-monogram" aria-hidden="true">
        {Array.from(project.name.trim())[0]?.toUpperCase() || "М"}
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className="max-project-thumbnail"
      src={previewUrl}
      alt={`Превью приложения «${project.name}»`}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

export function MaxStudioProjectCard({ project }: { project: Project }) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const readiness = useQuery({ queryKey: ["max-readiness", project.id], queryFn: () => getMaxReadiness(project.id), retry: false, staleTime: 20_000 });
  const journey = getMaxJourney(project.id, readiness.data?.items ?? []);
  const nextStage = readiness.isSuccess ? journey.currentStage : undefined;
  const nextHref = nextStage?.href ?? `/max/${project.id}/dashboard`;
  const statusKind: ProjectStatusKind = readiness.isError
    ? "failed"
    : readiness.isPending
      ? "pending"
      : !nextStage
        ? "ready"
        : nextStage.id === "build"
          ? "setup"
          : "needs-input";
  const StatusIcon = statusKind === "failed"
    ? TriangleAlert
    : statusKind === "pending"
      ? Clock3
      : statusKind === "ready"
        ? CheckCircle2
        : statusKind === "setup"
          ? Wrench
          : CircleAlert;

  return (
    <article className="max-project-row">
      <div className="max-project-identity">
        <MaxProjectArtwork key={safeProjectPreviewUrl(project.preview_url) ?? "fallback"} project={project} />
        <div className="min-w-0">
          <h2><Link href={`/max/${project.id}`}>{project.name}</Link></h2>
          <p>Обновлён {new Date(project.updated_at).toLocaleDateString("ru-RU")}</p>
        </div>
      </div>
      <div className="max-project-state">
        <p className={`max-project-status max-project-status--${statusKind}`} data-project-status={statusKind}>
          <StatusIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
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
