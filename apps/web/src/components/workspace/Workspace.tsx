"use client";

import type { Project } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/store/workspace";
import { ChatPanel } from "./ChatPanel";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";

export function Workspace({ project }: { project: Project }) {
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);

  return (
    // Колонки реактивны: свёрнутая панель уходит в 0px, центральный preview
    // забирает место. Анимируем сам grid-template-columns (CSS-transition —
    // без JS-reflow-петли); prefers-reduced-motion глушит её в globals.css.
    // Чат — 320px (читаемость важнее), история — 220px.
    <div
      className="flex-1 grid min-h-0 transition-[grid-template-columns] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
      style={{
        gridTemplateColumns: `${chatCollapsed ? "0px" : "320px"} minmax(0, 1fr) ${
          timelineCollapsed ? "0px" : "220px"
        }`,
      }}
    >
      <div
        className={cn(
          "min-h-0 overflow-hidden",
          !chatCollapsed && "border-r border-border-subtle",
        )}
      >
        <ChatPanel projectId={project.id} projectSlug={project.slug} />
      </div>
      <div className="min-h-0">
        <PreviewFrame project={project} />
      </div>
      <div
        className={cn(
          "min-h-0 overflow-hidden",
          !timelineCollapsed && "border-l border-border-subtle",
        )}
      >
        <Timeline project={project} />
      </div>
    </div>
  );
}
