"use client";

import type { Project } from "@/lib/api/types";
import { ChatPanel } from "./ChatPanel";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";

export function Workspace({ project }: { project: Project }) {
  return (
    <div
      className="flex-1 grid min-h-0"
      style={{
        // Колонка с историей версий сжата с 320 до 220px — основная площадь
        // отдаётся preview-iframe (центральная колонка). Чат слева оставлен
        // в 320px: его читаемость важнее «лишнего» пространства превью.
        gridTemplateColumns: "320px minmax(0, 1fr) 220px",
      }}
    >
      <div className="border-r border-border-subtle min-h-0">
        <ChatPanel projectId={project.id} projectSlug={project.slug} />
      </div>
      <div className="min-h-0">
        <PreviewFrame project={project} />
      </div>
      <div className="border-l border-border-subtle min-h-0">
        <Timeline project={project} />
      </div>
    </div>
  );
}
