"use client";

import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Check, Loader2 } from "lucide-react";
import type { MultipassStage, PassProgress } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * Phase B.3 — visualises multipass progress for one streaming assistant
 * message. Reads `["passes", projectId, messageId]` from React Query cache
 * (populated by `usePromptStream` on `llm.pass` events). Renders nothing
 * until the first event arrives — so single-shot generations don't show
 * a useless 0/4 placeholder.
 *
 * Each of the 4 stages (skeleton → content → visual → assembly) is one
 * cell. Cell state:
 *   pending  — muted dot
 *   active   — spinner + accent text
 *   done     — checkmark
 *
 * The bar lives inside `ChatMessage` body and is auto-removed on
 * `llm.done` / `llm.error` / cancel (the hook calls `removeQueries`).
 */
const STAGES: { id: MultipassStage; label: string }[] = [
  { id: "skeleton", label: "Структура" },
  { id: "content", label: "Контент" },
  { id: "visual", label: "Визуал" },
  { id: "assembly", label: "Сборка" },
];

export function PassProgressBar({
  projectId,
  messageId,
}: {
  projectId: string;
  messageId: string;
}) {
  // `useQuery` with `enabled: false` + no real `queryFn` just subscribes
  // to whatever the streaming hook writes via `setQueryData`. Re-renders
  // fire automatically when the cached entry changes; component returns
  // null until the first event arrives, so single-shot messages stay
  // clean.
  const { data: progress } = useQuery<PassProgress | undefined>({
    queryKey: ["passes", projectId, messageId],
    queryFn: () => undefined,
    enabled: false,
    staleTime: Infinity,
  });

  if (!progress) return null;

  const totalDone = progress.completed.length;
  const activeIdx = progress.current
    ? STAGES.findIndex((s) => s.id === progress.current)
    : -1;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -2 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -2 }}
        transition={{ duration: 0.18 }}
        className="rounded-md border border-border-subtle bg-surface-raised px-2.5 py-2"
      >
        <div className="flex items-center gap-2 text-[11px] font-mono text-fg-tertiary mb-1.5">
          <span>multipass</span>
          <span aria-hidden>·</span>
          <span>
            {totalDone}/{STAGES.length}
            {progress.current ? ` · ${labelFor(progress.current)}` : ""}
          </span>
        </div>
        <ol className="flex items-center gap-1.5">
          {STAGES.map((stage, idx) => {
            const isDone = progress.completed.includes(stage.id);
            const isActive = idx === activeIdx;
            return (
              <li
                key={stage.id}
                className={cn(
                  "flex-1 flex items-center gap-1.5 rounded px-1.5 py-1 text-xs transition-colors",
                  isDone && "text-fg-primary bg-surface-overlay",
                  isActive && "text-accent bg-accent-subtle",
                  !isDone && !isActive && "text-fg-tertiary",
                )}
              >
                <span
                  aria-hidden
                  className="h-3.5 w-3.5 flex items-center justify-center shrink-0"
                >
                  {isDone ? (
                    <Check className="h-3 w-3" />
                  ) : isActive ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <span className="h-1.5 w-1.5 rounded-full bg-current opacity-50" />
                  )}
                </span>
                <span className="truncate">{stage.label}</span>
              </li>
            );
          })}
        </ol>
      </motion.div>
    </AnimatePresence>
  );
}

function labelFor(stage: MultipassStage): string {
  return STAGES.find((s) => s.id === stage)?.label ?? stage;
}
