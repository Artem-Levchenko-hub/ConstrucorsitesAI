"use client";

import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
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
import { DownloadButton } from "@/components/workspace/DownloadButton";
import { getLatestGeneration } from "@/lib/api/messages";
import { listProjects } from "@/lib/api/projects";
import {
  listSnapshots,
  prepareSnapshotPreview,
  rollback as rollbackSnapshot,
  startSnapshotSession,
  stopSnapshotSession,
  type SnapshotSession,
} from "@/lib/api/snapshots";
import { getMaxReadiness } from "@/lib/api/max-studio";
import type { Project, Snapshot } from "@/lib/api/types";
import { getMaxJourney } from "@/lib/max-journey";
import {
  isGenerationActive,
  shouldDeferMaxRuntimeStart,
} from "@/lib/max-runtime-start";
import {
  MAX_VERSION_HISTORY_LIMIT,
  visibleMaxSnapshots,
} from "@/lib/max-version-history";
import { upsertSnapshotNewest } from "@/lib/snapshot-history";
import { cn } from "@/lib/utils";
import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";
import { MaxAccountMenu } from "./MaxAccountMenu";
import { MaxLaunchPanel } from "./MaxLaunchPanel";
import { MaxLivePreview } from "./MaxLivePreview";
import { MaxProjectNav } from "./MaxProjectNav";
import { MaxUsageBreakdown } from "./MaxUsageBreakdown";
import { MaxTrialBadge } from "./MaxTrialBadge";

type HistorySessionRequest = { snapshotId: string; requestId: number };

class StaleHistorySessionRequest extends Error {}

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
  const [desktopNavigationLayout, setDesktopNavigationLayout] = useState(false);
  const [versionSelection, setVersionSelection] = useState<{
    snapshotId: string;
    headId: string | null;
  } | null>(null);
  const [historySession, setHistorySession] = useState<{
    snapshotId: string;
    sessionId: string;
    url: string;
  } | null>(null);
  const activeHistorySession = useRef<{
    snapshotId: string;
    sessionId: string;
  } | null>(null);
  const [requestedHistorySnapshotId, setRequestedHistorySnapshotId] = useState<
    string | null
  >(null);
  const requestedHistorySnapshot = useRef<string | null>(null);
  const historySessionRequestId = useRef(0);
  const historySessionTail = useRef<Promise<void>>(Promise.resolve());
  const [hasStarterHandoff] = useState(
    typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("starter") === "1",
  );
  const [starterHandoffExpired, setStarterHandoffExpired] = useState(false);
  const queryClient = useQueryClient();
  const latestGeneration = useQuery({
    queryKey: ["generation", project.id],
    queryFn: () => getLatestGeneration(project.id),
    staleTime: 0,
    refetchOnMount: "always",
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (isGenerationActive(status)) {
        return 1_000;
      }
      return hasStarterHandoff && !starterHandoffExpired && !query.state.data
        ? 500
        : false;
    },
  });
  const deferInitialRuntimeStart = shouldDeferMaxRuntimeStart({
    generationQueryPending: latestGeneration.isPending || latestGeneration.isFetching,
    generationStatus: latestGeneration.data?.status,
    hasGeneration: Boolean(latestGeneration.data),
    hasStarterHandoff,
    starterHandoffExpired,
  });
  const navigationInteractive = desktopNavigationLayout
    ? navigationVisible
    : mobileNavOpen;

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setDesktopNavigationLayout(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!hasStarterHandoff || latestGeneration.data) return;
    const timeout = window.setTimeout(() => setStarterHandoffExpired(true), 35_000);
    return () => window.clearTimeout(timeout);
  }, [hasStarterHandoff, latestGeneration.data]);
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const snapshots = useQuery({
    queryKey: ["snapshots", project.id],
    queryFn: () => listSnapshots(project.id, MAX_VERSION_HISTORY_LIMIT + 1),
    refetchInterval: (query) => {
      const selectedId = versionSelection?.snapshotId;
      if (!selectedId) return false;
      const selected = query.state.data?.find((item) => item.id === selectedId);
      return selected && !selected.preview_url ? 2_000 : false;
    },
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

  const preparePreviewMutation = useMutation({
    mutationFn: (snapshotId: string) =>
      prepareSnapshotPreview(project.id, snapshotId),
    onSuccess: (snapshot) => {
      queryClient.setQueryData<Snapshot[]>(
        ["snapshots", project.id],
        (previous) =>
          previous?.map((item) =>
            item.id === snapshot.id
              ? {
                  ...item,
                  ...snapshot,
                  preview_url: snapshot.preview_url ?? item.preview_url,
                  version_number:
                    snapshot.version_number ?? item.version_number,
                }
              : item,
          ),
      );
    },
    onError: () => {
      toast.error("Не удалось подготовить снимок", {
        description: "Версия сохранена и доступна для восстановления. Повторите просмотр.",
      });
    },
  });

  const historySessionMutation = useMutation({
    mutationFn: ({
      snapshotId,
      requestId,
    }: HistorySessionRequest): Promise<SnapshotSession> => {
      const request = historySessionTail.current
        .catch(() => undefined)
        .then(async () => {
          if (historySessionRequestId.current !== requestId) {
            throw new StaleHistorySessionRequest();
          }
          return startSnapshotSession(project.id, snapshotId);
        });
      historySessionTail.current = request.then(
        () => undefined,
        () => undefined,
      );
      return request;
    },
    onSuccess: (session, { snapshotId, requestId }) => {
      if (
        historySessionRequestId.current === requestId &&
        requestedHistorySnapshot.current === snapshotId
      ) {
        setHistorySession({
          snapshotId,
          sessionId: session.session_id,
          url: session.bootstrap_url,
        });
        activeHistorySession.current = {
          snapshotId,
          sessionId: session.session_id,
        };
      } else {
        void stopSnapshotSession(project.id, snapshotId, session.session_id);
      }
    },
    onError: (error, { snapshotId, requestId }) => {
      if (
        !(error instanceof StaleHistorySessionRequest) &&
        historySessionRequestId.current === requestId &&
        requestedHistorySnapshot.current === snapshotId
      ) {
        toast.error("Интерактивная версия не открылась", {
          description:
            "Снимок остаётся доступен, а восстановление версии работает как обычно.",
        });
      }
    },
  });

  const cancelHistoryRuntime = useCallback(() => {
    historySessionRequestId.current += 1;
    const active = activeHistorySession.current;
    activeHistorySession.current = null;
    requestedHistorySnapshot.current = null;
    if (active) {
      void stopSnapshotSession(project.id, active.snapshotId, active.sessionId);
    }
  }, [project.id]);

  const closeHistorySession = useCallback(() => {
    cancelHistoryRuntime();
    setRequestedHistorySnapshotId(null);
    setHistorySession(null);
  }, [cancelHistoryRuntime]);

  useEffect(() => {
    if (!versionSelection || versionSelection.headId === currentSnapshotId) {
      return;
    }
    cancelHistoryRuntime();
  }, [cancelHistoryRuntime, currentSnapshotId, versionSelection]);

  useEffect(() => {
    const urls = versionSnapshots
      .map((snapshot) => snapshot.preview_url)
      .filter((url): url is string => Boolean(url));
    if (urls.length === 0) return;

    let cancelled = false;
    let next = 0;
    let active = 0;
    const images = new Set<HTMLImageElement>();
    const pump = () => {
      if (cancelled) return;
      while (active < 2 && next < urls.length) {
        const image = new Image();
        images.add(image);
        active += 1;
        image.onload = image.onerror = () => {
          images.delete(image);
          active -= 1;
          pump();
        };
        image.src = urls[next++];
      }
    };
    const idleId = window.requestIdleCallback
      ? window.requestIdleCallback(pump, { timeout: 1_000 })
      : window.setTimeout(pump, 0);
    return () => {
      cancelled = true;
      images.forEach((image) => {
        image.onload = null;
        image.onerror = null;
      });
      if (window.cancelIdleCallback) window.cancelIdleCallback(idleId);
      else window.clearTimeout(idleId);
    };
  }, [versionSnapshots]);

  useEffect(
    () => () => {
      const active = activeHistorySession.current;
      if (active) {
        void stopSnapshotSession(project.id, active.snapshotId, active.sessionId);
      }
    },
    [project.id],
  );

  function selectSnapshot(snapshotId: string | null) {
    const active = activeHistorySession.current;
    if (active) {
      activeHistorySession.current = null;
      void stopSnapshotSession(project.id, active.snapshotId, active.sessionId);
    }
    requestedHistorySnapshot.current = snapshotId;
    setRequestedHistorySnapshotId(snapshotId);
    setHistorySession(null);
    setVersionSelection(
      snapshotId ? { snapshotId, headId: currentSnapshotId } : null,
    );
    if (snapshotId) {
      const snapshot = versionSnapshots.find((item) => item.id === snapshotId);
      if (snapshot && !snapshot.preview_url) {
        preparePreviewMutation.mutate(snapshotId);
      }
      const requestId = historySessionRequestId.current + 1;
      historySessionRequestId.current = requestId;
      historySessionMutation.mutate({ snapshotId, requestId });
    } else {
      historySessionRequestId.current += 1;
    }
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
      closeHistorySession();
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
        "relative isolate grid h-full max-h-full min-h-0 grid-cols-1 grid-rows-[minmax(0,1fr)] overflow-hidden bg-[#fcfbf7] text-[#171716] transition-[grid-template-columns] duration-300 ease-[cubic-bezier(.22,1,.36,1)] motion-reduce:duration-0 lg:grid-cols-[var(--max-nav-column)_minmax(0,1fr)] xl:grid-cols-[var(--max-nav-column)_minmax(420px,1fr)_var(--max-preview-column)] 2xl:grid-cols-[var(--max-nav-column)_minmax(480px,1fr)_var(--max-preview-column)]",
      )}
      style={
        {
          "--max-nav-column": navigationVisible ? "220px" : "0px",
          "--max-preview-column": previewPanelVisible
            ? "clamp(380px,20.5vw,420px)"
            : "0px",
        } as CSSProperties
      }
    >
      <aside
        aria-hidden={!navigationInteractive}
        inert={!navigationInteractive}
        className={`fixed inset-y-0 left-0 z-50 flex h-dvh max-h-dvh min-h-0 w-[220px] flex-col overflow-hidden border-r bg-[#fcfbf7] transition-[transform,opacity,border-color] duration-300 ease-[cubic-bezier(.22,1,.36,1)] motion-reduce:duration-0 lg:static lg:h-full lg:max-h-full lg:w-full ${navigationVisible ? "lg:translate-x-0 lg:border-[#d8d4cb] lg:opacity-100" : "lg:pointer-events-none lg:-translate-x-2 lg:border-transparent lg:opacity-0"} ${
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
            <MaxTrialBadge />
            <MaxUsageBreakdown projectId={project.id} />
            {versionSnapshots.length > 0 && (
              <div className="hidden md:block">
                <DownloadButton projectId={project.id} projectSlug={project.slug} />
              </div>
            )}
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

      <div
        aria-hidden={!previewPanelVisible}
        inert={!previewPanelVisible}
        className={cn(
          "hidden h-full max-h-full min-h-0 overflow-hidden border-l bg-transparent transition-[transform,opacity,border-color] duration-300 ease-[cubic-bezier(.22,1,.36,1)] motion-reduce:duration-0 xl:block",
          previewPanelVisible
            ? "translate-x-0 border-[#d8d4cb] opacity-100"
            : "pointer-events-none translate-x-2 border-transparent opacity-0",
        )}
        data-testid="max-desktop-preview-column"
      >
          <MaxLivePreview
            project={project}
            deferInitialRuntimeStart={deferInitialRuntimeStart}
            snapshots={versionSnapshots}
            snapshotsLoading={snapshots.isPending}
            currentSnapshotId={currentSnapshotId}
            selectedSnapshotId={selectedSnapshotId}
            historicalSessionUrl={
              historySession?.snapshotId === selectedSnapshotId
                ? historySession.url
                : null
            }
            historicalSessionLoading={
              Boolean(selectedSnapshotId) &&
              requestedHistorySnapshotId === selectedSnapshotId &&
              historySessionMutation.isPending
            }
            onSelectSnapshot={selectSnapshot}
            onRestoreSnapshot={restoreSnapshot}
            restoringSnapshot={rollbackMutation.isPending}
            onClose={() => setPreviewPanelVisible(false)}
          />
      </div>

      <AnimatePresence initial={false}>
        {mobileNavOpen && (
          <motion.button
            type="button"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="fixed inset-0 z-40 bg-[#171716]/55 lg:hidden"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Закрыть меню"
          />
        )}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {previewOpen && (
          <motion.div
            key="max-mobile-preview-layer"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="fixed inset-0 z-[60] flex justify-end bg-[#171716]/55 backdrop-blur-[2px] xl:hidden"
          >
            <button
              type="button"
              className="absolute inset-0 cursor-default"
              onClick={() => setPreviewOpen(false)}
              aria-label="Закрыть живое превью"
            />
            <motion.section
              initial={{ x: 22 }}
              animate={{ x: 0 }}
              exit={{ x: 18 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className="relative flex h-full w-full max-w-[460px] flex-col bg-[#fcfbf7] shadow-[-30px_0_80px_rgba(0,0,0,.16)]"
              aria-label="Живое превью приложения"
              data-testid="max-mobile-preview"
            >
              <div className="min-h-0 flex-1">
                <MaxLivePreview
                  project={project}
                  deferInitialRuntimeStart={deferInitialRuntimeStart}
                  snapshots={versionSnapshots}
                  snapshotsLoading={snapshots.isPending}
                  currentSnapshotId={currentSnapshotId}
                  selectedSnapshotId={selectedSnapshotId}
                  historicalSessionUrl={
                    historySession?.snapshotId === selectedSnapshotId
                      ? historySession.url
                      : null
                  }
                  historicalSessionLoading={
                    Boolean(selectedSnapshotId) &&
                    requestedHistorySnapshotId === selectedSnapshotId &&
                    historySessionMutation.isPending
                  }
                  onSelectSnapshot={selectSnapshot}
                  onRestoreSnapshot={restoreSnapshot}
                  restoringSnapshot={rollbackMutation.isPending}
                  onClose={() => setPreviewOpen(false)}
                />
              </div>
            </motion.section>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {launchOpen && (
          <motion.div
            key="max-launch-layer"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="fixed inset-0 z-[70] flex justify-end bg-[#171716]/45 backdrop-blur-[2px]"
          >
            <button type="button" className="absolute inset-0 cursor-default" onClick={() => setLaunchOpen(false)} aria-label="Закрыть публикацию" />
            <motion.div
              initial={{ x: 22 }}
              animate={{ x: 0 }}
              exit={{ x: 18 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className="relative h-full w-full max-w-[420px] shadow-[-30px_0_80px_rgba(0,0,0,.16)]"
            >
              <MaxLaunchPanel project={project} onClose={() => setLaunchOpen(false)} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
