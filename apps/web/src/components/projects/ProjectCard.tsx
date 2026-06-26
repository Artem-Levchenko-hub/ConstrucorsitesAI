"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowUpRight, MoreVertical, Trash2 } from "lucide-react";
import type { Project } from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatRelativeTime } from "@/lib/utils";
import { DeleteProjectDialog } from "./DeleteProjectDialog";

const TEMPLATE_LABEL: Record<Project["template"], string> = {
  blank: "Чистый холст",
  landing: "Лендинг",
  portfolio: "Портфолио",
  blog: "Блог",
  fullstack: "Full-stack",
  nextjs_entities: "SaaS на сущностях",
  code: "Код",
  realtime: "Realtime-чат",
};

export function ProjectCard({ project }: { project: Project }) {
  const [deleteOpen, setDeleteOpen] = useState(false);

  return (
    <div className="group relative">
      {/* Project menu — sibling of the navigation Link (not nested inside it),
          so opening it / deleting never triggers a page navigation. */}
      <div className="absolute right-2 top-2 z-10">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Действия с проектом"
              className="h-8 w-8 bg-surface-raised/80 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
            >
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              className="text-danger focus:text-danger"
              onSelect={() => setDeleteOpen(true)}
            >
              <Trash2 className="h-4 w-4" />
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
        href={`/projects/${project.id}`}
        className="block focus:outline-none"
      >
        <Card className="h-full overflow-hidden hover:border-border-strong transition-colors">
        {/* Mini preview — the current snapshot's rendered screenshot (top fold).
            16:10 matches the 1280×800 preview viewport, so no distortion. */}
        <div className="relative aspect-[16/10] overflow-hidden border-b border-border-subtle bg-surface-base">
          {project.preview_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={project.preview_url}
              alt={`Превью «${project.name}»`}
              loading="lazy"
              className="h-full w-full object-cover object-top transition-transform duration-300 group-hover:scale-[1.03]"
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-surface-raised to-surface-base">
              <span className="select-none text-4xl font-semibold text-fg-tertiary opacity-40">
                {project.name.trim().charAt(0).toUpperCase() || "?"}
              </span>
            </div>
          )}
          <ArrowUpRight className="absolute right-3 top-3 h-4 w-4 text-fg-tertiary opacity-0 transition-opacity group-hover:opacity-100" />
        </div>

        <CardContent className="p-5 space-y-3">
          <Badge variant="outline" className="font-mono">
            {TEMPLATE_LABEL[project.template]}
          </Badge>

          <div className="space-y-1">
            <h3 className="text-lg font-medium truncate">{project.name}</h3>
            <p className="text-xs font-mono text-fg-tertiary truncate">
              /p/{project.slug}
            </p>
          </div>

          <div className="text-xs text-fg-secondary pt-2 border-t border-border-subtle">
            Обновлён {formatRelativeTime(project.updated_at)}
          </div>
        </CardContent>
        </Card>
      </Link>
    </div>
  );
}
