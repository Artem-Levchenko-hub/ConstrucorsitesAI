"use client";

import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Award,
  Check,
  Image as ImageIcon,
  LayoutTemplate,
  Loader2,
  Package,
  Palette,
  PenTool,
  Sparkles,
  Type,
  type LucideIcon,
} from "lucide-react";
import type { MultipassStage, PassProgress, StreamBrief } from "@/lib/api/types";
import { briefSectionPlan, briefSwatches } from "@/lib/brief-swatches";
import { cn } from "@/lib/utils";

/**
 * Cinematic build-stage timeline for one streaming assistant message — the
 * "watching something intelligent build my product" moment. Reads
 * `["passes", projectId, messageId]` from React Query cache (populated by
 * `usePromptStream` on `llm.pass` events). Renders nothing until the first
 * event arrives, so single-shot generations stay clean; auto-removed on
 * `llm.done` / `llm.error` / cancel.
 *
 * Two 4-stage pipelines feed the SAME llm.pass channel — freeform
 * (Замысел → Вёрстка → Картинки → Проверка) and multipass (Структура →
 * Контент → Визуал → Сборка); the set is picked per-message from the events
 * seen. Each stage is a node on a vertical timeline: pending (dim) → active
 * (glowing, with a one-line narration of intent + the working model) → done
 * (accent, locked with a check). Under `prefers-reduced-motion` it degrades to
 * a slim 4-segment bar with no looping animation.
 */
type StageMeta = { label: string; narration: string; Icon: LucideIcon };

const STAGE_META: Record<MultipassStage, StageMeta> = {
  // Freeform pipeline (art_director_writer.py → messages.py).
  art_director: {
    label: "Замысел",
    narration: "Продумываю композицию, характер и палитру",
    Icon: Sparkles,
  },
  writer: {
    label: "Вёрстка",
    narration: "Собираю страницу секция за секцией",
    Icon: PenTool,
  },
  images: {
    label: "Картинки",
    narration: "Подбираю и вписываю изображения",
    Icon: ImageIcon,
  },
  judge: {
    label: "Проверка",
    narration: "Сужу как жюри Awwwards — довожу до премиума",
    Icon: Award,
  },
  // Multipass pipeline (multipass_generator.py).
  skeleton: {
    label: "Структура",
    narration: "Расставляю каркас страницы",
    Icon: LayoutTemplate,
  },
  content: {
    label: "Контент",
    narration: "Наполняю смыслом и копирайтом",
    Icon: Type,
  },
  visual: {
    label: "Визуал",
    narration: "Навожу цвет, ритм и красоту",
    Icon: Palette,
  },
  assembly: {
    label: "Сборка",
    narration: "Собираю всё воедино",
    Icon: Package,
  },
};

const FREEFORM_IDS: MultipassStage[] = ["art_director", "writer", "images", "judge"];
const MULTIPASS_IDS: MultipassStage[] = ["skeleton", "content", "visual", "assembly"];
const FREEFORM_SET = new Set<MultipassStage>(FREEFORM_IDS);

/** Pick the stage set that matches the events seen for this message. */
function pickStages(p: PassProgress): MultipassStage[] {
  const seen = [p.current, ...p.completed];
  return seen.some((s) => s && FREEFORM_SET.has(s)) ? FREEFORM_IDS : MULTIPASS_IDS;
}

/** Compact a model id for the header line (drop the provider prefix). */
function shortModel(model: string): string {
  return model.split("/").pop() ?? model;
}

/**
 * V3.4 — SURFACING the art-director brief as visible "AI рисует" cards. The
 * brief arrives BEFORE the first writer-HTML token (V3.10a), so the user sees
 * THIS build's chosen palette (real colour chips carrying the brief's own HEX)
 * and its section plan while the page is still being written. Renders only with
 * ≥3 brief-derived swatches (the V3.4 gate); silent otherwise. Under
 * `prefers-reduced-motion` the entrance animation is skipped.
 */
function BriefReveal({
  brief,
  reduced,
}: {
  brief: StreamBrief | null | undefined;
  reduced: boolean | null;
}) {
  const swatches = briefSwatches(brief);
  if (swatches.length < 3) return null;
  const sections = briefSectionPlan(brief);
  return (
    <motion.div
      role="group"
      aria-label="Дизайн-бриф: палитра и секции"
      initial={reduced ? false : { opacity: 0, y: -2 }}
      animate={reduced ? undefined : { opacity: 1, y: 0 }}
      transition={{ duration: 0.22 }}
      className="mb-2.5 border-b border-border-subtle pb-2.5"
    >
      <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-fg-tertiary">
        <Palette aria-hidden className="h-3 w-3 text-accent" />
        Палитра проекта
      </div>
      <ul className="flex flex-wrap items-center gap-1.5">
        {swatches.map((s) => (
          <li
            key={s.hex}
            className="flex items-center gap-1.5 rounded-md border border-border-subtle bg-surface-overlay/60 py-1 pl-1 pr-2"
          >
            <span
              aria-hidden
              className="h-4 w-4 shrink-0 rounded border border-black/10"
              style={{ backgroundColor: s.hex }}
            />
            <span className="font-mono text-[10px] leading-none text-fg-secondary">
              <span className="text-fg-tertiary">{s.label}</span>{" "}
              <span className="tabular-nums">{s.hex}</span>
            </span>
          </li>
        ))}
      </ul>
      {sections.length > 0 && (
        <div className="mt-1.5 flex min-w-0 items-start gap-1.5">
          <LayoutTemplate
            aria-hidden
            className="mt-0.5 h-3 w-3 shrink-0 text-fg-tertiary"
          />
          <p className="min-w-0 text-[11px] leading-4 text-fg-tertiary">
            {sections.join(" · ")}
          </p>
        </div>
      )}
    </motion.div>
  );
}

export function PassProgressBar({
  projectId,
  messageId,
}: {
  projectId: string;
  messageId: string;
}) {
  const reduced = useReducedMotion();
  // `useQuery` with `enabled: false` just subscribes to whatever the streaming
  // hook writes via `setQueryData`; re-renders fire when the cache changes.
  const { data: progress } = useQuery<PassProgress | undefined>({
    queryKey: ["passes", projectId, messageId],
    queryFn: () => undefined,
    enabled: false,
    staleTime: Infinity,
  });
  // V3.4 — the art-director brief for THIS message (same client-only cache as
  // StreamingPreviewFrame, populated by usePromptStream on `omnia:brief`). Drives
  // the brief-reveal swatches; arrives before the first writer-HTML token.
  const { data: streamBrief } = useQuery<StreamBrief | null>({
    queryKey: ["stream-brief", projectId, messageId],
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });

  if (!progress) return null;

  const stages = pickStages(progress);
  const totalDone = progress.completed.length;
  const activeIdx = progress.current
    ? stages.indexOf(progress.current)
    : -1;

  // Reduced-motion / accessibility fallback: the original slim, non-looping bar.
  if (reduced) {
    return (
      <div className="min-w-0 max-w-full overflow-hidden rounded-md border border-border-subtle bg-surface-raised px-2.5 py-2">
        <BriefReveal brief={streamBrief} reduced={reduced} />
        <div className="mb-1.5 flex min-w-0 items-center gap-2 font-mono text-[11px] text-fg-tertiary">
          <span className="shrink-0">генерация</span>
          <span className="shrink-0 tabular-nums">
            {totalDone}/{stages.length}
          </span>
          {progress.current && (
            <span className="truncate text-fg-secondary">
              {STAGE_META[progress.current]?.label ?? progress.current}
            </span>
          )}
        </div>
        <ol className="flex min-w-0 items-center gap-1">
          {stages.map((id, idx) => {
            const done = progress.completed.includes(id);
            const active = idx === activeIdx;
            return (
              <li
                key={id}
                className={cn(
                  "h-1 min-w-0 flex-1 rounded-full",
                  done || active ? "bg-accent" : "bg-surface-overlay",
                )}
              />
            );
          })}
        </ol>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -2 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="min-w-0 max-w-full overflow-hidden rounded-lg border border-border-subtle bg-surface-raised px-3 py-2.5"
    >
      <BriefReveal brief={streamBrief} reduced={reduced} />
      <div className="mb-2 flex min-w-0 items-center gap-2 font-mono text-[11px] text-fg-tertiary">
        <Sparkles aria-hidden className="h-3 w-3 shrink-0 text-accent" />
        <span className="shrink-0">Omnia собирает</span>
        <span aria-hidden className="shrink-0">·</span>
        <span className="shrink-0 tabular-nums">
          {totalDone}/{stages.length}
        </span>
      </div>

      <ol
        aria-label={`Этап генерации: ${totalDone} из ${stages.length}`}
        className="relative min-w-0 space-y-0"
      >
        {stages.map((id, idx) => {
          const meta = STAGE_META[id];
          const done = progress.completed.includes(id);
          const active = idx === activeIdx;
          const pending = !done && !active;
          const isLast = idx === stages.length - 1;
          const Icon = meta?.Icon ?? Sparkles;
          return (
            <li key={id} className="relative flex min-w-0 gap-2.5 pb-2 last:pb-0">
              {/* Node + connector column */}
              <div className="relative flex w-5 shrink-0 flex-col items-center">
                <motion.div
                  className={cn(
                    "z-10 flex h-5 w-5 items-center justify-center rounded-full border",
                    done && "border-accent bg-accent text-accent-fg",
                    active && "border-accent bg-accent/10 text-accent",
                    pending && "border-border-subtle bg-surface-overlay text-fg-tertiary",
                  )}
                  animate={
                    active
                      ? {
                          boxShadow: [
                            "0 0 0 0 rgba(110,91,232,0.0)",
                            "0 0 0 4px rgba(110,91,232,0.18)",
                            "0 0 0 0 rgba(110,91,232,0.0)",
                          ],
                        }
                      : { boxShadow: "0 0 0 0 rgba(110,91,232,0)" }
                  }
                  transition={
                    active
                      ? { duration: 1.6, repeat: Infinity, ease: "easeInOut" }
                      : { duration: 0.2 }
                  }
                >
                  {done ? (
                    <motion.span
                      initial={{ scale: 0 }}
                      animate={{ scale: 1 }}
                      transition={{ type: "spring", stiffness: 500, damping: 22 }}
                    >
                      <Check className="h-3 w-3" />
                    </motion.span>
                  ) : active ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Icon className="h-2.5 w-2.5" />
                  )}
                </motion.div>
                {!isLast && (
                  <span
                    aria-hidden
                    className={cn(
                      "absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2",
                      done ? "bg-accent/50" : "bg-border-subtle",
                    )}
                  />
                )}
              </div>

              {/* Label + narration column */}
              <div className="min-w-0 flex-1 pt-0.5">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span
                    className={cn(
                      "truncate text-xs font-medium",
                      done && "text-fg-secondary",
                      active && "text-fg-primary",
                      pending && "text-fg-tertiary",
                    )}
                  >
                    {meta?.label ?? id}
                  </span>
                  {active && progress.currentModel && (
                    <span
                      className="min-w-0 truncate font-mono text-[10px] text-fg-tertiary"
                      title={progress.currentModel}
                    >
                      · {shortModel(progress.currentModel)}
                    </span>
                  )}
                </div>
                <AnimatePresence mode="wait">
                  {active && meta?.narration && (
                    <motion.div
                      key={id}
                      initial={{ opacity: 0, y: -2, height: 0 }}
                      animate={{ opacity: 1, y: 0, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.22 }}
                      className="overflow-hidden text-[11px] leading-4 text-fg-tertiary"
                    >
                      {meta.narration}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </li>
          );
        })}
      </ol>
    </motion.div>
  );
}
