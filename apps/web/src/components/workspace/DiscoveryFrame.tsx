"use client";

import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { Sparkles, ArrowRight, Check } from "lucide-react";
import type { DesignPreview } from "@/lib/api/types";
import { EASE_OUT } from "@/lib/motion";

// Canonical section keys → short RU labels for the live-preview chips. Mirrors
// the api section taxonomy (chip_pixel_gate._SECTION_KEYWORDS); an unknown key
// falls back to itself so a new section never renders blank.
const SECTION_LABELS: Record<string, string> = {
  catalog: "Каталог",
  testimonials: "Отзывы",
  contacts: "Контакты",
  pricing: "Цены",
  features: "Возможности",
  faq: "FAQ",
  about: "О нас",
  gallery: "Галерея",
};

// Canonical tone tokens → RU labels (mirrors api chip_pixel_gate._TONE_ALIASES).
const TONE_LABELS: Record<string, string> = {
  premium: "Премиум",
  friendly: "Дружелюбный",
  playful: "Игривый",
  minimal: "Минимализм",
  corporate: "Деловой",
};

/**
 * LIVE design-preview mini-hero (NORTH STAR pillars 2×3 — «покажи ЧТО построим»).
 * Paints a tiny browser-card from the design tokens the answers steer toward and
 * morphs it on every turn: the canvas flips dark/light with the theme answer, the
 * accent bar + CTA recolour to the picked colour family, a tone eyebrow appears,
 * and the chosen sections stack in as chips. framer-motion interpolates the
 * colours so each answer reads as a live transformation, not a static echo — the
 * onboarding's hypnosis moment before the build even starts.
 */
function DesignPreviewCard({ preview }: { preview: DesignPreview }) {
  const dark = preview.dark_mode === true;
  // Accent falls back to a muted slate until a colour family is picked, so the
  // first colour answer reads as a visible pop (grey → brand) rather than a
  // no-op. Readable on both canvases.
  const accent = preview.accent ?? "#94A3B8";
  const hasAccent = Boolean(preview.accent);
  const canvas = dark ? "#0E1014" : "#FFFFFF";
  const fg = dark ? "#E7E9EE" : "#0F172A";
  const bar = dark ? "rgba(231,233,238,0.16)" : "rgba(15,23,42,0.10)";
  const chrome = dark ? "rgba(255,255,255,0.05)" : "rgba(15,23,42,0.035)";
  const border = dark ? "rgba(255,255,255,0.08)" : "rgba(15,23,42,0.08)";

  const sections = (preview.sections ?? []).filter(Boolean);
  const shownSections = sections.slice(0, 4);
  const extraSections = sections.length - shownSections.length;
  const toneLabel = preview.tone ? TONE_LABELS[preview.tone] ?? preview.tone : null;

  return (
    <div className="px-4 pb-1.5 pt-0.5">
      <div className="mb-1 flex items-center gap-1.5">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
        </span>
        <span className="text-[11px] font-medium text-fg-tertiary">
          Превью дизайна — обновляется на лету
        </span>
      </div>

      <motion.div
        className="overflow-hidden rounded-lg shadow-sm"
        style={{ border: `1px solid ${border}` }}
        animate={{ backgroundColor: canvas }}
        transition={{ duration: 0.4, ease: EASE_OUT }}
      >
        {/* Faux browser chrome — frames it as «вот ваш сайт». */}
        <motion.div
          className="flex items-center gap-1 px-2.5 py-1.5"
          animate={{ backgroundColor: chrome }}
          transition={{ duration: 0.4, ease: EASE_OUT }}
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: bar }} />
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: bar }} />
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: bar }} />
          <motion.span
            className="ml-1.5 h-2 flex-1 rounded-full"
            animate={{ backgroundColor: hasAccent ? `${accent}33` : bar }}
            transition={{ duration: 0.4, ease: EASE_OUT }}
          />
        </motion.div>

        {/* Hero body — eyebrow tone pill, headline bars, accent CTA. */}
        <div className="space-y-2 px-3.5 py-3">
          {toneLabel ? (
            <motion.span
              key={`tone-${preview.tone}`}
              initial={{ opacity: 0, y: -2 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25, ease: EASE_OUT }}
              className="inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
              style={{ background: `${accent}22`, color: accent }}
            >
              {toneLabel}
            </motion.span>
          ) : null}

          <div className="space-y-1.5">
            <motion.div
              className="h-2.5 w-3/4 rounded-full"
              animate={{ backgroundColor: hasAccent ? accent : fg }}
              transition={{ duration: 0.4, ease: EASE_OUT }}
              style={{ opacity: hasAccent ? 0.92 : 0.85 }}
            />
            <div className="h-2 w-1/2 rounded-full" style={{ background: bar }} />
          </div>

          <motion.div
            className="flex h-5 w-20 items-center justify-center rounded-md"
            animate={{ backgroundColor: accent }}
            transition={{ duration: 0.4, ease: EASE_OUT }}
          >
            <span className="h-1.5 w-9 rounded-full bg-white/85" />
          </motion.div>

          {/* Chosen sections stack in as the user picks them. */}
          {shownSections.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1 pt-0.5">
              {shownSections.map((s, i) => (
                <motion.span
                  key={s}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.22, ease: EASE_OUT, delay: i * 0.04 }}
                  className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium"
                  style={{ background: bar, color: fg }}
                >
                  {SECTION_LABELS[s] ?? s}
                </motion.span>
              ))}
              {extraSections > 0 ? (
                <span
                  className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium"
                  style={{ background: bar, color: fg }}
                >
                  +{extraSections}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
      </motion.div>
    </div>
  );
}

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
 *
 * LIVE causality (pillar 2 — «вас услышали»): `recap` echoes the answers
 * gathered so far as «✓ …» chips, and `niche` is re-inferred on the cumulative
 * answers server-side, so the panel visibly reacts turn-by-turn instead of
 * reading as an inert quiz.
 */
export function DiscoveryFrame({
  niche,
  questionIndex,
  questionTotal,
  recap,
  designPreview,
  onSkip,
  children,
}: {
  niche: string | null;
  questionIndex: number | null;
  questionTotal: number | null;
  /** Short «✓ …» chips of the answers gathered so far (newest last). */
  recap?: string[] | null;
  /** Resolved design tokens for the live-preview mini-hero (pillars 2×3). */
  designPreview?: DesignPreview | null;
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
            <motion.span
              key={niche}
              initial={{ opacity: 0, scale: 0.92 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.25, ease: EASE_OUT }}
              className="inline-flex items-center rounded-md bg-accent-subtle/60 px-2 py-0.5 text-xs font-semibold text-accent"
            >
              {niche}
            </motion.span>
          ) : null}

          {/* Answer-recap (pillar 2 — «вас услышали»): echo what the user has said
              so far as «✓ …» chips, animated in, so the interview visibly reacts to
              every answer instead of feeling like a one-way quiz. */}
          {recap && recap.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1 pt-0.5">
              <span className="text-[11px] font-medium text-fg-tertiary">Учли:</span>
              {recap.map((item, i) => (
                <motion.span
                  key={`${item}-${i}`}
                  initial={{ opacity: 0, y: -3 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, ease: EASE_OUT, delay: i * 0.04 }}
                  className="inline-flex items-center gap-1 rounded-md bg-surface-overlay/70 px-1.5 py-0.5 text-[11px] font-medium text-fg-secondary"
                >
                  <Check className="h-2.5 w-2.5 shrink-0 text-accent" />
                  {item}
                </motion.span>
              ))}
            </div>
          ) : null}
        </div>

        {/* LIVE design-preview mini-hero (pillars 2×3 — «покажи ЧТО построим»):
            appears once any design axis is decided and morphs every turn. */}
        {designPreview ? <DesignPreviewCard preview={designPreview} /> : null}

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
