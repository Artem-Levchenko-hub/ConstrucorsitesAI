"use client";

import type { Project } from "@/lib/api/types";
import { ChatPanel } from "./ChatPanel";
import { PreviewFrame } from "./PreviewFrame";
import { Timeline } from "./Timeline";

export function Workspace({ project }: { project: Project }) {
  return (
    <div
      className="relative flex-1 grid min-h-0"
      style={{
        // Колонка с историей версий сжата с 320 до 220px — основная площадь
        // отдаётся preview-iframe (центральная колонка). Чат слева оставлен
        // в 320px: его читаемость важнее «лишнего» пространства превью.
        gridTemplateColumns: "320px minmax(0, 1fr) 220px",
      }}
    >
      {/* Ambient orbs anchored in the workspace corners. They sit behind every
          panel via z-index: -1 so they show through the glass panel backgrounds
          without affecting layout. Each panel still controls its own visible
          colour — the orbs only contribute extra warmth at the edges. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 -left-40 h-[32rem] w-[32rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgb(124 92 255 / 0.55) 0%, rgb(124 92 255 / 0.18) 40%, transparent 70%)",
          zIndex: -1,
          filter: "blur(50px)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-40 -right-40 h-[32rem] w-[32rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgb(92 184 255 / 0.50) 0%, rgb(92 184 255 / 0.15) 40%, transparent 70%)",
          zIndex: -1,
          filter: "blur(50px)",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-1/3 left-1/2 -translate-x-1/2 h-[24rem] w-[24rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgb(236 92 255 / 0.25) 0%, transparent 70%)",
          zIndex: -1,
          filter: "blur(60px)",
        }}
      />

      <div className="relative border-r border-border-subtle min-h-0">
        <ChatPanel projectId={project.id} projectSlug={project.slug} />
      </div>
      <div className="relative min-h-0">
        <PreviewFrame project={project} />
      </div>
      <div className="relative border-l border-border-subtle min-h-0">
        <Timeline project={project} />
      </div>
    </div>
  );
}
