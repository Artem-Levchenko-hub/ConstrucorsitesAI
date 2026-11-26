"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getSnapshotChanges } from "@/lib/api/snapshots";

type VersionTarget = { projectId: string; snapshotId: string };

export function VersionChangesDisclosure({ projectId, snapshotId }: VersionTarget) {
  const [open, setOpen] = useState(false);

  return (
    <details onToggle={event => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer rounded py-3 text-xs font-medium leading-5 text-fg-secondary hover:text-fg-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
        Что изменилось в этой версии
      </summary>
      {open && <VersionChanges projectId={projectId} snapshotId={snapshotId} />}
    </details>
  );
}

function VersionChanges({ projectId, snapshotId }: VersionTarget) {
  const { data, isError, isPending, refetch } = useQuery({
    queryKey: ["snapshot-changes", projectId, snapshotId],
    // Let the requested read fill the cache even if the disclosure closes.
    queryFn: () => getSnapshotChanges(projectId, snapshotId),
    staleTime: "static",
    networkMode: "always",
    retry: false,
    retryOnMount: false,
  });

  if (isError) {
    return (
      <div className="text-xs leading-5 text-fg-secondary">
        <p role="alert">Не удалось загрузить изменения.</p>
        <button
          type="button"
          onClick={event => {
            event.currentTarget.closest("details")?.querySelector("summary")?.focus();
            void refetch();
          }}
          className="min-h-11 rounded text-fg-primary underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Повторить загрузку
        </button>
      </div>
    );
  }

  return (
    <div role="status" className="text-xs leading-5 text-fg-secondary">
      {isPending ? "Загружаем изменения…" : data.length === 0 ? "Для этой версии описания изменений нет." : (
        <ul className="list-disc space-y-1 ps-4 [overflow-wrap:anywhere]">
          {data.map(change => <li key={change}>{change}</li>)}
        </ul>
      )}
    </div>
  );
}
