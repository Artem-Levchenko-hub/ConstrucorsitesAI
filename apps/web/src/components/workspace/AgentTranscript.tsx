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
} from "lucide-react";
import type { AgentStep } from "@/lib/api/types";
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
  if (s.kind !== "step") return s.action;
  return ACTION_LABEL[s.action] ?? s.action;
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
}: {
  projectId?: string;
  messageId: string;
  streaming?: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(true);
  // Which step rows are drilled-open (by index) — click a step to see inside it.
  const [openSteps, setOpenSteps] = useState<Record<number, boolean>>({});
  // Live elapsed timer: a real "работает Nс" counter beats a fake ETA (ETA raises
  // frustration when it slips — CHI-2026). Starts on the first streaming frame,
  // ticks each second, and freezes on its last value once the build finishes.
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number | null>(null);
  useEffect(() => {
    if (!streaming) {
      startRef.current = null;
      return;
    }
    if (startRef.current === null) startRef.current = Date.now();
    const tick = () =>
      setElapsed(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [streaming]);
  const { data: steps } = useQuery<AgentStep[]>({
    queryKey: ["agent-steps", projectId, messageId],
    // Data is pushed via setQueryData from usePromptStream's `agent.step`
    // handler; this observer just re-renders on each push. Mirrors the
    // discovery-chips / passes cache pattern (client-only, staleTime Infinity).
    queryFn: () =>
      qc.getQueryData<AgentStep[]>(["agent-steps", projectId, messageId]) ?? [],
    enabled: !!projectId,
    staleTime: Infinity,
  });

  if (!projectId || !steps || steps.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-xl border border-border-subtle bg-surface-raised/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
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
        ) : (
          <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent" />
        )}
        <span className="text-xs font-medium text-fg-primary">
          {streaming ? "Агент работает" : "Агент построил"}
        </span>
        <span className="ml-auto flex items-center gap-1.5 font-mono text-[11px] tabular-nums text-fg-tertiary">
          {(streaming || elapsed > 0) && (
            <span className={cn(streaming && "text-accent")}>
              {streaming ? "" : "за "}
              {formatElapsed(elapsed)} ·
            </span>
          )}
          <span>{steps.length} шаг.</span>
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
            <ol className="scrollbar-elegant max-h-72 space-y-0.5 overflow-y-auto p-1.5">
              {steps.map((s, i) => {
                const Icon = stepIcon(s);
                const last = i === steps.length - 1;
                const live =
                  streaming && last && s.kind === "step" && s.action !== "done";
                const failed = s.ok === false;
                const detail = (s.detail ?? "").trim();
                const canDrill = detail.length > 0;
                const isOpen = !!openSteps[i];
                return (
                  <motion.li
                    key={i}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.18, ease: EASE_OUT }}
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
