"use client";

import type { Project } from "@/lib/api/types";
import { useWorkspaceStore } from "@/store/workspace";
import { ChatPanel } from "./ChatPanel";
import { JoyBurst } from "./JoyBurst";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";
import { MaxLaunchPanel } from "@/components/max/MaxLaunchPanel";

export function Workspace({
  project,
  mode = "default",
}: {
  project: Project;
  mode?: "default" | "max";
}) {
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);
  const maxMode = mode === "max";

  return (
    // Свёрнутая панель уходит в 0px (не в рельс) — preview занимает всю ширину.
    // Развернуть можно иконкой в верхнем тулбаре preview (слева — чат, справа —
    // история); свернуть — шевроном в шапке самой панели. Анимируем grid-
    // template-columns; prefers-reduced-motion глушит транзишн в globals.css.
    <div
      className={`flex-1 grid min-h-0 transition-[grid-template-columns] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
        maxMode ? "max-workspace-grid" : ""
      }`}
      style={{
        gridTemplateColumns: `${chatCollapsed ? "0px" : maxMode ? "360px" : "320px"} minmax(0, 1fr) ${
          timelineCollapsed ? "0px" : maxMode ? "292px" : "220px"
        }`,
      }}
    >
      <div className="min-h-0 overflow-hidden">
        {!chatCollapsed && (
          <ChatPanel
            projectId={project.id}
            projectSlug={project.slug}
            mode={maxMode ? "max" : "default"}
            basePath={maxMode ? `/max/${project.id}` : `/projects/${project.id}`}
          />
        )}
      </div>
      <div className="relative min-h-0">
        <PreviewFrame project={project} defaultDevice={maxMode ? "mobile" : "desktop"} />
        {/* V3.8 — бренд-цветная reward-нота поверх preview на build-complete. */}
        <JoyBurst projectId={project.id} />
      </div>
      <div className="min-h-0 overflow-hidden">
        {!timelineCollapsed &&
          (maxMode ? (
            <MaxLaunchPanel project={project} />
          ) : (
            <Timeline project={project} />
          ))}
      </div>
    </div>
  );
}
