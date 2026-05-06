"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { Project } from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatRelativeTime } from "@/lib/utils";

const TEMPLATE_LABEL: Record<Project["template"], string> = {
  blank: "Чистый холст",
  landing: "Лендинг",
  portfolio: "Портфолио",
  blog: "Блог",
};

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      href={`/projects/${project.id}`}
      className="group focus:outline-none"
    >
      <Card className="h-full hover:border-border-strong transition-colors">
        <CardContent className="p-6 space-y-4">
          <div className="flex items-center justify-between">
            <Badge variant="outline" className="font-mono">
              {TEMPLATE_LABEL[project.template]}
            </Badge>
            <ArrowUpRight className="h-4 w-4 text-fg-tertiary group-hover:text-fg-primary transition-colors" />
          </div>

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
  );
}
