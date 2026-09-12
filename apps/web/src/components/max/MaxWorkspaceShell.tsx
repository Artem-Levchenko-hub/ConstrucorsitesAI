"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, Smartphone } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { MaxEditorLayout } from "./MaxEditorLayout";
import { ChatPanel, type ChatPanelAdaptationHandle } from "@/components/workspace/ChatPanel";
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
import { useMaxRestoration } from "@/lib/use-max-restoration";
import { MaxRestorationPanel } from "./MaxRestorationPanel";
import { useMaxAdaptation, type MaxAdaptationAttachment } from "@/lib/use-max-adaptation";
import { cancelRestoration, getRestoration } from "@/lib/api/restorations";
import { Button } from "@/components/ui/button";

export function MaxWorkspaceShell({
  project,
  email,
}: {
  project: Project;
  email: string;
}) {
  const adaptationRef = useRef<ChatPanelAdaptationHandle>(null);
  const adaptationPending = useRef(false);
  const [adaptationSubmitting, setAdaptationSubmitting] = useState(false);
  const adaptation = useMaxAdaptation(project.id);
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

  function applyRestoredSnapshot(snapshot: Snapshot) {
      const previousSnapshotId = queryClient.getQueryData<Snapshot[]>(["snapshots", project.id])?.[0]?.id;
      queryClient.setQueryData<Snapshot[]>(
        ["snapshots", project.id],
        (previous) => upsertSnapshotNewest(previous, snapshot),
      );
      setVersionSelection(null);
      // The HEAD effect owns the refetch only when the cached HEAD actually moves.
      void queryClient.invalidateQueries({
        queryKey: ["project-versions", project.id],
        refetchType: previousSnapshotId && previousSnapshotId !== snapshot.id
          ? "none" : "active",
      });
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
  }

  const rollbackMutation = useMutation({
    mutationFn: (snapshotId: string) => rollbackSnapshot(project.id, snapshotId),
    onSuccess: applyRestoredSnapshot,
    onError: (error) => {
      toast.error("Не удалось восстановить версию", {
        description:
          error instanceof Error
            ? error.message
            : "Текущая версия не изменилась. Повторите попытку.",
      });
    },
  });

  const restoration = useMaxRestoration({
    projectId: project.id, currentSnapshotId, onCompleted: applyRestoredSnapshot,
  });

  async function submitAdaptation(attachment: MaxAdaptationAttachment) {
    if (adaptationPending.current) return;
    adaptationPending.current = true;
    setAdaptationSubmitting(true);
    try {
      // A lost cancel response or F5 can leave an intent saved before cancellation.
      // Reconcile that exact operation only on this explicit click, never on mount.
      if (attachment.projectId !== project.id || attachment.reference.expected_draft_snapshot_id !== currentSnapshotId) {
        toast.error("Черновик изменился. Подготовьте восстановление выбранной версии заново.");
        return;
      }
      const reference = attachment.reference;
      let operation = await getRestoration(attachment.projectId, reference.operation_id);
      const matches = () => operation.id === reference.operation_id
        && operation.project_id === attachment.projectId
        && operation.base_draft_snapshot_id === reference.expected_draft_snapshot_id;
      if (!matches()) throw new Error("Данные подготовки изменились. Подготовьте выбранную версию заново.");
      if (operation.state !== "cancelled" && operation.can_cancel) {
        operation = await cancelRestoration(attachment.projectId, reference.operation_id);
      }
      if (!matches() || operation.state !== "cancelled") {
        toast.info("Отмена подготовки ещё не подтверждена. Повторите проверку позже.");
        return;
      }
      restoration.observeCancelled(operation);
      if (!adaptation.attach(attachment.prompt, reference, "ready")) {
        toast.error("Не удалось сохранить запрос. Повторите попытку.");
        return;
      }
      if (await adaptationRef.current?.submitAdaptation(attachment)) {
        adaptation.clear(attachment.reference.operation_id);
        toast.success("Адаптация запущена. Результат появится в редакторе.");
      } else {
        toast.error("Запуск адаптации не подтверждён. Запрос сохранён; можно повторить попытку.");
      }
    } catch {
      toast.error("Не удалось подтвердить отмену подготовки. Запрос сохранён; повторите попытку.");
    } finally {
      adaptationPending.current = false;
      setAdaptationSubmitting(false);
    }
  }

  function selectVersion(versionId: string | null) {
    setVersionSelection(
      versionId ? { versionId, projectId: project.id } : null,
    );
  }

  const preview = (onClose?: () => void) => (
    <MaxLivePreview
      key={project.id}
      onClose={onClose}
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
      onPrepareRestoration={restoration.prepare}
      restorationEnabled={restoration.enabled}
      restorationBusy={restoration.busy || restoration.active || restoration.hasPendingRequest}
    />
  );

  return (
    <MaxEditorLayout
      project={project}
      launchStatus={launchLabel}
      preview={preview}
      launch={<MaxLaunchPanel project={project} standalone />}
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
          <Link className="max-editor-nav-link" href={`/max/${project.id}?panel=services`}>Интеграции</Link>
        </>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
      {adaptation.attachment && <div className="mx-4 my-2 rounded-lg border p-3 text-sm" role="status">
        <p>{adaptationSubmitting ? "Запускаем адаптацию выбранной версии…"
          : "Запрос на адаптацию сохранён. Если запуск не подтверждён, повторите попытку — второй запрос не будет создан."}</p>
        <div className="mt-2 flex flex-wrap gap-3">
          <Button type="button" variant="outline" className="min-h-11 max-w-full whitespace-normal text-left" disabled={adaptationSubmitting || restoration.busy} onClick={() => void submitAdaptation(adaptation.attachment!)}>Повторить запуск адаптации</Button>
          <Button type="button" variant="ghost" className="min-h-11" disabled={adaptationSubmitting || restoration.busy} onClick={() => adaptation.clear()}>Убрать запрос</Button>
        </div>
      </div>}
      <div className="min-h-0 flex-1">
      <ChatPanel
        key={project.id}
        adaptationRef={adaptationRef}
        projectId={project.id}
        projectSlug={project.slug}
        currentSnapshotId={currentSnapshotId}
        mode="max"
        basePath={`/max/${project.id}`}
        embedded
      />
      </div>
      <div className="max-h-[45%] shrink-0 overflow-y-auto">
        <MaxRestorationPanel restoration={restoration}
          onPrepareAdapt={(prompt, reference) => {
            const saved = adaptation.attach(prompt, reference, "cancelling");
            if (!saved) toast.error("Не удалось сохранить запрос. Подготовка не отменена; повторите попытку.");
            return saved;
          }}
          onAdapt={async (prompt, reference) => {
            await submitAdaptation({ projectId: project.id, prompt, reference, phase: "ready" });
          }} />
      </div>
      </div>
    </MaxEditorLayout>
  );
}
