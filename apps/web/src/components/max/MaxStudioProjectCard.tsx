"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Copy, MoreVertical, Trash2 } from "lucide-react";
import Link from "next/link";
import { DeleteProjectDialog } from "@/components/projects/DeleteProjectDialog";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { getMaxIntegration } from "@/lib/api/max-integration";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import { maxProjectStateView } from "@/lib/max-project-state";
import { relativeDateLabel } from "@/lib/relative-date";
import { toast } from "sonner";

/** Полоса пройденного пути: шесть делений вместо мелкой подписи «1 из 6». */
function JourneyProgress({ done, total }: { done: number; total: number }) {
  return (
    <span className="max-project-progress" role="img" aria-label={`Пройдено ${done} из ${total} шагов до запуска`}>
      {Array.from({ length: total }, (_, index) => (
        <span key={index} data-done={index < done} />
      ))}
    </span>
  );
}

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
  const status = readiness.isError ? "error" : readiness.isSuccess ? "ready" : "loading";
  const state = maxProjectStateView(nextStage, status);
  const live = status === "ready" && !nextStage;
  // Адрес запрашиваем только у работающих приложений: у остальных его ещё нет,
  // и лишний запрос на каждую строку списка ничего бы не показал.
  const integration = useQuery({
    queryKey: ["max-integration", project.id],
    queryFn: () => getMaxIntegration(project.id),
    enabled: live,
    retry: false,
    staleTime: 60_000,
  });
  const appUrl = live ? integration.data?.app_url ?? null : null;
  const changed = relativeDateLabel(project.updated_at);

  return (
    <article className="max-project-row">
      <div className="max-project-identity">
        <MaxProjectArtwork key={safeProjectPreviewUrl(project.preview_url) ?? "fallback"} project={project} />
        <div className="min-w-0">
          <h2><Link href={`/max/${project.id}`} className="max-project-editor-link" aria-label={`Открыть редактор приложения ${project.name}`}>{project.name}</Link></h2>
          {changed && <p>{live ? "Опубликован" : "Изменён"} {changed}</p>}
          {appUrl && (
            <button
              type="button"
              className="max-project-url"
              onClick={() => {
                void navigator.clipboard?.writeText(appUrl)
                  .then(() => toast.success("Адрес скопирован"))
                  .catch(() => toast.error("Не удалось скопировать адрес", { description: "Скопируйте его вручную из раздела «После запуска»." }));
              }}
            >
              {appUrl.replace(/^https?:\/\//, "")}
              <Copy className="size-3 shrink-0" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      <div className="max-project-state" data-project-status={state.tone}>
        <strong>{state.title}</strong>
        {status === "ready" && <JourneyProgress done={journey.completedCount} total={journey.total} />}
        <small>{state.hint}</small>
      </div>
      <div className="max-project-actions">
        <Link href={nextHref} className="max-project-next">{nextStage?.actionLabel ?? (live ? "Открыть управление" : "Открыть проект")}<ArrowRight className="size-4 shrink-0" /></Link>
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
