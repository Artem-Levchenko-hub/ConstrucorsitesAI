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
  BookOpen,
  Hourglass,
  Lightbulb,
  ShieldCheck,
} from "lucide-react";
import type { AgentStep, GenerationRunStatus } from "@/lib/api/types";
import { agentElapsedSeconds } from "@/lib/agent-elapsed";
import { collapseAgentSteps } from "@/lib/agent-steps";
import { humanPath, stepCopy, type StepIconName, type StepTone } from "@/lib/agent-step-copy";
import { CAPACITY_WAITING_COPY, agentTranscriptTitle } from "@/lib/agent-transcript";
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
  generate_media: Film,
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
  generate_media: "Генерирую медиа",
  done: "Готово",
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

function stepLabel(s: AgentStep): string {
  // Backend now sends a ready human phrase in `action` («Пишу главную страницу»).
  // ACTION_LABEL still resolves an older raw tool name; otherwise show as-is.
  if (s.kind !== "step") return s.action;
  return ACTION_LABEL[s.action] ?? s.action;
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
  fail: "text-red-400",
};

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
  const [detailsOpen, setDetailsOpen] = useState(false);
  const capacityWaiting = generationStatus === "queued_for_capacity";
  const open = Boolean(streaming) || capacityWaiting || detailsOpen;
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

  const visibleSteps = steps ?? [];
  // Одинаковое действие подряд — одна строка со счётчиком повторов: десять
  // строк «Ожидаю ресурсы сервера» читаются как поломка, а не как ожидание.
  const rows = collapseAgentSteps(visibleSteps);
  if (!projectId || (visibleSteps.length === 0 && !capacityWaiting)) return null;
  const incomplete =
    !streaming &&
    (generationStatus === "failed" ||
      generationStatus === "cancelled" ||
      visibleSteps.at(-1)?.ok === false);

  return (
    <div data-agent-transcript={streaming || capacityWaiting ? "working" : "idle"} className="overflow-hidden rounded-xl border border-border-subtle bg-surface-raised/60">
      <button
        type="button"
        onClick={() => {
          if (!streaming) setDetailsOpen((value) => !value);
        }}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 transition-colors hover:bg-surface-overlay/60"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-fg-tertiary transition-transform",
            open && "rotate-90",
          )}
        />
        {streaming ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
        ) : incomplete ? (
          <CircleAlert className="h-3.5 w-3.5 shrink-0 text-red-500" />
        ) : (
          <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent" />
        )}
        <span className={cn("text-xs font-medium", incomplete ? "text-red-600" : "text-fg-primary")}>
          {agentTranscriptTitle(
            Boolean(streaming),
            generationStatus,
            visibleSteps.at(-1)?.ok === false,
          )}
        </span>
        <span className="ml-auto flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-fg-tertiary">
          {(streaming || elapsed > 0) && (
            <span className={cn(streaming && "text-accent")}>
              {streaming ? "" : "за "}
              {formatElapsed(elapsed)} ·
            </span>
          )}
          <span>
            {rows.length > 0 ? `${rows.length} шаг. · детали` : "детали"}
          </span>
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
            {capacityWaiting && visibleSteps.length === 0 && (
              <p className="px-3 py-2 text-xs text-fg-secondary">
                {CAPACITY_WAITING_COPY.detail}
              </p>
            )}
            <ol className="space-y-0.5 p-1.5">
              {rows.map((row, i) => {
                const s = row.step;
                const copy = stepCopy(s);
                const Icon = STEP_ICON[copy.icon] ?? stepIcon(s);
                const last = i === rows.length - 1;
                const live =
                  streaming &&
                  last &&
                  (s.kind === "step" || s.kind === "heartbeat") &&
                  s.action !== "done";
                const failed = s.ok === false;
                const detail = (s.detail ?? "").trim();
                const canDrill = detail.length > 0;
                const isOpen = !!openSteps[row.index];
                const place = humanPath(s.path);
                return (
                  <motion.li
                    key={row.key}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.18, ease: EASE_OUT }}
                    // Полный путь к файлу — в подсказке: в строке он только
                    // мешает, а при разборе проблемы нужен целиком.
                    title={stepLabel(s) + (s.path ? " · " + s.path : "")}
                  >
                    <button
                      type="button"
                      disabled={!canDrill}
                      onClick={() =>
                        setOpenSteps((m) => ({ ...m, [row.index]: !m[row.index] }))
                      }
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2 py-1 text-left transition-colors",
                        canDrill && "cursor-pointer hover:bg-surface-overlay/60",
                        live && "motion-safe:animate-[agent-step-pulse_1.6s_ease-in-out_infinite]",
                      )}
                      data-live={live ? "true" : undefined}
                      data-repeats={row.repeats > 1 ? row.repeats : undefined}
                    >
                      {canDrill ? (
                        <ChevronRight
                          className={cn(
                            "mt-0.5 h-3 w-3 shrink-0 text-fg-tertiary transition-transform",
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
                            className={cn(
                              "truncate text-[12px]",
                              failed ? "text-red-400" : "text-fg-secondary",
                            )}
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
                        <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-accent" />
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
                          {s.path && (
                            <p className="mx-2 mt-1 truncate font-mono text-[10px] text-fg-tertiary">
                              {s.path}
                            </p>
                          )}
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
