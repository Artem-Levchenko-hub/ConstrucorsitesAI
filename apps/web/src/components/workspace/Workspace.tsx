"use client";

import { PanelLeftOpen, PanelRightOpen, type LucideIcon } from "lucide-react";
import type { Project } from "@/lib/api/types";
import { useWorkspaceStore } from "@/store/workspace";
import { ChatPanel } from "./ChatPanel";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";

/**
 * Slim rail shown in place of a collapsed side panel. The expand chevron sits
 * in a h-10 zone so it lines up with the panel headers (and the in-panel
 * collapse chevron) — collapse and expand live on the same edge.
 */
function CollapsedRail({
  icon: Icon,
  label,
  onExpand,
}: {
  icon: LucideIcon;
  label: string;
  onExpand: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onExpand}
      aria-label={label}
      title={label}
      className="flex h-full w-full flex-col items-center text-fg-tertiary transition-colors hover:bg-surface-raised hover:text-fg-secondary"
    >
      <span className="flex h-10 w-full items-center justify-center">
        <Icon className="h-4 w-4" />
      </span>
    </button>
  );
}

export function Workspace({ project }: { project: Project }) {
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const toggleTimeline = useWorkspaceStore((s) => s.toggleTimeline);

  return (
    // Колонки реактивны: свёрнутая панель сжимается до тонкого рельса (44px) с
    // шевроном-развернуть — preview забирает место. Анимируем grid-template-
    // columns (CSS-transition); prefers-reduced-motion глушит её в globals.css.
    <div
      className="flex-1 grid min-h-0 transition-[grid-template-columns] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
      style={{
        gridTemplateColumns: `${chatCollapsed ? "44px" : "320px"} minmax(0, 1fr) ${
          timelineCollapsed ? "44px" : "220px"
        }`,
      }}
    >
      <div className="min-h-0 overflow-hidden">
        {chatCollapsed ? (
          <CollapsedRail
            icon={PanelLeftOpen}
            label="Развернуть чат"
            onExpand={toggleChat}
          />
        ) : (
          <ChatPanel projectId={project.id} projectSlug={project.slug} />
        )}
      </div>
      <div className="min-h-0">
        <PreviewFrame project={project} />
      </div>
      <div className="min-h-0 overflow-hidden">
        {timelineCollapsed ? (
          <CollapsedRail
            icon={PanelRightOpen}
            label="Развернуть историю"
            onExpand={toggleTimeline}
          />
        ) : (
          <Timeline project={project} />
        )}
      </div>
    </div>
  );
}
