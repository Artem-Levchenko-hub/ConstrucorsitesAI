"use client";

import type { Project } from "@/lib/api/types";
import { useWorkspaceStore } from "@/store/workspace";
import { ChatPanel } from "./ChatPanel";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";

export function Workspace({ project }: { project: Project }) {
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);

  return (
    // Свёрнутая панель уходит в 0px (не в рельс) — preview занимает всю ширину.
    // Развернуть можно иконкой в верхнем тулбаре preview (слева — чат, справа —
    // история); свернуть — шевроном в шапке самой панели. Анимируем grid-
    // template-columns; prefers-reduced-motion глушит транзишн в globals.css.
    <div
      className="flex-1 grid min-h-0 transition-[grid-template-columns] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
      style={{
        gridTemplateColumns: `${chatCollapsed ? "0px" : "320px"} minmax(0, 1fr) ${
          timelineCollapsed ? "0px" : "220px"
        }`,
      }}
    >
      <div className="min-h-0 overflow-hidden">
        {!chatCollapsed && (
          <ChatPanel projectId={project.id} projectSlug={project.slug} />
        )}
      </div>
      <div className="min-h-0">
        <PreviewFrame project={project} />
      </div>
      <div className="min-h-0 overflow-hidden">
        {!timelineCollapsed && <Timeline project={project} />}
      </div>
    </div>
  );
}
