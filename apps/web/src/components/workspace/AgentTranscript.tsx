"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
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
  CircleAlert,
  Compass,
  Eye,
  ListChecks,
  Plug,
  ShieldCheck,
} from "lucide-react";
import type { AgentStep, GenerationRunStatus } from "@/lib/api/types";
import { agentElapsedSeconds } from "@/lib/agent-elapsed";
import {
  creativeNarration,
  creativePhaseStates,
} from "@/lib/agent-experience";
import { agentTranscriptTitle } from "@/lib/agent-transcript";
import { hidePrivateModelNames } from "@/lib/model-privacy";
import { cn } from "@/lib/utils";
import { EASE_OUT } from "@/lib/motion";

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
  probe: Globe,
  verify_isolation: ShieldCheck,
  generate_media: Film,
  plan_task: Compass,
  update_plan: ListChecks,
  discover_capabilities: Plug,
  call_capability: Zap,
  read_skill: ScrollText,
  see: Eye,
  done: CheckCircle2,
};

// Tool → short Russian verb, so the row reads like a developer narrating.
const ACTION_LABEL: Record<string, string> = {
  list_dir: "Смотрю папку",
  read_file: "Читаю",
  grep: "Ищу в коде",
  write_file: "Пишу",
  edit_file: "Правлю",
  build: "Проверяю сборку",
  bash: "Команда",
  read_logs: "Читаю логи",
  runtime_check: "Проверяю запуск",
  probe: "Проверяю сценарий",
  verify_isolation: "Проверяю защиту",
  generate_media: "Генерирую медиа",
  plan_task: "Собираю замысел",
  update_plan: "Фиксирую прогресс",
  discover_capabilities: "Ищу возможности",
  call_capability: "Изучаю актуальные данные",
  read_skill: "Подключаю экспертизу",
  see: "Полирую визуал",
  done: "Готово",
};

function stepIcon(s: AgentStep): typeof FileCode2 {
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

function stepLabel(s: AgentStep): string {
  // Backend now sends a ready human phrase in `action` («Пишу главную страницу»).
  // ACTION_LABEL still resolves an older raw tool name; otherwise show as-is.
  if (s.kind !== "step") return hidePrivateModelNames(s.action);
  return hidePrivateModelNames(ACTION_LABEL[s.action] ?? s.action);
}

/**
 * Live "what the agent is doing" transcript — the Claude-Code feel. Reads the
 * per-message ["agent-steps", projectId, messageId] cache that usePromptStream
 * fills from `agent.step` WS events and renders each tool step (icon + verb +
 * path) as it happens. Self-hides when the message has no agent steps (a plain
 * LLM/multipass turn), so it's safe to mount on every assistant message.
 */
export function AgentTranscript({
  projectId,
  messageId,
  streaming,
  initialSteps,
  startedAt,
  finishedAt,
  generationStatus,
  studio = false,
}: {
  projectId?: string;
  messageId: string;
  streaming?: boolean;
  initialSteps?: AgentStep[] | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  generationStatus?: GenerationRunStatus | null;
  studio?: boolean;
}) {
  const qc = useQueryClient();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const open = Boolean(streaming) || detailsOpen;
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
  useEffect(() => {
    if (!streaming) {
      transientStartRef.current = null;
      return;
    }
    const persistedStart = startedAt ? Date.parse(startedAt) : Number.NaN;
    if (Number.isFinite(persistedStart)) {
      transientStartRef.current = persistedStart;
    } else if (transientStartRef.current === null) {
      transientStartRef.current = Date.now();
    }
    const tick = () => {
      const startMs = transientStartRef.current ?? Date.now();
      setLiveElapsed(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [finishedAt, startedAt, streaming]);
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

  if (!projectId || !steps || steps.length === 0) return null;
  const incomplete =
    !streaming &&
    (generationStatus === "failed" || generationStatus === "cancelled" || steps.at(-1)?.ok === false);
  const phases = creativePhaseStates(steps, Boolean(streaming));
  const narration = creativeNarration(steps, Boolean(streaming));

  return (
    <div
      data-testid="agent-creative-session"
      className={cn(
        "overflow-hidden rounded-xl border border-border-subtle bg-surface-raised/60",
        studio &&
          "rounded-[14px] border-[#d8d4cb] bg-[linear-gradient(145deg,#fffefa_0%,#f8f4ec_100%)] shadow-[0_12px_32px_-26px_rgba(23,23,22,.45)]",
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          if (!streaming) setDetailsOpen((value) => !value);
        }}
        className={cn(
          "flex min-h-12 w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-overlay/60",
          studio && "hover:bg-[#f5efe5]/75",
        )}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-fg-tertiary transition-transform",
            open && "rotate-90",
          )}
        />
        <span
          className={cn(
            "grid size-7 shrink-0 place-items-center rounded-full border border-border-subtle bg-surface-base text-accent",
            studio && "border-accent/18 bg-accent/8 text-accent",
            incomplete && "border-red-500/20 bg-red-500/8 text-red-500",
          )}
        >
          {streaming ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : incomplete ? (
            <CircleAlert className="h-3.5 w-3.5" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block text-[9px] font-semibold uppercase tracking-[0.13em] text-fg-tertiary",
              incomplete && "text-red-500",
            )}
          >
            {agentTranscriptTitle(Boolean(streaming), generationStatus, steps.at(-1)?.ok === false)}
          </span>
          <span
            className={cn(
              "mt-0.5 block truncate text-xs font-medium text-fg-primary",
              incomplete && "text-red-600",
              studio && "text-[#24221f]",
            )}
          >
            {narration}
          </span>
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 font-mono text-[10px] tabular-nums text-fg-tertiary">
          {(streaming || elapsed > 0) && (
            <span className={cn(streaming && "text-accent")}>
              {streaming ? "" : "за "}
              {formatElapsed(elapsed)}
            </span>
          )}
          <span className="hidden sm:inline">· {steps.length} шаг.</span>
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: EASE_OUT }}
            className="overflow-hidden border-t border-border-subtle"
          >
            <div
              className={cn(
                "grid grid-cols-4 gap-1.5 border-b border-border-subtle px-3 py-2.5",
                studio && "border-[#ded8ce] bg-white/35",
              )}
              aria-label="Фазы творческой сборки"
            >
              {phases.map((phase) => (
                <div key={phase.id} className="min-w-0" title={phase.description}>
                  <span
                    className={cn(
                      "block h-1 rounded-full bg-border-subtle transition-[background-color,box-shadow] duration-300",
                      phase.status === "complete" && "bg-emerald-500/55",
                      phase.status === "active" &&
                        "bg-accent shadow-[0_0_12px_rgb(71_26_255_/_0.32)]",
                      phase.status === "issue" && "bg-red-500",
                    )}
                  />
                  <span
                    className={cn(
                      "mt-1.5 block truncate text-[9px] font-medium text-fg-tertiary",
                      phase.status === "active" && "text-fg-primary",
                      phase.status === "complete" && "text-emerald-700",
                      phase.status === "issue" && "text-red-600",
                      studio && phase.status === "active" && "text-[#24221f]",
                    )}
                  >
                    {phase.label}
                  </span>
                </div>
              ))}
            </div>
            <ol className="space-y-0.5 p-1.5">
              {steps.map((s, i) => {
                const Icon = stepIcon(s);
                const last = i === steps.length - 1;
                const live =
                  streaming && last && s.kind === "step" && s.action !== "done";
                const failed = s.ok === false;
                const detail = hidePrivateModelNames(s.detail ?? "").trim();
                const canDrill = detail.length > 0;
                const isOpen = !!openSteps[i];
                return (
                  <motion.li
                    key={i}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.18, ease: EASE_OUT }}
                    // Full action + path on hover — the path is truncated in the
                    // row, so the native tooltip surfaces the whole thing (works
                    // for every step, incl. non-drillable ones with a disabled btn).
                    title={stepLabel(s) + (s.path ? " " + s.path : "")}
                  >
                    <button
                      type="button"
                      disabled={!canDrill}
                      onClick={() =>
                        setOpenSteps((m) => ({ ...m, [i]: !m[i] }))
                      }
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-1 text-left transition-colors",
                        canDrill && "cursor-pointer hover:bg-surface-overlay/60",
                      )}
                    >
                      {canDrill ? (
                        <ChevronRight
                          className={cn(
                            "h-3 w-3 shrink-0 text-fg-tertiary transition-transform",
                            isOpen && "rotate-90",
                          )}
                        />
                      ) : (
                        <span className="w-3 shrink-0" />
                      )}
                      <Icon
                        className={cn(
                          "h-3.5 w-3.5 shrink-0",
                          failed
                            ? "text-red-400"
                            : s.kind !== "step"
                              ? "text-amber-400"
                              : s.action === "done"
                                ? "text-accent"
                                : "text-fg-secondary",
                        )}
                      />
                      <span
                        className={cn(
                          "shrink-0 text-[12px]",
                          failed ? "text-red-400" : "text-fg-secondary",
                        )}
                      >
                        {stepLabel(s)}
                      </span>
                      {s.path && (
                        <span className="truncate font-mono text-[11px] text-fg-tertiary">
                          {s.path}
                        </span>
                      )}
                      {live && (
                        <Loader2 className="ml-auto h-3 w-3 shrink-0 animate-spin text-accent" />
                      )}
                    </button>
                    <AnimatePresence initial={false}>
                      {isOpen && canDrill && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.18, ease: EASE_OUT }}
                          className="overflow-hidden"
                        >
                          <pre
                            className={cn(
                              "scrollbar-elegant mx-2 my-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border-subtle bg-surface-base/70 p-2 font-mono text-[11px] leading-relaxed",
                              failed ? "text-red-300" : "text-fg-tertiary",
                            )}
                          >
                            {detail}
                          </pre>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </motion.li>
                );
              })}
            </ol>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
