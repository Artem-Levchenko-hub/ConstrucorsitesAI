"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import type { Project } from "@/lib/api/types";
import { useMaxPreviewHistory } from "@/lib/use-max-preview-history";
import { MaxLivePreview } from "./MaxLivePreview";
import "./max-editor.css";

/** Stable authenticated parent for the mutable draft and its renewable owner session. */
export function MaxOwnerPreviewPage({ project }: { project: Project }) {
  const { currentSnapshotId, versions, historyCurrent, history } = useMaxPreviewHistory(project);
  return (
    <main data-product-shell data-max-editor className="flex h-full min-h-0 flex-col bg-surface-base">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-border-default px-4 py-3">
        <Link href={`/max/${project.id}`} className="inline-flex min-h-11 items-center gap-2 text-sm text-fg-secondary">
          <ArrowLeft className="size-4" /> Вернуться в редактор
        </Link>
        <span className="truncate text-sm text-fg-primary">{project.name}</span>
      </header>
      <div className="min-h-0 flex-1">
        <MaxLivePreview
          key={project.id}
          project={project}
          versions={versions}
          historyCurrent={historyCurrent}
          historyError={history.isError}
          hasOlder={history.hasNextPage}
          snapshotsLoading={history.isPending}
          currentSnapshotId={currentSnapshotId}
          selectedVersionId={null}
          onSelectVersion={() => {}}
          restoringSnapshot={false}
          showOpenSeparately={false}
        />
      </div>
    </main>
  );
}
