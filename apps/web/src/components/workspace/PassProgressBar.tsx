"use client";

import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2 } from "lucide-react";
import type { MultipassStage, PassProgress } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * Phase B.3 — visualises multipass progress for one streaming assistant
 * message. Reads `["passes", projectId, messageId]` from React Query cache
 * (populated by `usePromptStream` on `llm.pass` events). Renders nothing
 * until the first event arrives — so single-shot generations don't show
 * a useless 0/4 placeholder.
 *
 * Both pipelines have 4 stages — multipass (skeleton → content → visual →
 * assembly) and freeform (Замысел → Вёрстка → Картинки → Проверка); the set
 * is picked per-message from the events seen. They render as a slim 4-segment
 * bar: each segment fills 0% → 50% (active) → 100% (done). The active stage
 * name is shown in the header line, not inside the bar, so the widget stays
 * width-safe and never overflows the chat column.
 *
 * The bar lives inside `ChatMessage` body and is auto-removed on
 * `llm.done` / `llm.error` / cancel (the hook calls `removeQueries`).
 */
const MULTIPASS_STAGES: { id: MultipassStage; label: string }[] = [
  { id: "skeleton", label: "Структура" },
  { id: "content", label: "Контент" },
  { id: "visual", label: "Визуал" },
  { id: "assembly", label: "Сборка" },
];
// The default (freeform) path emits these via the same llm.pass channel.
// Four real stages, in pipeline order: the Art-Director writes the brief, the
// Writer lays out the HTML, the resolver paints in the images, and the
// design-judge reviews the page. Backend emits all four (messages.py).
const FREEFORM_STAGES: { id: MultipassStage; label: string }[] = [
  { id: "art_director", label: "Замысел" },
  { id: "writer", label: "Вёрстка" },
  { id: "images", label: "Картинки" },
  { id: "judge", label: "Проверка" },
];
const FREEFORM_IDS = new Set<MultipassStage>(FREEFORM_STAGES.map((s) => s.id));

/** Pick the stage set that matches the events seen for this message. */
function pickStages(p: PassProgress): { id: MultipassStage; label: string }[] {
  const seen = [p.current, ...p.completed];
  return seen.some((s) => s && FREEFORM_IDS.has(s))
    ? FREEFORM_STAGES
    : MULTIPASS_STAGES;
}

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

  const stages = pickStages(progress);
  const isFreeform = stages === FREEFORM_STAGES;
  const totalDone = progress.completed.length;
  const activeIdx = progress.current
    ? stages.findIndex((s) => s.id === progress.current)
    : -1;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -2 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -2 }}
        transition={{ duration: 0.18 }}
        className="min-w-0 max-w-full overflow-hidden rounded-md border border-border-subtle bg-surface-raised px-2.5 py-2"
      >
        <div className="flex min-w-0 items-center gap-2 text-[11px] font-mono text-fg-tertiary mb-1.5">
          <Loader2
            aria-hidden
            className="h-3 w-3 shrink-0 animate-spin text-accent"
          />
          <span className="shrink-0">{isFreeform ? "генерация" : "multipass"}</span>
          <span aria-hidden className="shrink-0">·</span>
          <span className="shrink-0 tabular-nums">
            {totalDone}/{stages.length}
          </span>
          {progress.current && (
            <>
              <span aria-hidden className="shrink-0">·</span>
              <span className="truncate text-fg-secondary">
                {stages.find((s) => s.id === progress.current)?.label ??
                  progress.current}
              </span>
            </>
          )}
        </div>
        {/* Slim 4-segment bar. Each segment is `flex-1` inside a `min-w-0`
            row → it shrinks to any column width, so the chat can never grow a
            horizontal scrollbar (the old labelled chips did). The active
            stage name lives in the header above, not inside the bar. */}
        <ol
          aria-label={`Прогресс генерации: ${totalDone} из ${stages.length}`}
          className="flex min-w-0 items-center gap-1"
        >
          {stages.map((stage, idx) => {
            const isDone = progress.completed.includes(stage.id);
            const isActive = idx === activeIdx;
            return (
              <li
                key={stage.id}
                className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-overlay"
              >
                <motion.span
                  aria-hidden
                  className={cn(
                    "block h-full rounded-full",
                    isDone ? "bg-accent" : "bg-accent/60",
                  )}
                  initial={false}
                  animate={{ width: isDone ? "100%" : isActive ? "50%" : "0%" }}
                  transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                />
              </li>
            );
          })}
        </ol>
      </motion.div>
    </AnimatePresence>
  );
}
