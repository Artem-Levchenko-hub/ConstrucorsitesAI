"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  LayoutGrid,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightOpen,
  Smartphone,
  X,
} from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { BrandMark } from "@/components/marketing/BrandMark";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { listProjects } from "@/lib/api/projects";
import {
  listSnapshots,
  rollback as rollbackSnapshot,
} from "@/lib/api/snapshots";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project, Snapshot } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import { visibleMaxSnapshots } from "@/lib/max-version-history";
import { upsertSnapshotNewest } from "@/lib/snapshot-history";
import { cn } from "@/lib/utils";
import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";
import { MaxAccountMenu } from "./MaxAccountMenu";
import { MaxLaunchPanel } from "./MaxLaunchPanel";
import { MaxLivePreview } from "./MaxLivePreview";
import { MaxProjectNav } from "./MaxProjectNav";
import { MaxUsageBreakdown } from "./MaxUsageBreakdown";

export function MaxWorkspaceShell({
  project,
  email,
}: {
  project: Project;
  email: string;
}) {
  return (
    <MaxEditorProjectScope key={project.id} projectId={project.id}>
      <MaxWorkspaceContent project={project} email={email} />
    </MaxEditorProjectScope>
  );
}

function MaxEditorProjectScope({
  projectId,
  children,
}: {
  projectId: string;
  children: ReactNode;
}) {
  const inspectorScope = useInspectorStore((state) => state.projectScope);
  const styleScope = useStyleEditStore((state) => state.projectScope);

  useEffect(() => {
    // MAX mounts both a desktop and a drawer preview for one project. This
    // single keyed boundary scopes their shared stores before either editor is
    // rendered, and clears transient data on unmount. Selectors can therefore
    // never cross projects without breaking the two-preview synchronization.
    useInspectorStore.getState().scopeToProject(projectId);
    useStyleEditStore.getState().scopeToProject(projectId);
    return () => {
      useInspectorStore.getState().releaseProjectScope(projectId);
      useStyleEditStore.getState().releaseProjectScope(projectId);
    };
  }, [projectId]);

  if (inspectorScope !== projectId || styleScope !== projectId) {
    return (
      <div
        className="grid h-full min-h-0 place-items-center bg-[#fcfbf7] text-xs text-[#8d887f]"
        role="status"
      >
        Открываем редактор…
      </div>
    );
  }

  return children;
}

function MaxWorkspaceContent({
  project,
  email,
}: {
  project: Project;
  email: string;
}) {
  const [launchOpen, setLaunchOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [navigationVisible, setNavigationVisible] = useState(true);
  const [previewPanelVisible, setPreviewPanelVisible] = useState(true);
  const [versionSelection, setVersionSelection] = useState<{
    snapshotId: string;
    headId: string | null;
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
  const versionSnapshots = useMemo(
    () => visibleMaxSnapshots(snapshots.data ?? []),
    [snapshots.data],
  );
  const currentSnapshotId = snapshots.data?.[0]?.id ?? project.current_snapshot_id;
  // Bind a historical selection to the HEAD it was opened from. When a new
  // generation lands in the shared cache, the new current version immediately
  // wins without a state-setting effect or a flash of stale history.
  const selectedSnapshotId =
    versionSelection?.headId === currentSnapshotId
      ? versionSelection.snapshotId
      : null;

  function selectSnapshot(snapshotId: string | null) {
    setVersionSelection(
      snapshotId ? { snapshotId, headId: currentSnapshotId } : null,
    );
  }

  const rollbackMutation = useMutation({
    mutationFn: (snapshotId: string) =>
      rollbackSnapshot(project.id, snapshotId),
    onSuccess: (snapshot) => {
      queryClient.setQueryData<Snapshot[]>(
        ["snapshots", project.id],
        (previous) => upsertSnapshotNewest(previous, snapshot),
      );
      setVersionSelection(null);
      toast.success("Версия восстановлена", {
        description:
          "Создана новая текущая версия. Предыдущее состояние осталось в истории.",
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
            : "Текущая версия не изменена. Повторите попытку.",
      });
    },
  });

  async function restoreSnapshot(snapshotId: string) {
    await rollbackMutation.mutateAsync(snapshotId);
  }

  return (
    <div
      data-light-shell
      className={cn(
        "relative isolate grid h-full max-h-full min-h-0 grid-cols-1 grid-rows-[minmax(0,1fr)] overflow-hidden bg-[#fcfbf7] text-[#171716] transition-[grid-template-columns] duration-200",
        navigationVisible
          ? "lg:grid-cols-[220px_minmax(0,1fr)]"
          : "lg:grid-cols-[minmax(0,1fr)]",
        navigationVisible && previewPanelVisible
          ? "xl:grid-cols-[220px_minmax(420px,1fr)_380px] 2xl:grid-cols-[220px_minmax(480px,1fr)_420px]"
          : navigationVisible
            ? "xl:grid-cols-[220px_minmax(0,1fr)]"
            : previewPanelVisible
              ? "xl:grid-cols-[minmax(420px,1fr)_380px] 2xl:grid-cols-[minmax(480px,1fr)_420px]"
              : "xl:grid-cols-[minmax(0,1fr)]",
      )}
    >
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex h-dvh max-h-dvh min-h-0 w-[220px] flex-col overflow-hidden border-r border-[#d8d4cb] bg-[#fcfbf7] transition-transform lg:static lg:h-full lg:max-h-full lg:translate-x-0 ${navigationVisible ? "lg:flex" : "lg:hidden"} ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-[#d8d4cb] px-5">
          <BrandMark href="/max" />
          <div className="flex items-center">
            <button
              type="button"
              onClick={() => setNavigationVisible(false)}
              className="hidden size-8 place-items-center rounded-full text-[#8d887f] transition-colors hover:bg-[#ece8df] hover:text-[#171716] lg:grid"
              aria-label="Скрыть навигационную панель"
              title="Скрыть навигацию"
              data-testid="max-navigation-close"
            >
              <PanelLeftClose className="size-3.5" />
            </button>
            <button type="button" onClick={() => setMobileNavOpen(false)} className="grid size-11 place-items-center rounded-[8px] text-[#8d887f] lg:hidden" aria-label="Закрыть меню">
              <X className="size-4" />
            </button>
          </div>
        </div>

        <div
          className="flex min-h-0 flex-1 flex-col p-3"
          data-testid="max-navigation-scroll"
        >
          <Link href="/max" className="flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
            <LayoutGrid className="size-4" /> Все проекты
          </Link>
          <p className="omnia-kicker mt-5 px-3 text-[#aaa59b]">Ваши Mini Apps</p>
          <nav
            className="max-projects-scroll mt-2 min-h-20 flex-1 space-y-1 overflow-y-auto overscroll-contain pr-1"
            aria-label="Ваши Mini Apps"
            data-testid="max-projects-scroll"
          >
            {maxProjects.map((item) => {
              const active = item.id === project.id;
              return (
                <Link key={item.id} href={`/max/${item.id}`} className={`flex h-11 items-center gap-3 rounded-[8px] px-3 text-xs transition ${active ? "bg-[#ece8df] font-medium" : "text-[#6d6962] hover:bg-[#f5f3ee]"}`}>
                  <Smartphone className={`size-4 ${active ? "text-accent" : ""}`} />
                  <span className="min-w-0 flex-1 truncate">{item.name}</span>
                  {active && <span className="size-1.5 rounded-full bg-[#248a4b]" />}
                </Link>
              );
            })}
          </nav>

          <div className="mt-3 shrink-0 border-t border-[#d8d4cb] pt-3">
            <p className="omnia-kicker px-3 text-[#aaa59b]">Проект</p>
          </div>
          <div className="max-projects-scroll mt-2 min-h-0 shrink overflow-y-auto overscroll-contain pr-1">
            <MaxProjectNav projectId={project.id} active="editor" />
          </div>
        </div>

        <div className="shrink-0 border-t border-[#d8d4cb] p-3">
          <MaxAccountMenu
            email={email}
            onNavigate={() => setMobileNavOpen(false)}
          />
        </div>
      </aside>

      <section className="flex h-full max-h-full min-h-0 min-w-0 flex-col overflow-hidden bg-[#fcfbf7]">
        <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-[#d8d4cb] px-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-1 sm:gap-3">
            <button type="button" onClick={() => setMobileNavOpen(true)} className="grid size-11 shrink-0 place-items-center rounded-[8px] text-[#6d6962] lg:hidden" aria-label="Открыть меню"><Menu className="size-4" /></button>
            {!navigationVisible && (
              <button
                type="button"
                onClick={() => setNavigationVisible(true)}
                className="hidden size-8 shrink-0 place-items-center rounded-full text-[#8d887f] transition-colors hover:bg-[#ece8df] hover:text-[#171716] lg:grid"
                aria-label="Показать навигационную панель"
                title="Показать навигацию"
                data-testid="max-navigation-open"
              >
                <PanelLeftOpen className="size-3.5" />
              </button>
            )}
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold">{project.name}</h1>
              <p className="mt-0.5 flex items-center gap-1.5 text-[9px] text-[#8d887f]"><span className="size-1.5 rounded-full bg-[#248a4b]" /> Состояние сохраняется на сервере</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
            <MaxUsageBreakdown projectId={project.id} />
            <Link href={`/max/${project.id}/integrations`} className="hidden h-11 items-center rounded-[8px] border border-[#d8d4cb] px-3 text-xs text-[#6d6962] hover:bg-[#f5f3ee] md:inline-flex">Интеграции</Link>
            <button
              type="button"
              onClick={() => setPreviewOpen(true)}
              className="grid size-11 place-items-center rounded-[8px] border border-[#d8d4cb] text-[#6d6962] hover:bg-[#f5f3ee] xl:hidden"
              aria-label="Открыть живое превью"
              data-testid="max-mobile-preview-open"
            >
              <Smartphone className="size-4" />
            </button>
            {!previewPanelVisible && (
              <button
                type="button"
                onClick={() => setPreviewPanelVisible(true)}
                className="hidden size-8 place-items-center rounded-full text-[#8d887f] transition-colors hover:bg-[#ece8df] hover:text-[#171716] xl:grid"
                aria-label="Показать панель превью"
                title="Показать превью"
                data-testid="max-desktop-preview-open"
              >
                <PanelRightOpen className="size-3.5" />
              </button>
            )}
            <button type="button" onClick={() => setLaunchOpen(true)} className="inline-flex h-11 items-center gap-1.5 rounded-[8px] bg-accent px-3 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-hover sm:gap-2 sm:px-4">
              <span className="sm:hidden">Дальше</span>
              <span className="hidden sm:inline">{launchLabel}</span>
              <ChevronDown className="size-3.5" />
            </button>
          </div>
        </header>

        <button
          type="button"
          onClick={() => setLaunchOpen(true)}
          className="flex min-h-14 shrink-0 items-center gap-3 border-b border-[#d8d4cb] bg-[#f5f3ee] px-4 text-left transition-colors hover:bg-[#ece8df] sm:px-5"
          data-testid="max-next-action-bar"
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent text-[10px] font-semibold text-accent-fg">
            {readiness.isSuccess
              ? nextStage?.position ?? journey.total
              : "…"}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[9px] font-medium uppercase tracking-[0.12em] text-[#8d887f]">
              {nextStage ? "Следующий шаг" : "Путь до запуска"}
            </span>
            <span className="mt-0.5 block truncate text-xs font-semibold text-[#171716]">
              {readiness.isError
                ? "Не удалось проверить готовность — откройте панель для повтора"
                : readiness.isLoading
                  ? "Проверяем состояние проекта…"
                  : nextStage?.label ?? "Все обязательные этапы пройдены"}
            </span>
          </span>
          <span className="hidden shrink-0 text-[10px] text-[#8d887f] sm:block">
            {readiness.isSuccess
              ? `${journey.completedCount} из ${journey.total}`
              : "Статус обновляется"}
          </span>
          <ChevronDown className="size-3.5 shrink-0 -rotate-90 text-[#8d887f]" />
        </button>

        <div className="max-studio-chat min-h-0 flex-1 overflow-hidden">
          <ChatPanel
            projectId={project.id}
            projectSlug={project.slug}
            mode="max"
            basePath={`/max/${project.id}`}
            embedded
          />
        </div>
      </section>

      {previewPanelVisible && (
        <div
          className="hidden h-full max-h-full min-h-0 overflow-hidden border-l border-[#d8d4cb] bg-transparent xl:block"
          data-testid="max-desktop-preview-column"
        >
          <MaxLivePreview
            project={project}
            snapshots={versionSnapshots}
            snapshotsLoading={snapshots.isPending}
            currentSnapshotId={currentSnapshotId}
            selectedSnapshotId={selectedSnapshotId}
            onSelectSnapshot={selectSnapshot}
            onRestoreSnapshot={restoreSnapshot}
            restoringSnapshot={rollbackMutation.isPending}
            onClose={() => setPreviewPanelVisible(false)}
          />
        </div>
      )}

      {mobileNavOpen && <button type="button" className="fixed inset-0 z-40 bg-[#171716]/55 lg:hidden" onClick={() => setMobileNavOpen(false)} aria-label="Закрыть меню" />}

      {previewOpen && (
        <div className="fixed inset-0 z-[60] flex justify-end bg-[#171716]/55 backdrop-blur-[2px] xl:hidden">
          <button
            type="button"
            className="absolute inset-0 cursor-default"
            onClick={() => setPreviewOpen(false)}
            aria-label="Закрыть живое превью"
          />
          <section
            className="relative flex h-full w-full max-w-[460px] flex-col bg-[#fcfbf7] shadow-[-30px_0_80px_rgba(0,0,0,.16)]"
            aria-label="Живое превью приложения"
            data-testid="max-mobile-preview"
          >
            <div className="min-h-0 flex-1">
              <MaxLivePreview
                project={project}
                snapshots={versionSnapshots}
                snapshotsLoading={snapshots.isPending}
                currentSnapshotId={currentSnapshotId}
                selectedSnapshotId={selectedSnapshotId}
                onSelectSnapshot={selectSnapshot}
                onRestoreSnapshot={restoreSnapshot}
                restoringSnapshot={rollbackMutation.isPending}
                onClose={() => setPreviewOpen(false)}
              />
            </div>
          </section>
        </div>
      )}

      {launchOpen && (
        <div className="fixed inset-0 z-[70] flex justify-end bg-[#171716]/45 backdrop-blur-[2px]">
          <button type="button" className="absolute inset-0 cursor-default" onClick={() => setLaunchOpen(false)} aria-label="Закрыть публикацию" />
          <div className="relative h-full w-full max-w-[420px] shadow-[-30px_0_80px_rgba(0,0,0,.16)]">
            <MaxLaunchPanel project={project} onClose={() => setLaunchOpen(false)} />
          </div>
        </div>
      )}
    </div>
  );
}
