"use client";

import { useQuery } from "@tanstack/react-query";
import { listProjects } from "@/lib/api/projects";
import { ProjectCard } from "./ProjectCard";
import { Skeleton } from "@/components/ui/skeleton";

export function ProjectsList() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });

  if (isPending) {
    return (
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-40" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rounded-lg border border-border-default bg-surface-raised p-8 text-center text-fg-secondary">
        Не удалось загрузить проекты. Попробуйте обновить страницу.
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border-default bg-surface-raised p-12 text-center space-y-3">
        <h3 className="text-lg font-medium">Здесь будет ваш первый проект</h3>
        <p className="text-sm text-fg-secondary max-w-md mx-auto">
          Нажмите «Новый проект» сверху, выберите шаблон, и пишите промпт.
        </p>
      </div>
    );
  }

  return (
    <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {data.map((p) => (
        <ProjectCard key={p.id} project={p} />
      ))}
    </div>
  );
}
