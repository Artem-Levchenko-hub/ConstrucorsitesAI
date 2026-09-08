"use client";

import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, Smartphone } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { MaxEditorLayout } from "./MaxEditorLayout";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { DownloadButton } from "@/components/workspace/DownloadButton";
import { listProjects } from "@/lib/api/projects";
import { listProjectVersions, listSnapshots, rollback as rollbackSnapshot } from "@/lib/api/snapshots";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project, Snapshot } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import { upsertSnapshotNewest } from "@/lib/snapshot-history";
import { MaxLaunchPanel } from "./MaxLaunchPanel";
import { MaxLivePreview } from "./MaxLivePreview";
import { MaxAccountMenu } from "./MaxAccountMenu";
import { MaxProjectNav } from "./MaxProjectNav";
import { MaxUsageBreakdown } from "./MaxUsageBreakdown";

export function MaxWorkspaceShell({
  project,
  email,
}: {
  project: Project;
  email: string;
}) {
  const [launchOpen, setLaunchOpen] = useState(false);
  const [versionSelection, setVersionSelection] = useState<{
    versionId: string;
    projectId: string;
  } | null>(null);
  const queryClient = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const snapshots = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id),
  });
  const readiness = useQuery({
    queryKey: ["max-readiness", project.id],
    queryFn: () => getMaxReadiness(project.id),
    retry: false,
    refetchInterval: 10_000,
  });
  const journey = getMaxJourney(project.id, readiness.data?.items ?? []);
  const nextStage = readiness.isSuccess ? journey.currentStage : undefined;
  const launchLabel = readiness.isLoading
    ? "Проверяем…"
    : nextStage
      ? `Продолжить · ${journey.completedCount}/${journey.total}`
      : "Проверить запуск";
  const maxProjects = useMemo(
    () => (projects.data ?? []).filter((item) => item.template === "max_miniapp"),
    [projects.data],
  );
  const currentSnapshotId = snapshots.data?.[0]?.id ?? project.current_snapshot_id;
  const history = useInfiniteQuery({
    queryKey: ["project-versions", project.id],
    queryFn: async ({ pageParam, signal }) => {
      // Capture before the request: a late response for an earlier HEAD must
      // not authorize live preview of that HEAD's selected user version.
      const snapshotIdAtRequest = queryClient.getQueryData<Snapshot[]>(["snapshots", project.id])?.[0]?.id
        ?? project.current_snapshot_id;
      const page = await listProjectVersions(project.id, pageParam, signal);
      return { ...page, snapshotIdAtRequest };
    },
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5_000,
  });
  const versions = useMemo(() => {
    const seen = new Set<string>();
    return (history.data?.pages.flatMap((page) => page.versions) ?? []).filter((version) => {
      if (seen.has(version.id)) return false;
      seen.add(version.id); return true;
    });
  }, [history.data]);
  const historyCurrent = !!history.data?.pages.length
    && history.data.pages.every((page) => page.snapshotIdAtRequest === currentSnapshotId);
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["project-versions", project.id] });
  }, [currentSnapshotId, project.id, queryClient]);
  // Selection belongs to the project, never the moving HEAD. Clear during
  // render so switching A → B → A cannot resurrect a stale selection.
  if (versionSelection && versionSelection.projectId !== project.id) setVersionSelection(null);
  const selectedVersionId = versionSelection?.projectId === project.id ? versionSelection.versionId : null;

  const rollbackMutation = useMutation({
    mutationFn: (snapshotId: string) =>
      rollbackSnapshot(project.id, snapshotId),
    onSuccess: (snapshot) => {
      queryClient.setQueryData<Snapshot[]>(
        ["snapshots", project.id],
        (previous) => upsertSnapshotNewest(previous, snapshot),
      );
      setVersionSelection(null);
      void queryClient.invalidateQueries({ queryKey: ["project-versions", project.id] });
      toast.success("Версия восстановлена", {
        description:
          "Она стала текущей, а прежнее состояние осталось в истории.",
      });
      void queryClient.invalidateQueries({
        queryKey: ["snapshots", project.id],
      });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({
        queryKey: ["max-managed-kit-sync", project.id],
      });
      void queryClient.invalidateQueries({
        queryKey: ["max-preview-session", project.id],
      });
    },
    onError: (error) => {
      toast.error("Не удалось восстановить версию", {
        description:
          error instanceof Error
            ? error.message
            : "Текущая версия не изменилась. Повторите попытку.",
      });
    },
  });

  function selectVersion(versionId: string | null) {
    setVersionSelection(
      versionId ? { versionId, projectId: project.id } : null,
    );
  }

  const preview = (
    <MaxLivePreview
      key={project.id}
      project={project}
      versions={versions}
      historyCurrent={historyCurrent}
      historyError={history.isError}
      hasOlder={history.hasNextPage}
      loadingOlder={history.isFetchingNextPage}
      onLoadOlder={() => { void (history.isError && !history.isFetchNextPageError ? history.refetch() : history.fetchNextPage()); }}
      snapshotsLoading={history.isPending}
      currentSnapshotId={currentSnapshotId}
      selectedVersionId={selectedVersionId}
      onSelectVersion={selectVersion}
      onRestoreSnapshot={async (snapshotId) => { await rollbackMutation.mutateAsync(snapshotId); }}
      restoringSnapshot={rollbackMutation.isPending}
    />
  );

  return (
    <MaxEditorLayout
      project={project}
      launchStatus={launchLabel}
      launchOpen={launchOpen}
      onLaunchChange={setLaunchOpen}
      preview={preview}
      launch={<MaxLaunchPanel project={project} onClose={() => setLaunchOpen(false)} />}
      navigation={
        <>
          <div className="max-editor-navigation" data-testid="max-navigation-scroll">
            <Link href="/max" className="max-editor-nav-link">
              <LayoutGrid className="size-4" /> Все проекты
            </Link>
            <p className="max-editor-caption">Ваши приложения</p>
            <nav className="max-projects-scroll max-h-48 overflow-y-auto" aria-label="Ваши Mini Apps" data-testid="max-projects-scroll">
              {maxProjects.map((item) => (
                <Link key={item.id} href={`/max/${item.id}`} className="max-editor-nav-link" aria-current={item.id === project.id ? "page" : undefined}>
                  <Smartphone className="size-4 shrink-0" />
                  <span className="truncate">{item.name}</span>
                </Link>
              ))}
            </nav>
            <p className="max-editor-caption">Текущий проект</p>
            <MaxProjectNav projectId={project.id} active="editor" showProgress={false} />
          </div>
          <div className="max-editor-account">
            <MaxAccountMenu email={email} />
          </div>
        </>
      }
      tools={
        <>
          <MaxUsageBreakdown projectId={project.id} />
          {versions.length > 0 && <DownloadButton projectId={project.id} projectSlug={project.slug} />}
          <Link className="max-editor-nav-link" href={`/max/${project.id}/integrations`}>Интеграции</Link>
        </>
      }
    >
      <ChatPanel
        projectId={project.id}
        projectSlug={project.slug}
        currentSnapshotId={currentSnapshotId}
        mode="max"
        basePath={`/max/${project.id}`}
        embedded
      />
    </MaxEditorLayout>
  );
}
