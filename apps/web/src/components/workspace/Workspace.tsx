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
        gridTemplateColumns: "320px minmax(0, 1fr) 320px",
      }}
    >
      <div className="border-r border-border-default min-h-0">
        <ChatPanel projectId={project.id} projectSlug={project.slug} />
      </div>
      <div className="min-h-0">
        <PreviewFrame project={project} />
      </div>
      <div className="border-l border-border-default min-h-0">
        <Timeline project={project} />
      </div>
    </div>
  );
}
