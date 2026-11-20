"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  FolderTree,
  FileCode2,
  Search,
  PencilLine,
  Hammer,
  Terminal,
  ScrollText,
  Globe,
  CheckCircle2,
  Zap,
  RefreshCw,
  Loader2,
  Sparkles,
  Film,
  BookOpen,
  Hourglass,
  Lightbulb,
  ShieldCheck,
} from "lucide-react";
import { getLatestGeneration } from "@/lib/api/messages";
import { isGenerationActive } from "@/lib/generation-lifecycle";
import type { AgentStep, GenerationRunStatus } from "@/lib/api/types";
import { agentElapsedSeconds } from "@/lib/agent-elapsed";
import { collapseAgentSteps, countAgentActions } from "@/lib/agent-steps";
import { humanPath, stepCopy, type StepIconName, type StepTone } from "@/lib/agent-step-copy";
import { CAPACITY_WAITING_COPY } from "@/lib/agent-transcript";
import { projectGenerationPhase } from "@/lib/generation-phase";
import { generationVerifiedActionLabel, isGenerationResultConfirmed, parseGenerationRecovery } from "@/lib/generation-recovery";
import { cn } from "@/lib/utils";
import { GenerationPhaseCard, useGenerationPhaseActivity } from "./GenerationPhaseCard";

// Tool → icon. `typeof FileCode2` matches the codebase's icon-typing style
// (ChatMessage.iconForLabel) so we don't depend on lucide's LucideIcon export.
const ACTION_ICON: Record<string, typeof FileCode2> = {
  list_dir: FolderTree,
  read_file: FileCode2,
  grep: Search,
  write_file: FileCode2,
  edit_file: PencilLine,
  build: Hammer,
  bash: Terminal,
  read_logs: ScrollText,
  runtime_check: Globe,
  generate_media: Film,
  done: CheckCircle2,
};

function stepIcon(s: AgentStep): typeof FileCode2 {
  if (s.kind === "heartbeat") return Loader2;
  if (s.kind === "escalate") return Zap;
  if (s.kind === "retry" || s.kind === "stalled") return RefreshCw;
  // `action` is now a human phrase from the backend, so key the icon off the raw
  // `tool` name; `s.action` covers messages cached before the humanize change.
  return ACTION_ICON[s.tool ?? s.action] ?? Sparkles;
}

/** "5с" under a minute, "1м 05с" above — compact live-timer format. */
function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec}с`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}м ${String(s).padStart(2, "0")}с`;
}

// Разряд шага → цвет строки. Владельцу важно различать «агент думает»,
// «агент пишет», «агент проверяет» и «что-то не вышло» одним взглядом.
// Значок строки подбирается по смыслу действия, а не по имени инструмента:
// владелец узнаёт браузер, щит и песочные часы быстрее, чем слово runtime_check.
const STEP_ICON: Record<StepIconName, typeof FileCode2> = {
  explore: FolderTree,
  read: FileCode2,
  search: Search,
  docs: BookOpen,
  write: FileCode2,
  edit: PencilLine,
  media: Film,
  terminal: Terminal,
  build: Hammer,
  logs: ScrollText,
  browser: Globe,
  shield: ShieldCheck,
  wait: Hourglass,
  retry: RefreshCw,
  rethink: Lightbulb,
  boost: Zap,
  done: CheckCircle2,
};

const TONE_CLASS: Record<StepTone, string> = {
  think: "text-fg-tertiary",
  work: "text-fg-secondary",
  check: "text-blue-400",
  // Янтарный в продукте запрещён (theme-contract): ожидание узнаётся по
  // песочным часам и пульсации, а не по «предупреждающему» цвету.
  wait: "text-fg-tertiary",
  done: "text-accent",
  fail: "text-fg-secondary",
};

/**
 * One semantic phase above the native, opt-in technical journal. Observes the
 * same per-message cache that usePromptStream fills; no records/counters are
 * rewritten or discarded. Plain non-agent turns still self-hide.
 */
export function AgentTranscript({
  projectId,
  messageId,
  streaming: messageStreaming,
  initialSteps,
  startedAt: messageStartedAt,
  finishedAt: messageFinishedAt,
  generationStatus: messageStatus,
}: {
  projectId?: string;
  messageId: string;
  streaming?: boolean;
  initialSteps?: AgentStep[] | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  generationStatus?: GenerationRunStatus | null;
}) {
  const qc = useQueryClient();
  const latest = useQuery({ queryKey: ["generation", projectId],
    queryFn: () => getLatestGeneration(projectId!), enabled: Boolean(projectId), retry: false,
    refetchInterval: query => query.state.data?.project_id === projectId && query.state.data?.assistant_message_id === messageId
      && isGenerationActive(query.state.data) ? 5_000 : false });
  // Latest is project-wide; it may update only its exact assistant message.
  // Older failure cards and tool records retain their own durable outcome.
  const boundRun = latest.data?.project_id === projectId && latest.data?.assistant_message_id === messageId
    ? latest.data : undefined;
  const historicalRequest = latest.data?.project_id === projectId
    && typeof latest.data?.assistant_message_id === "string"
    && latest.data.assistant_message_id !== messageId;
  const generationStatus = boundRun?.status ?? messageStatus;
  const startedAt = boundRun?.started_at ?? messageStartedAt;
  const finishedAt = boundRun?.finished_at ?? messageFinishedAt;
  const streaming = generationStatus ? isGenerationActive({ status: generationStatus }) : Boolean(messageStreaming);
  const recovery = parseGenerationRecovery(boundRun?.recovery);
  const recoveryUnconfirmed = boundRun?.recovery_unavailable === true
    || (boundRun?.recovery != null && recovery === null);
  const currentRequestComplete = isGenerationResultConfirmed(boundRun);
  const hasRecovery = recovery !== null || recoveryUnconfirmed;
  const recoveryIdle = generationStatus !== "cancel_requested" && (
    recoveryUnconfirmed || recovery?.phase === "waiting_dependency"
    || recovery?.phase === "reconciling" || recovery?.phase === "needs_input");
  const verifiedAction = generationVerifiedActionLabel(recovery);
  const showElapsed = !hasRecovery;
  const [detailsOpen, setDetailsOpen] = useState(false);
  const capacityWaiting = generationStatus === "queued_for_capacity";
  // Which step rows are drilled-open (by index) — click a step to see inside it.
  const [openSteps, setOpenSteps] = useState<Record<number, boolean>>({});
  // The durable GenerationRun timestamps survive F5. A newly-submitted optimistic
  // row temporarily falls back to its client creation time, then the persisted
  // started_at takes over when history is refreshed.
  const [liveElapsed, setLiveElapsed] = useState(() =>
    agentElapsedSeconds(startedAt, finishedAt),
  );
  const elapsed =
    !streaming && startedAt && finishedAt
      ? agentElapsedSeconds(startedAt, finishedAt)
      : liveElapsed;
  const transientStartRef = useRef<number | null>(null);
  const { data: steps } = useQuery<AgentStep[]>({
    queryKey: ["agent-steps", projectId, messageId],
    // Data is pushed via setQueryData from usePromptStream's `agent.step`
    // handler; this observer just re-renders on each push. Mirrors the
    // discovery-chips / passes cache pattern (client-only, staleTime Infinity).
    queryFn: () =>
      qc.getQueryData<AgentStep[]>(["agent-steps", projectId, messageId]) ??
      initialSteps ??
      [],
    initialData: initialSteps?.length ? initialSteps : undefined,
    enabled: !!projectId,
    staleTime: Infinity,
  });

  const visibleSteps = steps ?? [];
  // Одинаковое действие подряд — одна строка со счётчиком повторов: десять
  // строк «Ожидаю ресурсы сервера» читаются как поломка, а не как ожидание.
  const rows = collapseAgentSteps(visibleSteps);
  const actionCount = countAgentActions(visibleSteps, boundRun?.id);
  const historyTruncated = visibleSteps.some(step => step.historyTruncated === true
    && (!boundRun || !step.runId || step.runId === boundRun.id));
  const phase = projectGenerationPhase({
    steps: visibleSteps, runId: boundRun?.id, status: generationStatus, streaming,
    recovery: boundRun?.recovery, recoveryUnconfirmed, currentRequestComplete,
    historical: historicalRequest,
  });
  const surfaceRef = useRef<HTMLDivElement>(null);
  const surfaceActive = useGenerationPhaseActivity(surfaceRef, phase.active);
  useEffect(() => {
    if (!streaming || !showElapsed) {
      transientStartRef.current = null;
      return;
    }
    const persistedStart = startedAt ? Date.parse(startedAt) : Number.NaN;
    if (Number.isFinite(persistedStart)) {
      transientStartRef.current = persistedStart;
    } else if (transientStartRef.current === null) {
      transientStartRef.current = Date.now();
    }
    if (!detailsOpen || !surfaceActive) return;
    const tick = () => {
      const startMs = transientStartRef.current ?? Date.now();
      setLiveElapsed(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [detailsOpen, finishedAt, showElapsed, startedAt, streaming, surfaceActive]);
  const initialBuild = streaming && !historicalRequest && boundRun?.response_mode === "build";
  if (!projectId || (visibleSteps.length === 0 && !capacityWaiting && !hasRecovery
    && !initialBuild && !currentRequestComplete)) return null;

  return (
    <div ref={surfaceRef} data-agent-transcript={streaming || capacityWaiting ? "working" : "idle"}
      className="overflow-hidden rounded-[14px] border border-border-subtle bg-surface-raised/60">
      <GenerationPhaseCard phase={phase} motionActive={surfaceActive} />
      <details open={detailsOpen} onToggle={event => setDetailsOpen(event.currentTarget.open)}
        className="group/agent-details border-t border-border-subtle">
        <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 px-4 py-2 text-xs text-fg-secondary transition-colors hover:bg-surface-overlay/40 focus-visible:outline-2 focus-visible:outline-offset-[-3px] focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 transition-transform group-open/agent-details:rotate-90 motion-reduce:transition-none" />
          Подробности работы
        </summary>
        <div className="border-t border-border-subtle">
          {phase.milestones.length > 0 && <p data-generation-milestones
            className="px-3 pt-3 text-xs text-fg-secondary">
            {phase.milestones.join(". ")}.
          </p>}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-3 pb-1 text-[11px] tabular-nums text-fg-tertiary">
            {showElapsed && (streaming || elapsed > 0) && <span>{streaming ? "Время работы: " : "за "}{formatElapsed(elapsed)}</span>}
            <span>{actionCount === null ? "Число шагов недоступно"
              : actionCount > 0 ? `${actionCount} шаг.` : rows.length > 0 ? `${rows.length} этап.` : "Пока нет выполненных шагов"}</span>
          </div>
          {capacityWaiting && visibleSteps.length === 0 && <p className="px-3 py-2 text-xs text-fg-secondary">{CAPACITY_WAITING_COPY.detail}</p>}
          {historyTruncated && <p className="px-3 py-2 text-xs text-fg-secondary">
            Сохранённая история сокращена: показаны начало и последнее событие.
            {actionCount === null ? " Общее число шагов недоступно." : " Счётчик учитывает всю работу."}
          </p>}
          {hasRecovery && <div className="space-y-1 px-3 py-2 text-xs text-fg-secondary">
            {verifiedAction && <p>Последнее подтверждённое действие: {verifiedAction.toLowerCase()}.</p>}
            {recoveryIdle && recovery?.next_check_at && <p>Следующая проверка: {new Date(recovery.next_check_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}.</p>}
            {recovery?.phase === "needs_input" && <p>Можно остановить работу и отправить уточнённый запрос.</p>}
            {recoveryUnconfirmed && <p>Уточняю состояние работы. Повторный запрос не запускается.</p>}
          </div>}
            <ol className="space-y-0.5 p-1.5">
              {rows.map((row, i) => {
                const s = row.step;
                const copy = stepCopy(s, currentRequestComplete);
                const Icon = STEP_ICON[copy.icon] ?? stepIcon(s);
                const last = i === rows.length - 1;
                const live =
                  phase.active && !recoveryIdle &&
                  (last || s.toolLifecycle === "started" || s.toolLifecycle === "heartbeat") &&
                  (s.kind === "step" || s.kind === "heartbeat") &&
                  s.toolLifecycle !== "finished" &&
                  s.ok !== false &&
                  s.action !== "done";
                const detail = (s.detail ?? "").trim();
                const canDrill = detail.length > 0;
                const isOpen = !!openSteps[row.index];
                const place = humanPath(s.path);
                return (
                  <li
                    key={row.key}
                    // Полный путь к файлу — в подсказке: в строке он только
                    // мешает, а при разборе проблемы нужен целиком.
                    title={copy.title + (s.path ? " · " + s.path : "")}
                  >
                    <button
                      type="button"
                      disabled={!canDrill}
                      aria-expanded={canDrill ? isOpen : undefined}
                      onClick={() =>
                        setOpenSteps((m) => ({ ...m, [row.index]: !m[row.index] }))
                      }
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2 py-1 text-left transition-colors focus-visible:outline-2 focus-visible:outline-accent",
                        canDrill && "cursor-pointer hover:bg-surface-overlay/60",
                      )}
                      data-live={live ? "true" : undefined}
                      data-repeats={row.repeats > 1 ? row.repeats : undefined}
                    >
                      {canDrill ? (
                        <ChevronRight
                          className={cn(
                            "mt-0.5 h-3 w-3 shrink-0 text-fg-tertiary transition-transform motion-reduce:transition-none",
                            isOpen && "rotate-90",
                          )}
                        />
                      ) : (
                        <span className="w-3 shrink-0" />
                      )}
                      <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", TONE_CLASS[copy.tone])} />
                      <span className="min-w-0 flex-1">
                        <span className="flex min-w-0 items-center gap-1.5">
                          <span
                            className="truncate text-[12px] text-fg-secondary"
                          >
                            {copy.title}
                          </span>
                          {place && (
                            <span className="truncate text-[11px] text-fg-tertiary">
                              · {place}
                            </span>
                          )}
                          {row.repeats > 1 && (
                            <span className="shrink-0 rounded-full bg-surface-overlay px-1.5 text-[10px] font-medium tabular-nums text-fg-tertiary">
                              ×{row.repeats}
                            </span>
                          )}
                        </span>
                        {/* Объяснение показываем у текущего шага: в истории оно
                            превратилось бы в стену текста. */}
                        {live && (
                          <span className="mt-0.5 block text-[11px] leading-4 text-fg-tertiary">
                            {copy.hint}
                          </span>
                        )}
                      </span>
                      {live && (
                        <Loader2 className="mt-0.5 h-3 w-3 shrink-0 text-accent" />
                      )}
                    </button>
                    {isOpen && canDrill && (
                        <div
                          className="overflow-hidden"
                        >
                          {s.path && (
                            <p className="mx-2 mt-1 truncate font-mono text-[10px] text-fg-tertiary">
                              {s.path}
                            </p>
                          )}
                          <pre
                            className={cn(
                              "scrollbar-elegant mx-2 my-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border-subtle bg-surface-base/70 p-2 font-mono text-[11px] leading-relaxed",
                              "text-fg-tertiary",
                            )}
                          >
                            {detail}
                          </pre>
                        </div>
                      )}
                  </li>
                );
              })}
            </ol>
        </div>
      </details>
    </div>
  );
}
