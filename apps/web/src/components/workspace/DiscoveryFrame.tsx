"use client";

import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { Sparkles, ArrowRight } from "lucide-react";
import { EASE_OUT } from "@/lib/motion";

/**
 * Onboarding-popup frame around a progressive-discovery question (NORTH STAR
 * pillar 2). The backend serves questions one at a time; on their own they read
 * as a bare chat row. This wraps the answer chips in a guided panel — a niche
 * banner («Давайте разберёмся под вашу идею: <ниша>»), a «Вопрос N из M»
 * counter with a progress bar, and an explicit «постройте сейчас» skip — so the
 * interview feels like a framed onboarding step (Stripe-style), never a trap.
 *
 * Counter + progress show only when the planned batch size is known
 * (`questionTotal` > 0 — the batch-discovery path). The skip is always offered.
 */
export function DiscoveryFrame({
  niche,
  questionIndex,
  questionTotal,
  onSkip,
  children,
}: {
  niche: string | null;
  questionIndex: number | null;
  questionTotal: number | null;
  /** Leave the interview and build now (submits an explicit build-now phrase). */
  onSkip: () => void;
  /** The answer affordance — the DiscoveryChips block. */
  children: ReactNode;
}) {
  const total = questionTotal ?? 0;
  const index = Math.min(Math.max(questionIndex ?? 1, 1), total || 1);
  const hasCounter = total > 0;
  const progress = hasCounter ? Math.round((index / total) * 100) : 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: EASE_OUT }}
      className="px-4 pb-3 pt-1"
    >
      <div className="overflow-hidden rounded-xl border border-accent/25 bg-surface-overlay/40 shadow-sm">
        {/* Progress bar — the onboarding's «сколько осталось» at a glance. */}
        {hasCounter && (
          <div
            className="h-0.5 w-full bg-border-default/50"
            role="progressbar"
            aria-valuenow={index}
            aria-valuemin={1}
            aria-valuemax={total}
            aria-label={`Вопрос ${index} из ${total}`}
          >
            <motion.div
              className="h-full bg-accent"
              initial={{ width: 0 }}
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.4, ease: EASE_OUT }}
            />
          </div>
        )}

        {/* Header: a compact lead + «Вопрос N из M», then the recognised niche on
            its own line as a badge — so it's never truncated in the narrow chat
            panel (the niche is the whole «угадали намерение» signal). */}
        <div className="space-y-1.5 px-4 py-2.5">
          <div className="flex items-start justify-between gap-2">
            <span className="flex min-w-0 items-center gap-1.5 text-xs font-medium text-fg-secondary">
              <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent" />
              Настраиваем под вашу идею
            </span>
            {hasCounter && (
              <span className="shrink-0 rounded-full bg-accent-subtle/60 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-accent">
                Вопрос {index} из {total}
              </span>
            )}
          </div>
          {niche ? (
            <span className="inline-flex items-center rounded-md bg-accent-subtle/60 px-2 py-0.5 text-xs font-semibold text-accent">
              {niche}
            </span>
          ) : null}
        </div>

        {/* Answer chips (single / multi / «Другое» inline). */}
        {children}

        {/* Explicit skip — never trap the user in the interview. */}
        <div className="border-t border-border-default/50 px-4 py-2">
          <button
            type="button"
            onClick={onSkip}
            className="group inline-flex items-center gap-1 text-xs font-medium text-fg-tertiary transition-colors hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 rounded"
          >
            Я готов — постройте сейчас
            <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
          </button>
        </div>
      </div>
    </motion.div>
  );
}
